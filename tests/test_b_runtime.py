import os
import time
from contextlib import closing

import pytest

from app.b_runtime import bounded, create_app, internal_app, settings
from app.services.ai.privacy_gateway import PrivacyGateway
from app.services.ai.recommendation_chain import enrich_with_ai
from app.services.ai.openai_client import OpenAIClientError
from scripts.b_synthetic_store import prepare


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('KINI_DB_BACKEND', 'sqlite')
    path = tmp_path / 'synthetic.sqlite3'
    prepare(path, 'synthetic-b', 'synthetic-password-only')
    monkeypatch.setenv('KINI_DB_PATH', str(path))
    monkeypatch.setenv('KINI_B_STORAGE', 'synthetic-sqlite')
    monkeypatch.setenv('KINI_B_AI_MODE', 'synthetic-fake')
    monkeypatch.setenv('KINI_SEED_DEMO', '0')
    monkeypatch.delenv('KINI_B_MEAL_FEEDBACK_ENABLED', raising=False)
    monkeypatch.delenv('KINI_MEAL_FEEDBACK_OPTIONS_FILE', raising=False)
    for key in ('LANGCHAIN_TRACING', 'LANGCHAIN_TRACING_V2', 'LANGSMITH_TRACING'):
        monkeypatch.delenv(key, raising=False)
    return path


def login(client, name='synthetic-b', household=1):
    response = client.post('/api/auth/login', json={'login_name': name,
        'password': 'synthetic-password-only', 'household_id': household})
    assert response.status_code == 200, response.json
    return {'Authorization': 'Bearer ' + response.json['access_token']}


def payload(**changes):
    return {'household_id': 1, 'participant_ids': [1, 3], 'meal_type': 'dinner', **changes}


def test_bounded_authenticated_smoke_and_readiness(store):
    client = create_app().test_client()
    assert client.get('/health/live').json['status'] == 'alive'
    ready = client.get('/health/ready')
    assert ready.status_code == 200, ready.json
    assert ready.json['external_ai'] == 'fake'
    assert client.post('/api/recommendations', json=payload()).status_code == 401
    assert client.post('/api/auth/login', json={'login_name': 'synthetic-b',
        'password': 'wrong', 'household_id': 1}).status_code == 401
    headers = login(client)
    result = client.post('/api/recommendations', json=payload(), headers=headers)
    assert result.status_code == 201 and result.json['status'] == 'recommendations_available', result.json
    assert result.json['recommendations']
    saved = client.get('/api/recommendations/' + str(result.json['request_id']), headers=headers)
    assert saved.json['recommendations'] == result.json['recommendations']
    assert result.headers['Cache-Control'] == 'no-store'
    assert client.post('/api/voice/recommendations', headers=headers).status_code == 404


def test_household_and_object_boundaries(store):
    client = internal_app('synthetic-fake').test_client()
    headers = login(client)
    foreign = login(client, 'synthetic-b-other', 2)
    created = client.post('/api/recommendations', json=payload(), headers=headers).json
    assert client.get('/api/recommendations/' + str(created['request_id']), headers=foreign).status_code == 403
    assert client.post('/api/recommendations', json=payload(household_id=2), headers=headers).status_code == 403
    assert client.get('/api/members?household_id=1', headers=foreign).status_code == 403
    assert client.get('/api/members', headers=foreign).status_code == 400
    assert client.post('/api/recommendations', json=payload(participant_ids=[99]), headers=headers).status_code == 400


@pytest.mark.parametrize('source', [{'name': 'synthetic-private'}, {'text': 'raw-input'},
    {'desired_menu': '010-1234-5678'}, {'excluded_ingredients': ['김민지']}])
def test_source_block_never_calls_external(store, monkeypatch, source):
    from app.services.ai.runtime_inspector import SyntheticPrivacyInspector
    from app.services.ai.openai_client import FakeOpenAIRecommendationClient
    def forbidden(*args):
        pytest.fail('AI was called for blocked source')
    monkeypatch.setattr(SyntheticPrivacyInspector, 'inspect', forbidden)
    monkeypatch.setattr(FakeOpenAIRecommendationClient, 'explain', forbidden)
    client = internal_app('synthetic-fake').test_client()
    response = client.post('/api/recommendations', json=payload(**source), headers=login(client))
    assert response.json['status'] == 'privacy_blocked' and response.json['recommendations'] == []


@pytest.mark.parametrize('behavior,expected', [('raise', 'api_error'), ('missing', 'api_error'),
    ('invalid', 'api_error'), ('reject', 'privacy_blocked')])
