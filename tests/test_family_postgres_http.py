"""Existing family contract over real HTTP and an explicitly isolated PostgreSQL DB."""
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from pathlib import Path
from secrets import token_urlsafe
import subprocess
import sys
from threading import Barrier, Thread
from uuid import uuid4

import pytest
import requests
from werkzeug.serving import make_server

from app.services import local_identity
from app.services.recommendation_repository import open_db


pytestmark = pytest.mark.skipif(
    os.getenv('KINI_DB_BACKEND', '').lower() != 'postgres' or not os.getenv('KINI_DATABASE_URL'),
    reason='requires an explicitly configured isolated PostgreSQL database',
)
ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def database():
    with closing(open_db()) as db, db:
        yield db


@pytest.fixture
def api(monkeypatch):
    from app.main import app

    with database() as db:
        assert db.execute("SELECT version FROM schema_migrations WHERE version='002_family_profiles'").fetchone()
        db.execute('TRUNCATE households, local_accounts CASCADE')
    monkeypatch.setitem(app.config, 'PROPAGATE_EXCEPTIONS', False)
    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    with requests.Session() as http:
        http.trust_env = False

        def call(method, path, body=None, headers=None):
            return http.request(method, f'http://127.0.0.1:{server.server_port}' + path,
                                json=body, headers=headers, timeout=10)

        try:
            yield call
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()


def headers(response):
    assert response.status_code == 200
    return {'Authorization': 'Bearer ' + response.json()['access_token']}


def account(api, name='synthetic-owner'):
    credentials = {'login_name': name, 'password': token_urlsafe(24)}
    created = api('POST', '/api/auth/register', credentials)
    assert created.status_code == 201
    login = api('POST', '/api/auth/account-login', credentials)
    return credentials, headers(login), created.json()['account_id']


def family(api, name='synthetic-owner'):
    credentials, account_headers, aid = account(api, name)
    created = api('POST', '/api/households', {'name': 'synthetic family'}, account_headers)
    assert created.status_code == 201
    hid = created.json()['id']
    session = api('POST', '/api/auth/login', {**credentials, 'household_id': hid})
    return hid, headers(session), account_headers, aid, credentials


def test_registration_family_profiles_and_existing_member_lookup(api, caplog):
    hid, auth, account_auth, aid, credentials = family(api)
    assert api('GET', '/api/households', headers=account_auth).json() == {
        'households': [{'id': hid, 'name': 'synthetic family', 'role': 'owner'}]}
    ids = []
    for name, age in [('synthetic adult', 40), ('synthetic child', 10)]:
        response = api('POST', f'/api/households/{hid}/members', {
            'name': name, 'age': age, 'allergies': [' shrimp ', 'shrimp'], 'dislikes': ['mushroom'],
        }, auth)
        assert response.status_code == 201
        member = response.json()
        assert member['household_id'] == hid and member['allergies'] == ['shrimp']
        assert member['preferences'] == [{'kind': 'dislike', 'value': 'mushroom'}]
        assert member['constraints'] == [] and member['targets'] == {}
        assert response.headers['Cache-Control'] == 'no-store'
        ids.append(member['id'])
    with database() as db:
        db.execute('INSERT INTO member_health_constraints VALUES (?,?,?)', (ids[0], 'sodium', 'low'))
        db.execute('INSERT INTO member_nutrition_targets VALUES (?,?,?)', (ids[0], 'protein_g', 60))
        assert db.execute('SELECT password_hash FROM local_accounts WHERE id=?', (aid,)).fetchone()[0] != credentials['password']
        stored = db.execute('SELECT token_hash FROM local_account_sessions WHERE account_id=?', (aid,)).fetchone()[0]
        assert stored == local_identity._digest(account_auth['Authorization'][7:])
    household = api('GET', f'/api/households/{hid}', headers=auth)
    assert household.status_code == 200 and household.json()['role'] == 'owner'
    assert api('GET', '/api/households/demo').status_code == 404
    assert [m['id'] for m in household.json()['members']] == ids
    assert household.json()['members'][0]['constraints'] == [{'kind': 'sodium', 'value': 'low'}]
    assert household.json()['members'][0]['targets'] == {'protein_g': 60}
    for path in [f'/api/households/{hid}/members', f'/api/members?household_id={hid}']:
        result = api('GET', path, headers=auth)
        assert result.status_code == 200
        assert result.json()['members'] == household.json()['members']
    for secret in [credentials['password'], auth['Authorization'][7:], account_auth['Authorization'][7:]]:
        assert secret not in caplog.text


def test_duplicate_account_registration_is_concurrent_and_case_insensitive(api):
    credentials = {'login_name': 'synthetic-duplicate', 'password': token_urlsafe(24)}
    barrier = Barrier(2)

    def register(name):
        barrier.wait(timeout=5)
        return api('POST', '/api/auth/register', {**credentials, 'login_name': name}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(register, [' SYNTHETIC-DUPLICATE ', 'synthetic-duplicate'])) == [201, 409]
    assert api('POST', '/api/auth/register', credentials).status_code == 409
    with database() as db:
        assert db.execute('SELECT COUNT(*) FROM local_accounts').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM households').fetchone()[0] == 0


