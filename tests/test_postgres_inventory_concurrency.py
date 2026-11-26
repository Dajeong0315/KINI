"""Real PostgreSQL concurrency probes; run only against a disposable local DB."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest

from app.services import inventory_events, local_identity
from app.services.recommendation_repository import open_db


pytestmark = pytest.mark.skipif(
    os.getenv("KINI_DB_BACKEND", "").lower() != "postgres" or not os.getenv("KINI_DATABASE_URL"),
    reason="requires an explicitly configured isolated PostgreSQL database",
)


def _reset():
    db = open_db()
    db.execute("TRUNCATE inventory_event_changes, inventory_events, inventory_items, auth_sessions, account_households, local_accounts, ingredients, households CASCADE")
    db.execute("INSERT INTO households(id,name) VALUES (101,'concurrency household')")
    db.execute("INSERT INTO ingredients(id,name,canonical_name,category) VALUES (901,'synthetic ingredient','synthetic ingredient','test')")
    local_identity.create_account(db, "concurrency-owner", "synthetic-password", 101)
    token = local_identity.login(db, "concurrency-owner", "synthetic-password", 101)
    db.execute("INSERT INTO inventory_items(id,household_id,ingredient_id,quantity_g,quantity,unit,data_status) VALUES (7001,101,901,1000,1000,'g','verified')")
    db.commit()
    db.close()
    return token


def _parallel(calls):
    barrier = Barrier(len(calls))

    def run(call):
        db = open_db()
        try:
            barrier.wait(timeout=10)
            return call(db)
        except Exception as exc:  # preserve the concrete failure for assertions
            return exc
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        return list(pool.map(run, calls))


def _out_event(token, event_id, quantity):
    return lambda db: inventory_events.confirm(
        db, token, event_id,
        {"ingredient_id": 901, "quantity_g": quantity, "inventory_item_id": 7001},
    )


def test_same_event_concurrent_confirmation_applies_once():
    token = _reset()
    db = open_db()
    queued = inventory_events.ingest(db, token, {"client_event_id": str(uuid4()), "source": "manual", "action": "stock_out", "detected_label": "synthetic"})
    db.close()
    results = _parallel([_out_event(token, queued["event_id"], 400)] * 2)
    assert all(isinstance(result, dict) and result["status"] == "applied" for result in results)
    db = open_db()
    assert db.execute("SELECT quantity_g FROM inventory_items WHERE id=7001").fetchone()[0] == 600
    assert db.execute("SELECT COUNT(*) FROM inventory_event_changes WHERE event_id=?", (queued["event_id"],)).fetchone()[0] == 1
    assert db.execute("SELECT inventory_version FROM inventory_sync_state WHERE household_id=101").fetchone()[0] == 1
    db.close()


def test_different_events_overdraw_only_one_and_keep_three_hundred():
    token = _reset()
    db = open_db()
    events = [inventory_events.ingest(db, token, {"client_event_id": str(uuid4()), "source": "manual", "action": "stock_out", "detected_label": "synthetic"}) for _ in range(2)]
    db.close()
    results = _parallel([_out_event(token, event["event_id"], 700) for event in events])
    assert sorted(isinstance(result, dict) for result in results) == [False, True]
    db = open_db()
    assert db.execute("SELECT quantity_g FROM inventory_items WHERE id=7001").fetchone()[0] == 300
    assert db.execute("SELECT COUNT(*) FROM inventory_event_changes").fetchone()[0] == 1
    db.close()


def test_different_events_within_stock_both_apply_and_history_matches():
    token = _reset()
    db = open_db()
    events = [inventory_events.ingest(db, token, {"client_event_id": str(uuid4()), "source": "manual", "action": "stock_out", "detected_label": "synthetic"}) for _ in range(2)]
    db.close()
    results = _parallel([_out_event(token, events[0]["event_id"], 400), _out_event(token, events[1]["event_id"], 300)])
    assert all(isinstance(result, dict) and result["status"] == "applied" for result in results)
    db = open_db()
    assert db.execute("SELECT quantity_g FROM inventory_items WHERE id=7001").fetchone()[0] == 300
    assert db.execute("SELECT COUNT(*) FROM inventory_event_changes").fetchone()[0] == 2
    assert db.execute("SELECT COUNT(*) FROM inventory_event_changes WHERE before_quantity_g=1000 AND after_quantity_g=600").fetchone()[0] == 1
    assert db.execute("SELECT inventory_version FROM inventory_sync_state WHERE household_id=101").fetchone()[0] == 2
    db.close()
def test_same_client_event_concurrent_ingest_is_idempotent_and_conflict_is_explicit():
    token = _reset()
    event_id = str(uuid4())
    payload = {"client_event_id": event_id, "source": "manual", "action": "stock_in", "detected_label": "synthetic"}
    results = _parallel([lambda db: inventory_events.ingest(db, token, payload)] * 2)
    assert results[0] == results[1]
    with pytest.raises(inventory_events.EventConflict):
        db = open_db()
        try:
            inventory_events.ingest(db, token, {**payload, "action": "stock_out"})
        finally:
            db.close()
