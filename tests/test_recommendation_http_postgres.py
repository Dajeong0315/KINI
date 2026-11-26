"""N3: real HTTP recommendation persistence/permissions with offline AI doubles."""
import os
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from secrets import token_urlsafe
from threading import Barrier, Thread

import pytest
import requests
from werkzeug.serving import make_server

from app.services.local_identity import create_account
from app.services.recommendation_repository import open_db
from app.services.catalog_repository import stage, validate, publish
from app.services.personalization import add_evidence
from app.services.ai.openai_client import FakeOpenAIRecommendationClient, OpenAIClientError, OpenAITimeoutError, AIExplanation
from test_recommendation_read_postgres import food
from test_recommendation_rules_postgres import rule_db, request as payload

pytestmark = pytest.mark.skipif(os.getenv('KINI_DB_BACKEND') != 'postgres', reason='requires disposable PostgreSQL')


@pytest.fixture
def http(rule_db, monkeypatch):
    from app.main import app
    from app.services.ai import recommendation_chain
    monkeypatch.setattr(recommendation_chain, 'configured_recommendation_client', FakeOpenAIRecommendationClient)
    monkeypatch.setitem(app.config, 'PROPAGATE_EXCEPTIONS', False)
    password = token_urlsafe(24)
    for name, hid in [('rec-owner',1), ('rec-viewer',1), ('rec-other',2)]:
        aid = create_account(rule_db, name, password, hid)
        if name == 'rec-viewer': rule_db.execute("UPDATE account_households SET role='viewer' WHERE account_id=?", (aid,))
    rule_db.commit()
    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    with requests.Session() as session:
        session.trust_env = False
        def call(method, path, body=None, auth=None):
            return session.request(method, f'http://127.0.0.1:{server.server_port}'+path,
                                   json=body, headers=auth, timeout=15)
        def login(name='rec-owner', hid=1):
            response = call('POST', '/api/auth/login', dict(login_name=name,password=password,household_id=hid))
            assert response.status_code == 200
            return {'Authorization': 'Bearer '+response.json()['access_token']}
        try: yield call, login
        finally:
            server.shutdown(); thread.join(timeout=5); server.server_close()


def counts(db):
    return tuple(db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in
                 ('recommendation_requests','recommendation_candidates','recommendation_feedback'))


def test_post_get_feedback_and_private_fields_stay_in_db(http, rule_db, caplog, monkeypatch):
    from app.services.ai import recommendation_chain as chain
    captured = []
    class Capturing(FakeOpenAIRecommendationClient):
        def explain(self, data):
            captured.append(data)
            return super().explain(data)
    monkeypatch.setattr(chain, 'configured_recommendation_client', Capturing)
    api, login = http; auth = login()
    response = api('POST', '/api/recommendations', payload(), auth)
    assert response.status_code == 201
    result = response.json(); rid = result['request_id']
    assert result['status'] == 'recommendations_available' and result['ai_explanation']
    assert result['recommendations'][0]['nutrition_per_serving']['energy_kcal'] == 100
    wire = json.dumps(captured)
    assert captured and 'synthetic adult' not in wire and auth['Authorization'][7:] not in wire
    for forbidden in ['household_id', 'member_id', 'participant_ids', 'profile_snapshot', 'inventory_snapshot', 'allergies']:
        assert f'"{forbidden}"' not in wire
    assert counts(rule_db) == (1,1,1)
    stored = api('GET', f'/api/recommendations/{rid}', auth=auth)
    assert stored.status_code == 200 and stored.json() == result
    assert stored.headers['Cache-Control'] == 'no-store'
    feedback = api('POST', f'/api/recommendations/{rid}/feedback', {
        'candidate_recipe_id':501, 'member_id':11, 'action':'selected', 'reason_text':'synthetic raw private note',
    }, auth)
    assert feedback.status_code == 201 and feedback.json()['feedback_id']
    assert counts(rule_db) == (1,1,2)
    assert rule_db.execute('SELECT reason_text FROM recommendation_feedback WHERE action=?', ('selected',)).fetchone()[0] is None
    assert auth['Authorization'][7:] not in caplog.text


def test_http_permissions_before_recommendation_and_retrieval(http, rule_db):
    api, login = http
    assert api('POST', '/api/recommendations', payload()).status_code == 401
    assert api('GET', '/api/recommendations/1').status_code == 401
    assert api('POST', '/api/recommendations', payload(), login('rec-viewer')).status_code == 403
    other = login('rec-other',2)
    assert api('POST', '/api/recommendations', payload(), other).status_code == 403
    assert counts(rule_db) == (0,0,0)
    created = api('POST', '/api/recommendations', payload(), login()).json()
    rid = created['request_id']
    assert api('GET', f'/api/recommendations/{rid}', auth=other).status_code == 403
    assert api('POST', f'/api/recommendations/{rid}/feedback', {'candidate_recipe_id':501,'action':'selected'}, other).status_code == 403
    assert api('GET', f'/api/recommendations/{rid}', auth=login('rec-viewer')).status_code == 200
    assert api('GET', '/api/recommendations/9999999', auth=login()).status_code == 404


