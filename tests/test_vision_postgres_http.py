"""Synthetic Vision events over real loopback HTTP against disposable PostgreSQL."""
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from secrets import token_urlsafe
from threading import Barrier, Thread
from uuid import uuid4

import pytest
import requests
from werkzeug.serving import make_server

from app.services import local_identity
from app.services.recommendation_repository import open_db
from fridge_vision.kini_inventory_client import KiniAPIError, KiniInventoryClient


pytestmark = pytest.mark.skipif(
    os.getenv("KINI_DB_BACKEND", "").lower() != "postgres" or not os.getenv("KINI_DATABASE_URL"),
    reason="requires an explicitly configured isolated PostgreSQL database",
)


@contextmanager
def _database():
    with closing(open_db()) as db, db:
        yield db


@pytest.fixture
def server(monkeypatch):
    from app.main import app

    password = token_urlsafe(24)
    with _database() as db:
        assert db.execute("SELECT version()").fetchone()[0].startswith("PostgreSQL ")
        assert db.execute("SELECT version FROM schema_migrations WHERE version='001_step1_postgresql'").fetchone()[0] == "001_step1_postgresql"
        db.execute("TRUNCATE inventory_event_changes, inventory_events, inventory_items, auth_sessions, account_households, local_accounts, ingredients, households CASCADE")
        db.execute("INSERT INTO households(id,name) VALUES (101,'synthetic A'),(202,'synthetic B')")
        db.execute("INSERT INTO ingredients(id,name) VALUES (901,'synthetic ingredient')")
        local_identity.create_account(db, "owner", password, 101)
        viewer = local_identity.create_account(db, "viewer", password, 101)
        local_identity.create_account(db, "other", password, 202)
        db.execute("UPDATE account_households SET role='viewer' WHERE account_id=?", (viewer,))

    monkeypatch.setitem(app.config, "PROPAGATE_EXCEPTIONS", False)
    httpd = make_server("127.0.0.1", 0, app, threaded=True)
    thread = Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_port}"
    try:
        with requests.Session() as http:
            http.trust_env = False

            def login(name="owner", household=101):
                reply = http.post(base + "/api/auth/login", timeout=5, json={
                    "login_name": name, "password": password, "household_id": household,
                })
                assert reply.status_code == 200
                return reply.json()["access_token"]

            yield base, http, login
    finally:
        httpd.shutdown()
        thread.join(timeout=5)
        httpd.server_close()


def _detection(api, *, label="synthetic ingredient", action="stock_in", event_id=None):
    return api.record_detection(
        client_event_id=event_id or str(uuid4()), detected_label=label,
        source="mock", action=action, confidence=0.91,
    )


def _state():
    with _database() as db:
        return {
            "items": db.execute("SELECT id,quantity_g,quantity FROM inventory_items ORDER BY id").fetchall(),
            "events": db.execute("""SELECT id,status,inventory_item_id,confirmation_sha256,applied_at
                                    FROM inventory_events ORDER BY id""").fetchall(),
            "history": db.execute("""SELECT event_id,inventory_item_id,quantity_g,before_quantity_g,after_quantity_g
                                     FROM inventory_event_changes ORDER BY before_quantity_g DESC""").fetchall(),
            "version": db.execute("SELECT * FROM inventory_sync_state").fetchone(),
        }


def _assert_error(status, operation):
    with pytest.raises(KiniAPIError) as error:
        operation()
    assert error.value.status == status
    return error.value


def test_vision_to_inventory_over_loopback(server, tmp_path):
    base, http, login = server
    token = login()
    api = KiniInventoryClient(base, token, tmp_path / "queue.jsonl")

    def snapshot():
        response = http.get(base + "/api/data/inventory", timeout=5,
                            headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        return response.json()

    pending = _detection(api)
    assert pending["is_demo"] and pending["requires_confirmation"]
    assert snapshot()["items"] == [] and snapshot()["inventory_version"] == 0
    assert _state()["history"] == [] and _state()["version"] is None

    event = pending["event_id"]
    applied = api.confirm(event, ingredient_id=901, quantity_g=1000)
    lot = applied["inventory_item_id"]
    assert api.confirm(event, ingredient_id=901, quantity_g=1000) == applied
    assert _detection(api, event_id=pending["client_event_id"]) == applied
    _assert_error(409, lambda: api.confirm(event, ingredient_id=901, quantity_g=999))
    _assert_error(409, lambda: api.reject(event))
    after_ingest = _state()
    assert len(after_ingest["items"]) == len(after_ingest["events"]) == len(after_ingest["history"]) == 1
    assert after_ingest["events"][0]["status"] == "applied"
    assert after_ingest["history"][0] == dict(event_id=event, inventory_item_id=lot, quantity_g=1000,
                                              before_quantity_g=0, after_quantity_g=1000)
    assert snapshot()["inventory_version"] == after_ingest["version"]["inventory_version"] == 1

    unknown = _detection(api, label="Unknown")
    assert unknown["requires_confirmation"] and snapshot()["pending_event_count"] == 1
    assert api.reject(unknown["event_id"])["status"] == "rejected"
    assert snapshot()["pending_event_count"] == 0
    for key in ("items", "history", "version"):
        assert _state()[key] == after_ingest[key]

    queued = [_detection(api, action="stock_out")["event_id"] for _ in range(2)]
    barrier = Barrier(2)

    def confirm_out(event_id):
        barrier.wait(timeout=5)
        try:
            return _confirm_out(api, event_id, lot)
        except KiniAPIError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(confirm_out, queued))
    (successful,) = [result for result in results if isinstance(result, dict)]
    assert [result.status for result in results if isinstance(result, KiniAPIError)] == [400]
    assert _confirm_out(api, successful["event_id"], lot) == successful
    state = _state()
    assert state["items"] == [dict(id=lot, quantity=300, quantity_g=300)]
    assert {row["status"] for row in state["events"] if row["id"] in queued} == {"applied", "pending_confirmation"}
    assert len(state["history"]) == 2 and state["version"]["inventory_version"] == 2
    assert state["history"][0] == dict(event_id=successful["event_id"], inventory_item_id=lot,
                                      quantity_g=700, before_quantity_g=1000, after_quantity_g=300)
    assert snapshot()["items"][0]["quantity_g"] == 300

    other = KiniInventoryClient(base, login("other", 202), tmp_path / "other.jsonl")
    _assert_error(404, lambda: other.reject(successful["event_id"]))
    assert _state() == state


