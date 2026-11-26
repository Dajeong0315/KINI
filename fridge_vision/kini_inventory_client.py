"""Small, confirmation-first client for KINI's local A inventory API.

Detection is never treated as stock. ``record_detection`` only creates a
pending event. A human or another trusted UI must call ``confirm`` with the
canonical ingredient, measured weight and (for stock-out) the inventory lot.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable
from uuid import UUID

import requests

LOG = logging.getLogger(__name__)
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


class KiniAPIError(RuntimeError):
    def __init__(self, status: int | None, message: str):
        self.status = status
        super().__init__(message)


class KiniInventoryClient:
    """A API client with a secret-free, minimal JSONL retry queue."""

    def __init__(self, base_url: str, token: str, queue_path: str | Path,
                 *, timeout: float = 5.0,
                 request: Callable[..., Any] | None = None) -> None:
        if not base_url.startswith("http://"):
            raise ValueError("KINI_A_API_URL must use the local http contract")
        if not token:
            raise ValueError("KINI_A_API_TOKEN is required")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.queue_path = Path(queue_path)
        self.timeout = timeout
        self._request = request or requests.request

    def _call(self, method: str, path: str, payload: dict | None = None) -> dict:
        try:
            response = self._request(
                method, self.base_url + path,
                headers={"Authorization": f"Bearer {self.token}",
                         "Content-Type": "application/json"},
                json=payload, timeout=self.timeout)
        except requests.RequestException as exc:
            raise KiniAPIError(None, "KINI A API connection failed") from exc
        if response.status_code >= 300:
            try:
                body = response.json()
                message = str(body.get("error", body))[:200]
            except ValueError:
                message = "A API request failed"
            raise KiniAPIError(response.status_code, message)
        if hasattr(response, "content"):
            return response.json() if response.content else {}
        body = response.get_json(silent=True)
        return body or {}

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
        self.queue_path.write_text("".join(json.dumps(x, ensure_ascii=False, sort_keys=True) + "\n" for x in items), encoding="utf-8")

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

    def retry_pending(self) -> list[dict]:
        """Retry queued ingestion only; confirmation is always explicit."""
        queued = self._load_queue()
        completed: list[dict] = []
        remaining: list[dict] = []
        for item in queued:
            try:
                completed.append(self._call("POST", "/api/data/inventory/events", item["payload"]))
            except KiniAPIError as exc:
                if exc.status is None or exc.status in RETRYABLE_STATUS:
                    remaining.append(item)
                else:
                    LOG.warning("A API rejected queued event with status %s", exc.status)
        self._save_queue(remaining)
        return completed