@pytest.mark.parametrize('mode,status', [('error','api_error'), ('timeout','api_error'), ('malformed','api_error'), ('privacy','privacy_blocked'), ('allergy','no_safe_recommendation')])
def test_terminal_states_persist_no_visible_candidates(http, rule_db, monkeypatch, mode, status):
    from app.services.ai import recommendation_chain as chain
    from app.services.ai.privacy_gateway import PrivacyDecision
    if mode == 'privacy':
        class Denied:
            def prepare(self, *args): return PrivacyDecision('privacy_blocked', reason='synthetic')
        monkeypatch.setattr(chain, 'PrivacyGateway', Denied)
    elif mode == 'allergy':
        rule_db.execute("INSERT INTO member_allergies VALUES (11,'synthetic')"); rule_db.commit()
    else:
        class Broken:
            def explain(self, data):
                if mode == 'malformed': return AIExplanation('ignored',[{'candidate_code':'unknown'}])
                raise (OpenAITimeoutError if mode == 'timeout' else OpenAIClientError)('synthetic')
        monkeypatch.setattr(chain, 'configured_recommendation_client', Broken)
    api, login = http; auth = login()
    response = api('POST', '/api/recommendations', payload(), auth)
    assert response.status_code == 201 and response.json()['status'] == status
    assert response.json()['recommendations'] == [] and counts(rule_db) == (1,0,0)
    assert api('GET', f"/api/recommendations/{response.json()['request_id']}", auth=auth).json()['status'] == status


def new_release(db):
    release = stage(db,'mfds_nutrition',[food(200)], source_revision='next')
    assert not validate(db,release); publish(db,release)


def test_release_change_after_save_and_during_ai_returns_stale(http, rule_db, monkeypatch):
    from app.services.ai import recommendation_chain as chain
    api, login = http; auth = login()
    original = api('POST','/api/recommendations',payload(),auth).json()
    new_release(rule_db)
    stale = api('GET',f"/api/recommendations/{original['request_id']}",auth=auth).json()
    assert stale['status'] == 'stale_unvalidated' and stale['recommendations'] == []
    class Changing(FakeOpenAIRecommendationClient):
        def explain(self, data):
            with closing(open_db()) as db: new_release(db)
            return super().explain(data)
    monkeypatch.setattr(chain,'configured_recommendation_client',Changing)
    changed = api('POST','/api/recommendations',payload(),auth)
    assert changed.status_code == 201 and changed.json()['status'] == 'stale_unvalidated'
    assert changed.json()['recommendations'] == []
    assert rule_db.execute('SELECT COUNT(*) FROM recommendation_candidates WHERE request_id=?', (changed.json()['request_id'],)).fetchone()[0] == 0


def test_save_failure_rolls_back_request_candidates_feedback_and_preferences(http, rule_db):
    api, login = http; auth = login()
    for _ in range(3): add_evidence(rule_db,1,None,'conversation','ingredient','synthetic','prefer')
    rule_db.execute("""CREATE FUNCTION fail_candidate_write() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'synthetic candidate failure'; END $$""")
    rule_db.execute('CREATE TRIGGER fail_candidate_write BEFORE INSERT ON recommendation_candidates FOR EACH ROW EXECUTE FUNCTION fail_candidate_write()')
    rule_db.commit()
    try:
        assert api('POST','/api/recommendations',payload(),auth).status_code == 500
        assert counts(rule_db) == (0,0,0)
        assert rule_db.execute('SELECT COUNT(*) FROM inferred_preferences').fetchone()[0] == 0
    finally:
        rule_db.execute('DROP TRIGGER fail_candidate_write ON recommendation_candidates')
        rule_db.execute('DROP FUNCTION fail_candidate_write()'); rule_db.commit()
    assert api('POST','/api/recommendations',payload(),auth).status_code == 201


def test_concurrent_requests_do_not_duplicate_inferred_identity(http, rule_db):
    api, login = http; auth = login()
    for _ in range(3): add_evidence(rule_db,1,None,'conversation','ingredient','synthetic','prefer')
    rule_db.commit()
    barrier = Barrier(2)
    def run(_):
        barrier.wait(timeout=5)
        return api('POST','/api/recommendations',payload(),auth).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(run, range(2))) == [201,201]
    assert counts(rule_db) == (2,2,2)
    assert rule_db.execute('SELECT COUNT(*) FROM inferred_preferences WHERE household_id=1').fetchone()[0] == 1


def test_unmigrated_postgres_routes_do_not_expose_or_write_private_records(http, rule_db):
    api, login = http
    assert api('POST','/api/meal-records',{},login()).status_code == 501
    assert api('GET','/api/household-behavior-summary?household_id=1').status_code == 501


def test_unauthenticated_text_cannot_invoke_parser(http, monkeypatch):
    from app.services import recommendation_api
    def forbidden(*args): raise AssertionError('parser must not run before authentication')
    monkeypatch.setattr(recommendation_api, 'parse_recommendation_request', forbidden)
    api, _ = http
    assert api('POST','/api/recommendations',{'text':'synthetic private request'}).status_code == 401