def _confirm_out(api, event, lot):
    return api.confirm(event, ingredient_id=901, quantity_g=700, inventory_item_id=lot)


def test_queue_replay_after_real_http_response_loss_and_errors(server, tmp_path, monkeypatch):
    base, http, login = server
    assert http.get(base + "/api/data/inventory", timeout=5).status_code == 401
    token = login()
    viewer = login("viewer")
    queue = tmp_path / "queue.jsonl"
    api = KiniInventoryClient(base, token, queue)
    event_id = str(uuid4())
    real_send = requests.Session.send

    def lose_response(session, request, **kwargs):
        response = real_send(session, request, **kwargs)
        assert response.status_code in (200, 409)
        response.close()
        raise requests.ConnectionError("synthetic response loss")

    with monkeypatch.context() as fault:
        fault.setattr(requests.Session, "send", lose_response)
        _assert_error(None, lambda: _detection(api, event_id=event_id))
    (original,) = _state()["events"]

    def omit_authorization(session, request, **kwargs):
        request.headers.pop("Authorization", None)
        return real_send(session, request, **kwargs)

    original_queue = queue.read_bytes()
    with monkeypatch.context() as fault:
        fault.setattr(requests.Session, "send", omit_authorization)
        assert _assert_error(401, api.retry_pending).reauthentication_required
    assert queue.read_bytes() == original_queue

    # Preserve the A API's 403 for expired sessions and for viewer writes.
    with _database() as db:
        db.execute("UPDATE auth_sessions SET created_at=1,expires_at=2 WHERE token_hash=?",
                   (local_identity._digest(token),))
    assert _assert_error(403, api.retry_pending).manual_review_required
    assert api.retry_pending() == [] and queue.exists()
    viewer_api = KiniInventoryClient(base, viewer, queue)
    assert _assert_error(403, lambda: viewer_api.retry_pending(include_manual=True)).manual_review_required
    fresh = KiniInventoryClient(base, login(), queue)
    (replayed,) = fresh.retry_pending(include_manual=True)
    assert replayed["event_id"] == original["id"] and replayed["client_event_id"] == event_id
    assert not queue.exists()

    with monkeypatch.context() as fault:
        fault.setattr(requests.Session, "send", lose_response)
        _assert_error(None, lambda: _detection(fresh, event_id=event_id, label="conflicting"))
    assert _assert_error(409, fresh.retry_pending).manual_review_required
    assert fresh.retry_pending() == [] and queue.exists()
    assert _state()["events"] == [original]
    assert _state()["items"] == [] and _state()["version"] is None


def test_http_confirmation_rolls_back_on_database_error_after_writes(server, tmp_path, caplog):
    base, _, login = server
    api = KiniInventoryClient(base, login(), tmp_path / "queue.jsonl")
    applied = api.confirm(_detection(api)["event_id"], ingredient_id=901, quantity_g=1000)
    event = _detection(api, action="stock_out")["event_id"]
    lot = applied["inventory_item_id"]
    before = _state()
    with _database() as db:
        db.execute("""CREATE FUNCTION fail_test_version_update() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'synthetic version update failure'; END $$""")
        db.execute("""CREATE TRIGGER fail_test_version_update AFTER UPDATE ON inventory_sync_state
                      FOR EACH ROW EXECUTE FUNCTION fail_test_version_update()""")
    try:
        _assert_error(500, lambda: _confirm_out(api, event, lot))
        assert "synthetic version update failure" in caplog.text
        assert _state() == before
    finally:
        with _database() as db:
            db.execute("DROP TRIGGER fail_test_version_update ON inventory_sync_state")
            db.execute("DROP FUNCTION fail_test_version_update()")
    assert _confirm_out(api, event, lot)["status"] == "applied"
    assert _state()["version"]["inventory_version"] == 2