def test_required_inspection_fail_closed(store, behavior, expected):
    from app.services.recommendation_repository import open_db
    from app.services.recommendation_engine import recommend
    class Inspector:
        def inspect(self, value):
            if behavior == 'raise':
                raise TimeoutError('sensitive exception must never escape')
            return None if behavior == 'invalid' else False
    class External:
        def explain(self, value):
            pytest.fail('External called after failed inspection')
    with closing(open_db()) as db:
        rules = recommend(db, payload(), persist=False)
    gateway = PrivacyGateway(None if behavior == 'missing' else Inspector(), required=True)
    result = enrich_with_ai(payload(), rules, gateway=gateway, client=External())
    assert result['status'] == expected and result['recommendations'] == []


def test_external_failure_no_candidates_and_sanitized_logs(store, monkeypatch, caplog):
    from app.services.ai.openai_client import FakeOpenAIRecommendationClient
    def fail(*args):
        raise OpenAIClientError('sk-SENSITIVE-EXCEPTION')
    monkeypatch.setattr(FakeOpenAIRecommendationClient, 'explain', fail)
    client = internal_app('synthetic-fake').test_client()
    response = client.post('/api/recommendations', json=payload(), headers=login(client))
    assert response.json['status'] == 'api_error' and response.json['recommendations'] == []
    assert 'SENSITIVE' not in str(response.json) + caplog.text


def test_inspector_cannot_modify_wire_payload(store):
    from app.services.recommendation_repository import open_db
    from app.services.recommendation_engine import recommend
    class Inspector:
        def inspect(self, value):
            value['token'] = 'must-not-leave'
            return True
    with closing(open_db()) as db:
        rules = recommend(db, payload(), persist=False)
    decision = PrivacyGateway(Inspector(), required=True).prepare(payload(), rules)
    assert decision.status == 'allowed' and 'token' not in decision.payload


def test_configuration_rejects_implicit_mode_and_tracing(store, monkeypatch):
    monkeypatch.delenv('KINI_B_AI_MODE')
    with pytest.raises(RuntimeError):
        settings()
    monkeypatch.setenv('KINI_B_AI_MODE', 'synthetic-fake')
    monkeypatch.setenv('LANGSMITH_TRACING', 'true')
    with pytest.raises(RuntimeError):
        settings()


def test_wall_deadline_kills_worker(store):
    # Spawn/import and request work cannot finish in zero time. Verify the hard
    # boundary returns no candidates and leaves no child alive (including on Linux).
    import multiprocessing
    started = time.monotonic()
    result, status = bounded('ready', None, 0)
    assert status == 504 and result['recommendations'] == []
    assert time.monotonic() - started < 2
    assert not multiprocessing.active_children()


def test_model_mode_does_not_silently_use_fake(store, monkeypatch):
    monkeypatch.setenv('KINI_B_AI_MODE', 'synthetic-ollama')
    monkeypatch.setenv('OLLAMA_MODEL', 'missing-synthetic-model-for-test')
    response = create_app().test_client().get('/health/ready')
    assert response.status_code in (503, 504)
    assert response.json['recommendations'] == []


def test_explicit_rules_mode_uses_no_models_and_is_labelled(store, monkeypatch):
    monkeypatch.setenv('KINI_B_AI_MODE', 'rules-only')
    monkeypatch.delenv('OLLAMA_MODEL', raising=False)
    from app.services.ai.openai_client import FakeOpenAIRecommendationClient, HttpOpenAIRecommendationClient
    from app.services.ai.runtime_inspector import OllamaPrivacyInspector
    def forbidden(*args, **kwargs):
        raise AssertionError('Rules-only must not invoke fake or real AI')
    monkeypatch.setattr(FakeOpenAIRecommendationClient, 'explain', forbidden)
    monkeypatch.setattr(HttpOpenAIRecommendationClient, 'explain', forbidden)
    monkeypatch.setattr(OllamaPrivacyInspector, 'inspect', forbidden)
    assert settings()[0] == 'rules-only'
    client = internal_app('rules-only').test_client()
    response = client.post('/api/recommendations', json=payload(), headers=login(client))
    assert response.status_code == 201 and response.json['status'] == 'recommendations_available'
    assert response.json['ai_status'] == 'disabled' and response.json['ai_mode'] == 'rules-only'
    assert 'ai_explanation' not in response.json


