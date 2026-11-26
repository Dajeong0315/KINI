import json
from uuid import uuid4

import pytest

from app.main import app
from app.services.local_identity import create_account, login
from app.services.recommendation_repository import open_db
from fridge_vision.kini_inventory_client import KiniAPIError, KiniInventoryClient


class LocalAPI:
    def __init__(self, client):
        self.client = client

    def __call__(self, method, url, *, headers, json=None, timeout=None):
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


def client_for(tmp_path, local):
    return KiniInventoryClient("http://local-a", "local-test-token", tmp_path / "kini_queue.jsonl", request=local)


def test_detection_is_pending_then_explicit_confirmation_changes_stock(tmp_path, local_api):
    http, token = local_api
    api = KiniInventoryClient("http://local-a", token, tmp_path / "queue.jsonl",
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
    api = KiniInventoryClient("http://local-a", token, tmp_path / "queue.jsonl", request=LocalAPI(http))
    event = api.record_detection(client_event_id=str(uuid4()), detected_label="Unknown")
    assert event["status"] == "pending_confirmation"
    assert api.reject(event["event_id"])["status"] == "rejected"
    assert http.get("/api/data/inventory", headers={"Authorization": "Bearer " + token}).json["inventory_version"] == 0


def test_connection_failure_queues_minimum_event_without_token(tmp_path):
    def fail(*args, **kwargs):
        import requests
        raise requests.ConnectionError("offline")
    queue = tmp_path / "kini_queue.jsonl"
    api = KiniInventoryClient("http://local-a", "secret-token", queue, request=fail)
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

    api = KiniInventoryClient("http://local-a", "token", tmp_path / "queue.jsonl",
                              request=lambda *args, **kwargs: Response())
    with pytest.raises(KiniAPIError) as error:
        api.record_detection(client_event_id=str(uuid4()), detected_label="두부")
    assert error.value.status == status
    assert not (tmp_path / "queue.jsonl").exists()
