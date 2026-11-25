"""Offline contract checks for the recommendation-to-meal flow used by client C."""

from app.services.ai.openai_client import OpenAIClientError


def _request(**extra):
    return {
        "household_id": 1,
        "participant_ids": [3],
        "meal_type": "dinner",
        "purchase_allowed": False,
        **extra,
    }


def test_recommendation_terminal_states_use_offline_doubles(monkeypatch, tmp_path):
    """Success, no-safe, privacy, and AI-failure stay distinguishable over HTTP."""
    monkeypatch.setenv("KINI_DB_PATH", str(tmp_path / "handoff.sqlite3"))
    monkeypatch.delenv("KINI_USE_OPENAI", raising=False)
    import app.services.ai.recommendation_chain as chain
    from app.main import app

    client = app.test_client()
    success = client.post("/api/recommendations", json=_request())
    assert success.status_code == 201
    assert success.json["status"] == "recommendations_available"
    assert success.json["ai_explanation"]["source"] == "local_template"

    no_safe = client.post(
        "/api/recommendations",
        json=_request(excluded_ingredients=["쌀", "닭고기", "당근", "두부", "버섯", "새우", "견과류", "간장", "피망"]),
    )
    assert no_safe.status_code == 201
    assert no_safe.json["status"] == "no_safe_recommendation"
    assert no_safe.json["recommendations"] == []

    privacy = client.post("/api/recommendations", json=_request(excluded_ingredients=["010-1234-5678"]))
    assert privacy.status_code == 201
    assert privacy.json["status"] == "privacy_blocked"
    assert privacy.json["recommendations"] == []

    class FailingClient:
        def explain(self, _payload):
            raise OpenAIClientError("test failure")

    monkeypatch.setattr(chain, "configured_recommendation_client", lambda: FailingClient())
    failed = client.post("/api/recommendations", json=_request())
    assert failed.status_code == 201
    assert failed.json["status"] == "api_error"
    assert failed.json["recommendations"] == []


def test_recommendation_cooking_and_intake_flow(monkeypatch, tmp_path):
    monkeypatch.setenv("KINI_DB_PATH", str(tmp_path / "flow.sqlite3"))
    monkeypatch.delenv("KINI_USE_OPENAI", raising=False)
    from app.main import app

    client = app.test_client()
    recommendation = client.post("/api/recommendations", json=_request()).json
    meal = client.post("/api/meal-sessions", json=_request()).json
    cooking = client.post(
        "/api/cooking-sessions",
        json={
            "meal_session_id": meal["session_id"],
            "recommendation_request_id": recommendation["request_id"],
            "recipe_id": 2,
        },
    )
    assert cooking.status_code == 201
    before = cooking.json
    changed = client.post(
        f"/api/cooking-sessions/{cooking.json['id']}/ingredients",
        json={"ingredient": "버섯", "quantity_g": 0},
    )
    assert changed.status_code == 200
    assert changed.json['nutrition_per_serving']['protein_g'] < before['nutrition_per_serving']['protein_g']
    failed_change = client.post(f"/api/cooking-sessions/{before['id']}/ingredients", json={'ingredient': '새우', 'quantity_g': 100})
    assert failed_change.status_code == 400
    assert client.get(f"/api/cooking-sessions/{before['id']}").json['ingredients'] == changed.json['ingredients']
    recorded = client.post(
        f"/api/cooking-sessions/{cooking.json['id']}/complete",
        json={"confirmed": True, "eaten_at": "2026-11-25T12:00:00", "items": [{"member_id": 3, "servings": 1}]},
    )
    assert recorded.status_code == 201
    assert recorded.json["record_id"] > 0
    assert recorded.json['nutrition_per_serving'] == changed.json['nutrition_per_serving']


def test_completion_stock_zero_portions_and_atomic_failure(monkeypatch, tmp_path):
    monkeypatch.setenv('KINI_DB_PATH', str(tmp_path / 'stock.sqlite3'))
    monkeypatch.delenv('KINI_USE_OPENAI', raising=False)
    from app.main import app
    from app.services.recommendation_repository import open_db
    client = app.test_client()
    request = _request(participant_ids=[1, 3])
    rec = client.post('/api/recommendations', json=request).json
    meal = client.post('/api/meal-sessions', json=request).json
    created = client.post('/api/cooking-sessions', json={'meal_session_id': meal['session_id'], 'recommendation_request_id': rec['request_id'], 'recipe_id': 2})
    assert created.status_code == 201
    sid = created.json['id']
    payload = {'confirmed': True, 'eaten_at': '2026-11-25T12:00:00', 'items': [{'member_id': 1, 'servings': 1}, {'member_id': 3, 'servings': 0}]}
    with open_db() as db:
        db.execute('UPDATE inventory_items SET quantity_g=1, quantity=1 WHERE household_id=1 AND ingredient_id=4')
        before = [tuple(r) for r in db.execute('SELECT id,quantity_g FROM inventory_items ORDER BY id')]
    assert client.post(f'/api/cooking-sessions/{sid}/complete', json=payload).status_code == 400
    with open_db() as db:
        assert [tuple(r) for r in db.execute('SELECT id,quantity_g FROM inventory_items ORDER BY id')] == before
        assert db.execute('SELECT COUNT(*) FROM meal_records').fetchone()[0] == 0
        assert db.execute('SELECT status FROM cooking_sessions WHERE id=?', (sid,)).fetchone()[0] == 'active'
        db.execute('UPDATE inventory_items SET quantity_g=1000, quantity=1000 WHERE household_id=1 AND ingredient_id=4')
        before = {r['ingredient_id']: r['quantity_g'] for r in db.execute('SELECT ingredient_id,quantity_g FROM inventory_items WHERE household_id=1')}
    done = client.post(f'/api/cooking-sessions/{sid}/complete', json=payload)
    assert done.status_code == 201
    assert client.post(f'/api/cooking-sessions/{sid}/complete', json=payload).status_code == 400
    with open_db() as db:
        after = {r['ingredient_id']: r['quantity_g'] for r in db.execute('SELECT ingredient_id,quantity_g FROM inventory_items WHERE household_id=1')}
        for item in created.json['ingredients']:
            assert after[item['ingredient_id']] == before[item['ingredient_id']] - item['quantity_g']
        assert db.execute('SELECT servings FROM meal_record_items WHERE record_id=? AND member_id=3', (done.json['record_id'],)).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM preference_evidence WHERE source_type='meal_record' AND member_id=3").fetchone()[0] == 0
