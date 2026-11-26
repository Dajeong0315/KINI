"""Real PostgreSQL integration test for the KINI step 1 boundary.

Run only with KINI_DB_BACKEND=postgres and KINI_DATABASE_URL pointing at an
isolated disposable database. No Supabase credentials belong here.
"""
import os
from contextlib import closing

import pytest

from app.services import inventory_events, local_identity
from app.services.recommendation_repository import open_db


pytestmark = pytest.mark.skipif(
    os.getenv("KINI_DB_BACKEND", "").lower() != "postgres" or not os.getenv("KINI_DATABASE_URL"),
    reason="requires an explicitly configured isolated PostgreSQL database",
)


def test_step1_real_postgres_storage_permissions_and_rollback():
    with closing(open_db()) as db:
        db.execute("TRUNCATE inventory_event_changes, inventory_events, meal_session_participants, meal_sessions, inventory_items, ingredient_aliases, unit_conversions, ingredients, auth_sessions, account_households, members, local_accounts, households CASCADE")
        db.executemany("INSERT INTO households(id,name) VALUES (?,?)", [(101, "synthetic A"), (202, "synthetic B")])
        db.executemany("INSERT INTO members(id,household_id,name,age) VALUES (?,?,?,?)", [(1001,101,"A member",30),(2001,202,"B member",31)])
        db.execute("INSERT INTO ingredients(id,name,canonical_name,category) VALUES (?,?,?,?)", (901,"synthetic ingredient","synthetic ingredient","test"))
        a = local_identity.create_account(db, "pg-a", "synthetic-password-a", 101)
        b = local_identity.create_account(db, "pg-b", "synthetic-password-b", 202)
        viewer = local_identity.create_account(db, "pg-viewer", "synthetic-password-viewer", 101)
        db.execute("UPDATE account_households SET role='viewer' WHERE account_id=? AND household_id=?", (viewer, 101))
        db.commit()

        token_a = local_identity.login(db, "pg-a", "synthetic-password-a", 101)
        token_b = local_identity.login(db, "pg-b", "synthetic-password-b", 202)
        token_viewer = local_identity.login(db, "pg-viewer", "synthetic-password-viewer", 101)
        with pytest.raises(local_identity.AuthenticationError):
            local_identity.login(db, "pg-a", "wrong-password", 101)
        assert local_identity.authorize(db, token_a, household_id=101)["role"] == "owner"
        with pytest.raises(local_identity.AuthorizationError):
            local_identity.authorize(db, token_a, household_id=202)
        with pytest.raises(local_identity.AuthorizationError):
            inventory_events.ingest(db, token_viewer, {"client_event_id":"22222222-2222-2222-2222-222222222222", "source":"manual", "action":"stock_in", "detected_label":"synthetic ingredient", "confidence":1.0})

        event = inventory_events.ingest(db, token_a, {"client_event_id":"11111111-1111-1111-1111-111111111111", "source":"manual", "action":"stock_in", "detected_label":"synthetic ingredient", "confidence":1.0})
        applied = inventory_events.confirm(db, token_a, event["event_id"], {"ingredient_id":901,"quantity_g":500})
        duplicate = inventory_events.ingest(db, token_a, {"client_event_id":"11111111-1111-1111-1111-111111111111", "source":"manual", "action":"stock_in", "detected_label":"synthetic ingredient", "confidence":1.0})
        assert applied["status"] == "applied" and duplicate["event_id"] == applied["event_id"]
        assert db.execute("SELECT quantity_g FROM inventory_items WHERE household_id=?", (101,)).fetchone()[0] == 500
        assert db.execute("SELECT COUNT(*) FROM inventory_event_changes WHERE event_id=?", (applied["event_id"],)).fetchone()[0] == 1
        assert inventory_events.snapshot(db, token_b)["items"] == []

        meal_id = local_identity.create_meal_for_session(db, token_a, [1001], "dinner")
        participants = local_identity.meal_participants(db, token_a, meal_id)
        assert [row["id"] for row in participants] == [1001]
        try:
            with db:
                db.execute("INSERT INTO households(name) VALUES (?)", ("rollback household",))
                raise RuntimeError("rollback probe")
        except RuntimeError:
            pass
        assert db.execute("SELECT COUNT(*) FROM households WHERE name=?", ("rollback household",)).fetchone()[0] == 0
