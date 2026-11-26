import json
from uuid import uuid4

import pytest
import requests

from app.main import app
from app.services.local_identity import create_account, login
from app.services.recommendation_repository import open_db
from fridge_vision.kini_inventory_client import KiniAPIError, KiniInventoryClient


class LocalAPI:
    def __init__(self, client):
        self.client = client

    def __call__(self, method, url, *, headers, json=None, timeout=None, allow_redirects):
        assert allow_redirects is False
        path = url.split("/api", 1)[1]
        return self.client.open(path="/api" + path, method=method, headers=headers, json=json)


@pytest.fixture
def local_api(tmp_path, monkeypatch):
    path = str(tmp_path / "a.sqlite3")
    monkeypatch.setenv("KINI_DB_PATH", path)
    db = open_db(path)
    create_account(db, "owner", "test-password-123", 1)
    token = login(db, "owner", "test-password-123", 1)
    db.close()
    return app.test_client(), token


def test_detection_is_pending_then_explicit_confirmation_changes_stock(tmp_path, local_api):
    http, token = local_api
    api = KiniInventoryClient("http://localhost", token, tmp_path / "queue.jsonl",
                              request=LocalAPI(http))
    event = api.record_detection(client_event_id=str(uuid4()), detected_label="두부", confidence=.91)
    assert event["requires_confirmation"]
    before = http.get("/api/data/inventory", headers={"Authorization": "Bearer " + token}).json["inventory_version"]
    assert before == 0
    applied = api.confirm(event["event_id"], ingredient_id=4, quantity_g=250)
    assert applied["status"] == "applied"
    assert api.confirm(event["event_id"], ingredient_id=4, quantity_g=250) == applied


def test_reject_keeps_inventory_unchanged_and_unknown_stays_pending(tmp_path, local_api):
    http, token = local_api
    api = KiniInventoryClient("http://localhost", token, tmp_path / "queue.jsonl", request=LocalAPI(http))
    event = api.record_detection(client_event_id=str(uuid4()), detected_label="Unknown")
    assert event["status"] == "pending_confirmation"
    assert api.reject(event["event_id"])["status"] == "rejected"
    assert http.get("/api/data/inventory", headers={"Authorization": "Bearer " + token}).json["inventory_version"] == 0


def test_connection_failure_queues_minimum_event_without_token(tmp_path):
    def fail(*args, **kwargs):
        import requests
        raise requests.ConnectionError("offline")
    queue = tmp_path / "kini_queue.jsonl"
    api = KiniInventoryClient("http://localhost", "secret-token", queue, request=fail)
    with pytest.raises(KiniAPIError):
        api.record_detection(client_event_id=str(uuid4()), detected_label="두부")
    raw = queue.read_text(encoding="utf-8")
    assert "secret-token" not in raw and "Authorization" not in raw
    assert set(json.loads(raw)["payload"]) == {"action", "client_event_id", "detected_label", "source"}


@pytest.mark.parametrize("status", [401, 403, 409])
def test_auth_and_conflict_errors_are_not_queued(tmp_path, status):
    class Response:
        status_code = status
        content = b'{"error":"rejected"}'
        def json(self):
            return {"error": "rejected"}

    api = KiniInventoryClient("http://localhost", "token", tmp_path / "queue.jsonl",
                              request=lambda *args, **kwargs: Response())
    with pytest.raises(KiniAPIError) as error:
        api.record_detection(client_event_id=str(uuid4()), detected_label="두부")
    assert error.value.status == status
    assert not (tmp_path / "queue.jsonl").exists()


@pytest.mark.parametrize("url", [
    "http://example.invalid", "http://192.168.1.2", "https://localhost",
    "http://name@localhost", "http://localhost@other.invalid",
    "http://localhost/api", "http://localhost?x", "http://localhost#x",
    "http://127.1", "http://0.0.0.0", "http://[::]:5000",
    "http://localhost:99999", "http://localhost:", "http://[::1]bad:80",
    "http://localhost\\bad", "http://local\nhost", "http://%6cocalhost",
])
def test_rejects_invalid_origins_before_token_or_network(tmp_path, url):
    with pytest.raises(ValueError, match="loopback HTTP origin"):
        KiniInventoryClient(url, "", tmp_path / "queue", request=None)


@pytest.mark.parametrize("url", ["http://localhost", "http://127.0.0.2:5000/", "http://[::1]:5000"])
def test_accepts_loopback_origins_without_request(tmp_path, url):
    with pytest.raises(ValueError, match="TOKEN is required"):
        KiniInventoryClient(url, "", tmp_path / "queue", request=None)


def _response(status, body=None):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(body or {}).encode()
    return response


def test_redirect_is_rejected_without_following(tmp_path, monkeypatch):
    calls = []

    def send(adapter, request, **kwargs):
        assert not kwargs["proxies"]
        calls.append(request.url)
        response = _response(307)
        response.headers["Location"] = "http://example.invalid/"
        response.request = request
        response.url = request.url
        return response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    api = KiniInventoryClient("http://localhost", "synthetic", tmp_path / "queue")
    with pytest.raises(KiniAPIError) as error:
        api.record_detection(client_event_id=str(uuid4()), detected_label="synthetic")
    assert error.value.status == 307
    assert calls == ["http://localhost/api/data/inventory/events"]


@pytest.mark.parametrize("status", [401, 403, 409])
def test_retry_preserves_failure_and_tail_and_removes_only_success(tmp_path, status):
    event_ids = [str(uuid4()) for _ in range(3)]
    queue = tmp_path / "queue"
    mode = "offline"
    calls = []

    def send(*args, json, **kwargs):
        event_id = json["client_event_id"]
        calls.append(event_id)
        if mode == "offline":
            raise requests.ConnectionError()
        if mode == "failure" and event_id == event_ids[1]:
            return _response(status)
        return _response(200, {"client_event_id": event_id})

    api = KiniInventoryClient("http://localhost", "synthetic", queue, request=send)
    for event_id in event_ids:
        with pytest.raises(KiniAPIError):
            api.record_detection(client_event_id=event_id, detected_label="synthetic")
    mode = "failure"
    calls.clear()
    with pytest.raises(KiniAPIError) as error:
        api.retry_pending()
    assert error.value.status == status
    assert error.value.reauthentication_required == (status == 401)
    assert error.value.manual_review_required == (status != 401)
    assert calls == event_ids[:2]
    saved = [json.loads(line) for line in queue.read_text().splitlines()]
    assert [item["payload"]["client_event_id"] for item in saved] == event_ids[1:]
    assert saved[0].get("manual_review_status") == (None if status == 401 else status)
    mode = "success"
    fresh_client = KiniInventoryClient("http://localhost", "replacement", queue, request=send)
    assert [item["client_event_id"] for item in fresh_client.retry_pending()] == event_ids[1 if status == 401 else 2:]
    if status != 401:
        assert queue.exists()
        assert fresh_client.retry_pending(include_manual=True) == [{"client_event_id": event_ids[1]}]
    assert not queue.exists()
