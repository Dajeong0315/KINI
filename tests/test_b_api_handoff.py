"""Offline contract checks for the recommendation-to-meal flow used by client C."""

from contextlib import closing
from uuid import uuid4

from flask import Flask
import pytest

from app.services.ai.openai_client import OpenAIClientError


def _standalone_client():
    from app.services.recommendation_api import api

    app = Flask(__name__)
    app.register_blueprint(api)
    return app.test_client()


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
    client = _standalone_client()
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
    from app.services.recommendation_repository import open_db
    client = _standalone_client()
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


@pytest.fixture
def inventory_authority(monkeypatch, tmp_path):
    pytest.importorskip('app.services.data_status_api')
    from app.main import app
    from app.services.local_identity import create_account, login
    from app.services.recommendation_repository import open_db

    monkeypatch.setenv('KINI_DB_PATH', str(tmp_path / 'authority.sqlite3'))
    monkeypatch.setenv('KINI_USE_OPENAI', '0')
    with closing(open_db()) as db:
        create_account(db, 'owner', 'synthetic-test-password', 1)
        token = login(db, 'owner', 'synthetic-test-password', 1)
        with db:
            db.execute("INSERT INTO households VALUES (2,'other test household')")
        create_account(db, 'other', 'synthetic-other-password', 2)
        other_token = login(db, 'other', 'synthetic-other-password', 2)
    return app.test_client(), {'Authorization': f'Bearer {token}'}, {'Authorization': f'Bearer {other_token}'}


def _draft(client):
    recommendation = client.post('/api/recommendations', json=_request()).json
    meal = client.post('/api/meal-sessions', json=_request()).json
    result = client.post('/api/cooking-sessions', json={
        'meal_session_id': meal['session_id'],
        'recommendation_request_id': recommendation['request_id'],
        'recipe_id': 2,
    })
    assert result.status_code == 201
    return result.json


def _complete_draft(client, draft, headers=None, **fields):
    return client.post(f"/api/cooking-sessions/{draft['id']}/complete", headers=headers, json={
        'confirmed': True, 'eaten_at': '2026-11-25T12:00:00',
        'items': [{'member_id': 3, 'servings': 1}], **fields,
    })


def _inventory_event(client, headers, action, source, detected_label):
    response = client.post('/api/data/inventory/events', headers=headers, json={
        'client_event_id': str(uuid4()), 'source': source, 'action': action, 'detected_label': detected_label,
    })
    assert response.status_code == 200
    return response.json['event_id']


def _confirm_event(client, headers, event_id, **body):
    response = client.post(f'/api/data/inventory/events/{event_id}/confirm', headers=headers, json=body)
    assert response.status_code == 200
    assert response.json['status'] == 'applied'


def test_a_outflow_and_cooking_completion_only_change_stock_once(inventory_authority):
    client, headers, _ = inventory_authority
    draft = _draft(client)
    second_draft = _draft(client)
    original = client.get('/api/data/inventory', headers=headers).json
    lots = {item['ingredient_id']: item for item in original['items']}
    event_ids = []
    for item in draft['ingredients']:
        ingredient_id = item['ingredient_id']
        lot = lots[ingredient_id]
        # Remove 500g of tofu for a 400g recipe, then return 100g through A.
        is_tofu = ingredient_id == 4
        event_id = _inventory_event(client, headers, 'stock_out', 'yolo' if is_tofu else 'manual', lot['name'])
        _confirm_event(client, headers, event_id, ingredient_id=ingredient_id, inventory_item_id=lot['id'],
                       quantity_g=item['quantity_g'] + (100 if is_tofu else 0))
        event_ids.append(event_id)
    return_id = _inventory_event(client, headers, 'stock_in', 'manual', '두부')
    _confirm_event(client, headers, return_id, ingredient_id=4, quantity_g=100)
    after_outflow = client.get('/api/data/inventory', headers=headers).json
    assert sum(x['quantity_g'] for x in after_outflow['items'] if x['ingredient_id'] == 4) == 600
    assert after_outflow['inventory_version'] == original['inventory_version'] + 4

    response = _complete_draft(client, draft, headers, inventory_event_ids=event_ids)
    assert response.status_code == 201
    assert response.json['nutrition_per_serving'] == draft['nutrition_per_serving']
    assert client.get('/api/data/inventory', headers=headers).json == after_outflow
    assert _complete_draft(client, draft, headers, inventory_event_ids=event_ids).status_code == 400
    assert _complete_draft(client, second_draft, headers, inventory_event_ids=event_ids).status_code == 409
    assert client.get('/api/data/inventory', headers=headers).json == after_outflow

    from app.services.recommendation_repository import open_db

    with closing(open_db()) as db:
        assert db.execute('SELECT COUNT(*) FROM meal_records').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM cooking_inventory_event_links').fetchone()[0] == 3


def test_a_inventory_completion_requires_authorized_confirmed_stock_out(inventory_authority):
    client, headers, other_headers = inventory_authority
    draft = _draft(client)
    assert _complete_draft(client, draft).status_code == 401
    assert _complete_draft(client, draft, other_headers).status_code == 403
    missing = _complete_draft(client, draft, headers)
    assert missing.status_code == 409
    assert missing.json['code'] == 'inventory_confirmation_required'
    assert missing.json['ingredients'] == draft['ingredients']

    before = client.get('/api/data/inventory', headers=headers).json
    pending_id = _inventory_event(client, headers, 'stock_out', 'manual', '두부')
    assert _complete_draft(client, draft, headers, inventory_event_ids=[pending_id]).status_code == 409
    tofu = next(item for item in before['items'] if item['ingredient_id'] == 4)
    _confirm_event(client, headers, pending_id, ingredient_id=4, inventory_item_id=tofu['id'], quantity_g=100)
    after = client.get('/api/data/inventory', headers=headers).json
    assert _complete_draft(client, draft, headers, inventory_event_ids=[pending_id]).status_code == 409
    assert _complete_draft(client, draft, headers, inventory_event_ids=[pending_id, pending_id]).status_code == 409
    assert client.get('/api/data/inventory', headers=headers).json == after
    assert client.get(f"/api/cooking-sessions/{draft['id']}").json['status'] == 'active'
