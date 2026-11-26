"""Real PostgreSQL concurrency probes; run only against a disposable local DB."""
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
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


@pytest.fixture
def token():
    return _reset()


def _stock_state():
    with closing(open_db()) as db:
        return (
            tuple(db.execute("SELECT quantity_g,quantity FROM inventory_items WHERE id=7001").fetchone().values()),
            [(r[0], r[1], r[2]) for r in db.execute("""SELECT quantity_g,before_quantity_g,after_quantity_g
                FROM inventory_event_changes ORDER BY before_quantity_g DESC""")],
            db.execute("SELECT COALESCE(MAX(inventory_version),0) FROM inventory_sync_state").fetchone()[0],
            [r[0] for r in db.execute("SELECT status FROM inventory_events ORDER BY id")],
        )


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


def _queue_out(token):
    with closing(open_db()) as db:
        return inventory_events.ingest(db, token, {
            "client_event_id": str(uuid4()), "action": "stock_out",
            "detected_label": "synthetic",
        })["event_id"]


@pytest.mark.parametrize("run", range(10))
def test_different_confirmations_of_same_event(token, run):
    event = _queue_out(token)
    amounts = (400, 300)
    results = _parallel([_out_event(token, event, amount) for amount in amounts])
    assert sum(isinstance(result, inventory_events.EventConflict) for result in results) == 1, results
    (amount,) = [amount for amount, result in zip(amounts, results) if isinstance(result, dict)]
    remaining = 1000 - amount
    assert _stock_state() == ((remaining, remaining), [(amount, 1000, remaining)], 1, ["applied"])


@pytest.mark.parametrize("run", range(10))
def test_confirm_competes_with_reject(token, run):
    event = _queue_out(token)
    confirmed, rejected = _parallel([
        _out_event(token, event, 400),
        lambda db: inventory_events.reject(db, token, event),
    ])
    if isinstance(confirmed, dict):
        assert confirmed["status"] == "applied" and isinstance(rejected, inventory_events.EventConflict)
        assert _stock_state() == ((600, 600), [(400, 1000, 600)], 1, ["applied"])
    else:
        assert isinstance(confirmed, inventory_events.EventConflict) and isinstance(rejected, dict)
        assert rejected["status"] == "rejected"
        assert _stock_state() == ((1000, 1000), [], 0, ["rejected"])

def _out_event(token, event_id, quantity):
    return lambda db: inventory_events.confirm(
        db, token, event_id,
        {"ingredient_id": 901, "quantity_g": quantity, "inventory_item_id": 7001},
    )


@pytest.mark.parametrize("run", range(10))
def test_same_event_concurrent_confirmation_applies_once(token, run):
    db = open_db()
    queued = inventory_events.ingest(db, token, {"client_event_id": str(uuid4()), "source": "manual", "action": "stock_out", "detected_label": "synthetic"})
    db.close()
    results = _parallel([_out_event(token, queued["event_id"], 400)] * 2)
    assert all(isinstance(result, dict) and result["status"] == "applied" for result in results), results
    assert results[0] == results[1]
    db = open_db()
    assert db.execute("SELECT quantity_g FROM inventory_items WHERE id=7001").fetchone()[0] == 600
    assert db.execute("SELECT COUNT(*) FROM inventory_event_changes WHERE event_id=?", (queued["event_id"],)).fetchone()[0] == 1
    assert db.execute("SELECT inventory_version FROM inventory_sync_state WHERE household_id=101").fetchone()[0] == 1
    db.close()


@pytest.mark.parametrize("run", range(10))
def test_different_events_overdraw_only_one_and_keep_three_hundred(token, run):
    db = open_db()
    events = [inventory_events.ingest(db, token, {"client_event_id": str(uuid4()), "source": "manual", "action": "stock_out", "detected_label": "synthetic"}) for _ in range(2)]
    db.close()
    results = _parallel([_out_event(token, event["event_id"], 700) for event in events])
    assert sorted(isinstance(result, dict) for result in results) == [False, True], results
    assert [type(result) for result in results if not isinstance(result, dict)] == [ValueError], results
    stock, history, version, statuses = _stock_state()
    assert (stock, history, version, sorted(statuses)) == ((300, 300), [(700, 1000, 300)], 1, ["applied", "pending_confirmation"])


@pytest.mark.parametrize("run", range(10))
def test_different_events_within_stock_both_apply_and_history_matches(token, run):
    db = open_db()
    events = [inventory_events.ingest(db, token, {"client_event_id": str(uuid4()), "source": "manual", "action": "stock_out", "detected_label": "synthetic"}) for _ in range(2)]
    db.close()
    results = _parallel([_out_event(token, events[0]["event_id"], 400), _out_event(token, events[1]["event_id"], 300)])
    assert all(isinstance(result, dict) and result["status"] == "applied" for result in results), results
    stock, history, version, statuses = _stock_state()
    assert (stock, version, statuses) == ((300, 300), 2, ["applied", "applied"])
    first, second = history
    assert sorted((first[0], second[0])) == [300, 400]
    assert first[1] == 1000 and first[2] == second[1] and second[2] == 300
    assert all(before - after == quantity for quantity, before, after in history)


@pytest.mark.parametrize("run", range(10))
def test_same_client_event_concurrent_ingest_is_idempotent_and_conflict_is_explicit(token, run):
    event_id = str(uuid4())
    payload = {"client_event_id": event_id, "source": "manual", "action": "stock_in", "detected_label": "synthetic"}
    results = _parallel([lambda db: inventory_events.ingest(db, token, payload)] * 2)
    assert isinstance(results[0], dict) and results[0] == results[1], results
    assert _stock_state() == ((1000, 1000), [], 0, ["pending_confirmation"])
    with pytest.raises(inventory_events.EventConflict):
        db = open_db()
        try:
            inventory_events.ingest(db, token, {**payload, "action": "stock_out"})
        finally:
            db.close()