def test_postgres_rules_mode_does_not_require_paid_ai(store, monkeypatch):
    monkeypatch.setenv('KINI_B_STORAGE', 'postgres')
    monkeypatch.setenv('KINI_DB_BACKEND', 'postgres')
    monkeypatch.setenv('KINI_B_AI_MODE', 'rules-only')
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.delenv('KINI_B_EXTERNAL_APPROVED', raising=False)
    monkeypatch.delenv('OLLAMA_MODEL', raising=False)
    assert settings()[0:2] == ('rules-only', 'postgres')
    monkeypatch.setenv('KINI_B_AI_MODE', 'synthetic-ollama')
    with pytest.raises(RuntimeError):
        settings()


def test_screen_reads_do_not_compete_for_the_model_gate(store, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    import app.b_runtime as runtime
    entered,release=threading.Event(),threading.Event()
    def work(operation,args,budget):
        if operation=='http' and args[0:2]==('POST','/api/recommendations'):
            entered.set()
            assert release.wait(5)
        return {'status':'synthetic_test'},200
    monkeypatch.setattr(runtime,'bounded',work)
    app=create_app()
    with ThreadPoolExecutor() as pool:
        pending=pool.submit(lambda:app.test_client().post('/api/recommendations',json={}))
        try:
            assert entered.wait(5)
            assert app.test_client().get('/api/data/inventory').status_code==200
            assert app.test_client().post('/api/recommendations',json={}).status_code==503
        finally:
            release.set()
        assert pending.result().status_code==200


@pytest.mark.parametrize('fault', ['error', 'timeout'])
def test_deployed_synthetic_fault_switch(store, monkeypatch, fault):
    monkeypatch.setenv('KINI_B_SYNTHETIC_EXTERNAL_FAULT', fault)
    client = create_app().test_client()
    headers = login(client)
    response = client.post('/api/recommendations', json=payload(), headers=headers)
    assert response.json['status'] == 'api_error' and response.json['recommendations'] == []
    with closing(__import__('sqlite3').connect(store)) as db:
        assert db.execute('SELECT COUNT(*) FROM recommendation_candidates').fetchone()[0] == 0


def test_slow_request_is_cancelled_without_late_saved_candidates(store, monkeypatch):
    import multiprocessing
    monkeypatch.setenv('KINI_B_SYNTHETIC_EXTERNAL_FAULT', 'timeout')
    client = create_app().test_client()
    headers = login(client)
    monkeypatch.setenv('KINI_B_DEADLINE_SECONDS', '1')
    started = time.monotonic()
    response = create_app().test_client().post('/api/recommendations', json=payload(), headers=headers)
    assert response.status_code == 504 and response.json['recommendations'] == []
    assert time.monotonic() - started < 2
    assert not multiprocessing.active_children()
    with closing(__import__('sqlite3').connect(store)) as db:
        assert db.execute('SELECT COUNT(*) FROM recommendation_requests').fetchone()[0] == 0


def test_b_cooking_preserves_a_confirmed_outflow_contract(store):
    from uuid import uuid4
    from app.services.recommendation_repository import open_db
    from app.services import inventory_events
    client = internal_app('synthetic-fake').test_client()
    headers = login(client)
    token = headers['Authorization'][7:]
    req = payload(participant_ids=[3])
    rec = client.post('/api/recommendations', json=req, headers=headers).json
    meal = client.post('/api/meal-sessions', json=req, headers=headers).json
    session = client.post('/api/cooking-sessions', json={'meal_session_id': meal['session_id'],
        'recommendation_request_id': rec['request_id'], 'recipe_id': 2}, headers=headers)
    assert session.status_code == 201, session.json
    sid = session.json['id']
    body = {'confirmed': True, 'eaten_at': '2026-11-27T10:00:00+09:00',
            'items': [{'member_id': 3, 'servings': 1}], 'inventory_event_ids': []}
    assert client.post(f'/api/cooking-sessions/{sid}/complete', json=body, headers=headers).status_code == 409
    events = []
    with closing(open_db()) as db:
        for item in session.json['ingredients']:
            name = db.execute('SELECT name FROM ingredients WHERE id=?', (item['ingredient_id'],)).fetchone()[0]
            event = inventory_events.ingest(db, token, {'client_event_id': str(uuid4()),
                'source': 'manual', 'action': 'stock_out', 'detected_label': name, 'confidence': 1.0})
            stock = db.execute('SELECT id FROM inventory_items WHERE household_id=1 AND ingredient_id=? AND quantity_g>=? ORDER BY id', (item['ingredient_id'], item['quantity_g'])).fetchone()
            assert stock is not None
            inventory_events.confirm(db, token, event['event_id'], {
                'inventory_item_id': stock['id'], 'ingredient_id': item['ingredient_id'], 'quantity_g': item['quantity_g']})
            events.append(event['event_id'])
        before = [tuple(row) for row in db.execute('SELECT id,quantity_g FROM inventory_items ORDER BY id')]
    result = client.post(f'/api/cooking-sessions/{sid}/complete', json=body | {'inventory_event_ids': events}, headers=headers)
    assert result.status_code == 201, result.json
    with closing(open_db()) as db:
        assert [tuple(row) for row in db.execute('SELECT id,quantity_g FROM inventory_items ORDER BY id')] == before
    # The screen now reuses A's authenticated event contract. Invalid empty
    # payload is rejected rather than reaching an unimplemented endpoint.
    assert create_app().test_client().post('/api/data/inventory/events', headers=headers).status_code == 400


def test_feedback_disabled_preserves_existing_readiness(store, monkeypatch):
    monkeypatch.setenv('KINI_MEAL_FEEDBACK_OPTIONS_FILE', '/missing/not-loaded-while-disabled.json')
    client = create_app().test_client()
    assert client.get('/health/ready').status_code == 200
    assert client.get('/api/meal-records/201/participants/3/feedback').status_code == 404
    assert client.put('/api/meal-records/201/participants/3/feedback', json={}).status_code == 404


def test_feedback_enabled_missing_schema_is_unready_without_ddl(store, monkeypatch):
    import sqlite3
    monkeypatch.setenv('KINI_B_MEAL_FEEDBACK_ENABLED', '1')
    assert create_app().test_client().get('/health/ready').status_code == 503
    with closing(sqlite3.connect(store)) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name='meal_feedback'").fetchone() is None


def test_feedback_bounded_put_approved_codes_and_missing_options(store, monkeypatch):
    from pathlib import Path
    from app.services.recommendation_repository import open_db
    from scripts.setup_meal_feedback import install
    monkeypatch.setenv('KINI_B_MEAL_FEEDBACK_ENABLED', '1')
    with closing(open_db()) as db:
        install(db)
        db.execute("INSERT INTO meal_sessions(id,household_id,meal_type) VALUES (201,1,'dinner')")
        db.execute('INSERT INTO meal_session_participants VALUES (201,3)')
        db.execute("INSERT INTO meal_records(id,session_id,recipe_id,eaten_at) VALUES (201,201,2,'2026-11-27T09:00:00Z')")
        db.execute('INSERT INTO meal_record_items(record_id,member_id,servings) VALUES (201,3,1)')
        db.commit()
    client = create_app().test_client()
    path = '/api/meal-records/201/participants/3/feedback'
    headers = login(client)
    body = {'status': 'submitted', 'expected_revision': 0, 'ratings': {'taste': 'taste_satisfied'}}
    assert client.put(path, json=body).status_code == 401
    missing = client.put(path, json=body, headers=headers)
    assert missing.status_code == 503 and missing.json['code'] == 'feedback_options_unconfigured'
    options = Path(__file__).resolve().parents[1] / 'deploy/b/meal-feedback-options.v1.json'
    monkeypatch.setenv('KINI_MEAL_FEEDBACK_OPTIONS_FILE', str(options))
    assert client.get('/health/ready').status_code == 200
    saved = client.put(path, json=body, headers=headers)
    assert saved.status_code == 200 and saved.json['personalization_status'] == 'pending_mapping'
    assert client.put(path, json=body, headers=headers).json == saved.json
    assert client.get(path, headers=headers).json == saved.json
    bad = body | {'expected_revision': 1, 'ratings': {'taste': 'mock_taste_a'}}
    assert client.put(path, json=bad, headers=headers).status_code == 400
    skipped = client.put(path, json={'status': 'skipped', 'expected_revision': 1}, headers=headers)
    assert skipped.status_code == 200 and skipped.json['status'] == 'skipped'
    assert client.put('/api/recommendations', json={}, headers=headers).status_code == 404


def test_feedback_runtime_rejects_reserved_options_file(store, monkeypatch, tmp_path):
    import json
    path = tmp_path / 'invalid-options.json'
    path.write_text(json.dumps({'taste': ['test_taste'], 'seasoning': ['seasoning_balanced'],
                                'difficulty': ['difficulty_easy']}), encoding='utf-8')
    monkeypatch.setenv('KINI_B_MEAL_FEEDBACK_ENABLED', '1')
    monkeypatch.setenv('KINI_MEAL_FEEDBACK_OPTIONS_FILE', str(path))
    with pytest.raises(RuntimeError, match='Invalid approved'):
        internal_app('synthetic-fake')