def test_household_isolation_roles_and_token_scopes(api):
    hid, auth, account_auth, aid, _ = family(api)
    other_hid, other, other_account, _, _ = family(api, 'synthetic-other')
    for path in [f'/api/households/{hid}', f'/api/households/{hid}/members', f'/api/members?household_id={hid}']:
        assert api('GET', path, headers=other).status_code == 403
        assert api('GET', path).status_code == 401
    assert api('POST', f'/api/households/{hid}/members', {'name': 'synthetic', 'age': 1}, other).status_code == 403
    assert [h['id'] for h in api('GET', '/api/households', headers=other_account).json()['households']] == [other_hid]
    assert api('GET', '/api/households', headers=auth).status_code == 401
    assert api('GET', f'/api/households/{hid}', headers=account_auth).status_code == 403
    for role, expected in [('viewer', 403), ('editor', 201)]:
        with database() as db:
            db.execute('UPDATE account_households SET role=? WHERE account_id=?', (role, aid))
        assert api('GET', f'/api/households/{hid}', headers=auth).status_code == 200
        assert api('POST', f'/api/households/{hid}/members', {'name': 'synthetic', 'age': 1}, auth).status_code == expected
    assert api('GET', f'/api/households/{other_hid}', headers=auth).status_code == 403


@pytest.mark.parametrize('state', ['expired', 'revoked', 'inactive'])
def test_account_session_invalidated_before_household_creation(api, state):
    _, auth, aid = account(api)
    with database() as db:
        if state == 'expired':
            db.execute('UPDATE local_account_sessions SET created_at=0,expires_at=1')
        elif state == 'revoked':
            db.execute('UPDATE local_account_sessions SET revoked_at=1')
        else:
            db.execute('UPDATE local_accounts SET active=FALSE WHERE id=?', (aid,))
    assert api('GET', '/api/households', headers=auth).status_code == 401
    assert api('POST', '/api/households', {'name': 'synthetic'}, auth).status_code == 401
    with database() as db:
        assert db.execute('SELECT COUNT(*) FROM households').fetchone()[0] == 0


@pytest.mark.parametrize('target', ['account_households', 'member_preferences'])
def test_family_creation_and_profile_creation_roll_back_atomically(api, target):
    hid, auth, account_auth, _, _ = family(api)
    with database() as db:
        db.execute("""CREATE FUNCTION fail_family_write() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'synthetic family write failure'; END $$""")
        db.execute(f'CREATE TRIGGER fail_family_write BEFORE INSERT ON {target} FOR EACH ROW EXECUTE FUNCTION fail_family_write()')
    try:
        if target == 'account_households':
            response = api('POST', '/api/households', {'name': 'synthetic failed family'}, account_auth)
        else:
            response = api('POST', f'/api/households/{hid}/members', {
                'name': 'synthetic failed member', 'age': 10,
                'allergies': ['shrimp'], 'dislikes': ['mushroom'],
            }, auth)
        assert response.status_code == 500
        with database() as db:
            for table, count in [('households', 1), ('account_households', 1), ('members', 0),
                                 ('member_allergies', 0), ('member_preferences', 0)]:
                assert db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == count
    finally:
        with database() as db:
            db.execute(f'DROP TRIGGER fail_family_write ON {target}')
            db.execute('DROP FUNCTION fail_family_write()')


@pytest.mark.parametrize('start', ['empty', 'step1'])
def test_migrations_fresh_upgrade_repeat_and_profile_cascade(start):
    import psycopg
    from psycopg import sql

    schema = 'test_family_' + uuid4().hex
    url = os.environ['KINI_DATABASE_URL']
    aid = str(uuid4())
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        try:
            with psycopg.connect(url, options=f'-c search_path={schema}', autocommit=True) as db:
                if start == 'step1':
                    db.execute((ROOT / 'db/migrations/001_step1_postgresql.sql').read_text(encoding='utf-8'))
                    db.execute('INSERT INTO households(id,name) VALUES (9001,%s)', ('synthetic preserved family',))
                    db.execute('INSERT INTO members(id,household_id,name,age) VALUES (9002,9001,%s,10)', ('synthetic preserved member',))
                    db.execute('INSERT INTO local_accounts(id,login_name,password_hash) VALUES (%s,%s,%s)',
                               (aid, 'synthetic-preserved', 'synthetic-hash'))
                    db.execute("INSERT INTO account_households VALUES (%s,9001,'owner')", (aid,))
                    db.execute("INSERT INTO auth_sessions VALUES ('synthetic-session-hash',%s,9001,3600,0,NULL)", (aid,))
                env = {**os.environ, 'PGOPTIONS': f'-c search_path={schema}'}
                first = subprocess.run([sys.executable, str(ROOT / 'scripts/run_migrations.py')],
                                       env=env, capture_output=True, text=True)
                assert first.returncode == 0, 'migration failed in isolated schema'
                assert '002_family_profiles' in first.stdout
                repeat = subprocess.run([sys.executable, str(ROOT / 'scripts/run_migrations.py')],
                                        env=env, capture_output=True, text=True)
                assert repeat.returncode == 0 and 'already up to date' in repeat.stdout
                assert {'001_step1_postgresql', '002_family_profiles'} <= {r[0] for r in db.execute('SELECT version FROM schema_migrations')}
                if start == 'step1':
                    assert db.execute('SELECT name FROM households WHERE id=9001').fetchone()[0] == 'synthetic preserved family'
                    assert db.execute('SELECT COUNT(*) FROM auth_sessions').fetchone()[0] == 1
                    for table, columns, values in [
                        ('member_allergies', 'member_id,ingredient', (9002, 'shrimp')),
                        ('member_preferences', 'member_id,kind,value', (9002, 'dislike', 'mushroom')),
                        ('member_health_constraints', 'member_id,kind,value', (9002, 'sodium', 'low')),
                        ('member_nutrition_targets', 'member_id,nutrient,amount', (9002, 'protein_g', 60)),
                    ]:
                        placeholders = ','.join(['%s'] * len(values))
                        db.execute(f'INSERT INTO {table}({columns}) VALUES ({placeholders})', values)
                    db.execute('DELETE FROM members WHERE id=9002')
                    for table in ['member_allergies', 'member_preferences', 'member_health_constraints', 'member_nutrition_targets']:
                        assert db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0
        finally:
            admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
