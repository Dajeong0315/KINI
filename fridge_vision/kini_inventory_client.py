"""Small, confirmation-first client for KINI's local A inventory API.

Detection is never treated as stock. ``record_detection`` only creates a
pending event. A human or another trusted UI must call ``confirm`` with the
canonical ingredient, measured weight and (for stock-out) the inventory lot.
"""
from __future__ import annotations

import json
from ipaddress import ip_address
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Callable
from urllib.parse import urlsplit
from uuid import UUID

import requests

RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


class KiniAPIError(RuntimeError):
    def __init__(self, status: int | None, message: str):
        self.status = status
        self.reauthentication_required = status == 401
        self.manual_review_required = status is not None and status != 401 and status not in RETRYABLE_STATUS
        super().__init__(message)


def _local_origin(value: str) -> str:
    try:
        url = urlsplit(value)
        host, port = url.hostname, url.port
        if (url.scheme != "http" or not host or url.username is not None
                or url.path not in ("", "/") or any(c in value for c in "\\?#%")
                or any(c.isspace() or ord(c) < 32 for c in value)
                or not 0 < (80 if port is None else port) <= 65535):
            raise ValueError
        if host != "localhost" and not ip_address(host).is_loopback:
            raise ValueError
        authority = f"[{host}]" if ":" in host else host
        authority += f":{port}" if port is not None else ""
        if url.netloc.lower() != authority:
            raise ValueError
        return f"http://{authority}"
    except ValueError:
        raise ValueError("KINI_A_API_URL must be a loopback HTTP origin") from None


def _local_request(*args, **kwargs):
    with requests.Session() as session:
        session.trust_env = False
        return session.request(*args, **kwargs)


class KiniInventoryClient:
    """A API client with a secret-free, minimal JSONL retry queue."""

    def __init__(self, base_url: str, token: str, queue_path: str | Path,
                 *, timeout: float = 5.0,
                 request: Callable[..., Any] | None = None) -> None:
        self.base_url = _local_origin(base_url)
        if not token:
            raise ValueError("KINI_A_API_TOKEN is required")
        self.token = token
        self.queue_path = Path(queue_path)
        self.timeout = timeout
        self._request = request or _local_request

    def _call(self, method: str, path: str, payload: dict | None = None) -> dict:
        try:
            response = self._request(
                method, self.base_url + path,
                headers={"Authorization": f"Bearer {self.token}",
                         "Content-Type": "application/json"},
                json=payload, timeout=self.timeout, allow_redirects=False)
        except requests.RequestException as exc:
            raise KiniAPIError(None, "KINI A API connection failed") from exc
        if response.status_code >= 300:
            try:
                body = response.json()
                message = str(body.get("error", body) if isinstance(body, dict) else body)[:200]
            except ValueError:
                message = "A API request failed"
            raise KiniAPIError(response.status_code, message)
        try:
            body = response.json() if hasattr(response, "content") else response.get_json()
        except ValueError as exc:
            raise KiniAPIError(None, "A API response was invalid") from exc
        if not isinstance(body, dict):
            raise KiniAPIError(None, "A API response was invalid")
        return body

    def _append_queue(self, item: dict) -> None:
        self.queue_path.parent.mkdir(parents=True, exist_ok=True)
        with self.queue_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")

    def _load_queue(self) -> list[dict]:
        if not self.queue_path.exists():
            return []
        return [json.loads(line) for line in self.queue_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _save_queue(self, items: list[dict]) -> None:
        if not items:
            if self.queue_path.exists():
                self.queue_path.unlink()
            return
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.queue_path.parent,
                                 delete=False) as stream:
            temporary = Path(stream.name)
            try:
                for item in items:
                    stream.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
            except BaseException:
                stream.close()
                temporary.unlink()
                raise
        try:
            temporary.replace(self.queue_path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _validate_event_id(value: str) -> str:
        return str(UUID(value))

    def record_detection(self, *, client_event_id: str, detected_label: str,
                         source: str = "yolo", action: str = "stock_in",
                         confidence: float | None = None) -> dict:
        """Send one detection as pending; retry the same event ID on failure."""
        payload = {"client_event_id": self._validate_event_id(client_event_id),
                   "source": source, "action": action,
                   "detected_label": detected_label}
        if confidence is not None:
            payload["confidence"] = confidence
        try:
            return self._call("POST", "/api/data/inventory/events", payload)
        except KiniAPIError as exc:
            if exc.status is None or exc.status in RETRYABLE_STATUS:
                self._append_queue({"op": "ingest", "payload": payload})
            raise

    def confirm(self, event_id: int, *, ingredient_id: int, quantity_g: float,
                inventory_item_id: int | None = None,
                expiry_date: str | None = None) -> dict:
        payload = {"ingredient_id": ingredient_id, "quantity_g": quantity_g}
        if inventory_item_id is not None:
            payload["inventory_item_id"] = inventory_item_id
        if expiry_date is not None:
            payload["expiry_date"] = expiry_date
        return self._call("POST", f"/api/data/inventory/events/{event_id}/confirm", payload)

    def reject(self, event_id: int) -> dict:
        return self._call("POST", f"/api/data/inventory/events/{event_id}/reject")

    def retry_pending(self, *, include_manual: bool = False) -> list[dict]:
        """Retry queued ingestion. Explicitly opt in after reviewing a blocked item."""
        queued = self._load_queue()
        completed: list[dict] = []
        remaining: list[dict] = []
        for index, item in enumerate(queued):
            if "manual_review_status" in item and not include_manual:
                remaining.append(item)
                continue
            try:
                completed.append(self._call("POST", "/api/data/inventory/events", item["payload"]))
            except KiniAPIError as exc:
                if exc.manual_review_required:
                    item = {**item, "manual_review_status": exc.status}
                remaining.append(item)
                if exc.reauthentication_required or exc.manual_review_required:
                    self._save_queue(remaining + queued[index + 1:])
                    raise
        self._save_queue(remaining)
        return completed
