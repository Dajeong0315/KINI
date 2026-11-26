"""Step 1 synthetic boundary tests; no Supabase access or external AI calls."""
from contextlib import closing

import pytest

from app.services import local_identity
from app.services.recommendation_repository import initialize, open_db


def test_two_synthetic_households_are_isolated(tmp_path):
    path = str(tmp_path / "synthetic-step1.sqlite3")
    with closing(open_db(path, seed_demo=False)) as db:
        initialize(db, seed_demo=False)
        db.executemany("INSERT INTO households(id,name) VALUES (?,?)", [(101, "합성 가구 A"), (202, "합성 가구 B")])
        db.executemany("INSERT INTO members(id,household_id,name,age) VALUES (?,?,?,?)", [(1001,101,"A 구성원",30),(2001,202,"B 구성원",31)])
        a = local_identity.create_account(db, "synthetic-a", "synthetic-password-a", 101)
        b = local_identity.create_account(db, "synthetic-b", "synthetic-password-b", 202)
        db.execute("INSERT INTO ingredients(id,name,category) VALUES (?,?,?)", (901, "합성 식재료", "test"))
        db.executemany("INSERT INTO inventory_items(id,household_id,ingredient_id,quantity_g,freshness_state) VALUES (?,?,?,?,?)", [(9101,101,901,500,"good"),(9201,202,901,700,"good")])
        db.commit()
        token_a = local_identity.login(db, "synthetic-a", "synthetic-password-a", 101)
        token_b = local_identity.login(db, "synthetic-b", "synthetic-password-b", 202)
        assert local_identity.authorize(db, token_a, household_id=101)["role"] == "owner"
        assert local_identity.authorize(db, token_b, household_id=202)["role"] == "owner"
        with pytest.raises(PermissionError):
            local_identity.authorize(db, token_a, household_id=202)
        rows = db.execute("SELECT id,quantity_g FROM inventory_items WHERE household_id=?", (101,)).fetchall()
        assert [dict(row) for row in rows] == [{"id": 9101, "quantity_g": 500.0}]


def test_postgres_mode_never_initializes_or_seeds(monkeypatch):
    class FakeDb:
        def close(self):
            pass

    monkeypatch.setenv("KINI_DB_BACKEND", "postgres")
    monkeypatch.setattr("app.services.postgres_repository.connect_postgres", lambda: FakeDb())
    db = open_db()
    assert isinstance(db, FakeDb)
