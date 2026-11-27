"""First account -> household -> four profiles, entirely over HTTP."""
from contextlib import closing
import secrets

import pytest

from app.services.recommendation_repository import open_db


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv('KINI_DB_PATH', str(tmp_path / 'family.sqlite3'))
    monkeypatch.setenv('KINI_SEED_DEMO', '0')
    monkeypatch.setenv('KINI_USE_OPENAI', '0')
    from app.main import app
    return app.test_client()


def _headers(response):
    return {'Authorization': 'Bearer ' + response.json['access_token']}


def _account(client, name='synthetic-owner'):
    password = secrets.token_urlsafe(24)
    credentials = {'login_name': name, 'password': password}
    created = client.post('/api/auth/register', json=credentials)
    assert created.status_code == 201
    login = client.post('/api/auth/account-login', json=credentials)
    assert login.status_code == 200
    return credentials, _headers(login), created.json['account_id']


def _family(client, name='synthetic-owner'):
    credentials, account_headers, account_id = _account(client, name)
    created = client.post('/api/households', headers=account_headers, json={'name': '합성 가족'})
    assert created.status_code == 201
    hid = created.json['id']
    login = client.post('/api/auth/login', json={**credentials, 'household_id': hid})
    assert login.status_code == 200
    return hid, _headers(login), account_headers, account_id


def test_first_account_family_and_four_members(client, monkeypatch, caplog):
    # Public registration must not seed a demo household even with the default demo setting.
    monkeypatch.delenv('KINI_SEED_DEMO')
    credentials, account_headers, account_id = _account(client)
    with closing(open_db()) as db:
        assert db.execute('SELECT COUNT(*) FROM households').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM members').fetchone()[0] == 0
        row = db.execute('SELECT password_hash FROM local_accounts').fetchone()
        assert bool(row[0] != credentials['password'])
    assert client.get('/api/households', headers=account_headers).json == {'households': []}
    created = client.post('/api/households', headers=account_headers, json={'name': ' 우리 가족 '})
    assert created.status_code == 201
    hid = created.json['id']
    assert created.json == {'id': hid, 'name': '우리 가족', 'role': 'owner', 'members': []}
    assert client.get('/api/households', headers=account_headers).json == {
        'households': [{'id': hid, 'name': '우리 가족', 'role': 'owner'}],
    }
    with closing(open_db()) as db:
        assert tuple(db.execute('SELECT * FROM account_households').fetchone()) == (account_id, hid, 'owner')
    login = client.post('/api/auth/login', json={**credentials, 'household_id': hid})
    assert login.status_code == 200
    household_headers = _headers(login)
    ids = []
    for name, age in [('엄마', 40), ('아빠', 41), ('자녀1', 12), ('자녀2', 8)]:
        result = client.post(f'/api/households/{hid}/members', headers=household_headers, json={
            'name': name, 'age': age, 'allergies': [' 새우 ', '새우'], 'dislikes': ['버섯'],
        })
        assert result.status_code == 201
        assert result.json['household_id'] == hid
        assert result.json['allergies'] == ['새우']
        assert result.json['preferences'] == [{'kind': 'dislike', 'value': '버섯'}]
        assert result.headers['Cache-Control'] == 'no-store'
        ids.append(result.json['id'])
    profiles = client.get(f'/api/households/{hid}', headers=household_headers)
    assert profiles.status_code == 200
    assert len(profiles.json['members']) == 4
    assert [m['id'] for m in profiles.json['members']] == ids
    meal = client.post('/api/meal-sessions', json={
        'household_id': hid, 'participant_ids': ids, 'meal_type': 'dinner',
    })
    assert meal.status_code == 201
    assert meal.json['participant_ids'] == ids
    assert len(client.get(f'/api/households/{hid}/members', headers=household_headers).json['members']) == 4
    inventory = client.get('/api/data/inventory', headers=household_headers)
    assert inventory.status_code == 200
    assert inventory.json['items'] == []
    assert client.get('/api/households/demo').status_code == 404
    assert client.get(f'/api/members?household_id={hid}').status_code == 401
    assert client.get(f'/api/members?household_id={hid}', headers=household_headers).status_code == 200
    with closing(open_db()) as db:
        assert db.execute('SELECT COUNT(*) FROM local_accounts').fetchone()[0] == 1
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    assert bool(credentials['password'] not in caplog.text)
    assert bool(login.json['access_token'] not in caplog.text)
    assert bool(account_headers['Authorization'] not in caplog.text)


def test_duplicate_registration_is_case_insensitive_and_atomic(client):
    credentials, _, _ = _account(client)
    response = client.post('/api/auth/register', json={**credentials, 'login_name': ' SYNTHETIC-OWNER '})
    assert response.status_code == 409
    assert response.json == {'error': '사용할 수 없는 로그인 이름입니다.'}
    with closing(open_db()) as db:
        assert db.execute('SELECT COUNT(*) FROM local_accounts').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM households').fetchone()[0] == 0


@pytest.mark.parametrize('changes', [
    {'login_name': ''}, {'login_name': ' '}, {'login_name': 1}, {'login_name': 'a' * 257},
    {'password': 'short'}, {'password': []}, {'password': 'x' * 4097},
    {'household_id': 1}, {'role': 'owner'}, {'account_id': 'spoof'},
])
def test_invalid_registration_does_not_write(client, changes):
    response = client.post('/api/auth/register', json={
        'login_name': 'synthetic', 'password': secrets.token_urlsafe(24), **changes,
    })
    assert response.status_code == 400
    assert response.json == {'error': '요청 값의 형식을 확인해 주세요.'}
    with closing(open_db()) as db:
        assert db.execute('SELECT COUNT(*) FROM local_accounts').fetchone()[0] == 0


@pytest.mark.parametrize('kind', ['missing', 'password', 'inactive'])
def test_account_login_failures_have_same_response(client, kind):
    credentials, _, _ = _account(client)
    if kind == 'missing':
        credentials['login_name'] = 'missing'
    elif kind == 'password':
        credentials['password'] = secrets.token_urlsafe(24)
    else:
        with closing(open_db()) as db, db:
            db.execute('UPDATE local_accounts SET active=0')
    response = client.post('/api/auth/account-login', json=credentials)
    assert response.status_code == 401
    assert response.json == {'error': '로그인 정보를 확인해 주세요.'}
    with closing(open_db()) as db:
        assert db.execute('SELECT COUNT(*) FROM local_account_sessions').fetchone()[0] == 1


def test_account_and_household_tokens_cannot_be_interchanged(client):
    hid, household_headers, account_headers, _ = _family(client)
    assert client.get('/api/data/inventory', headers=account_headers).status_code == 403
    assert client.get(f'/api/households/{hid}', headers=account_headers).status_code == 403
    assert client.get('/api/households', headers=household_headers).status_code == 401
    assert client.post('/api/households', headers=household_headers, json={'name': '다른 가족'}).status_code == 401
    with closing(open_db()) as db:
        assert db.execute('SELECT COUNT(*) FROM households').fetchone()[0] == 1


@pytest.mark.parametrize('state', ['expiry', 'revocation', 'inactive'])
def test_account_session_hash_and_invalid_session_rejection(client, state):
    from app.services.local_identity import _digest
    _, headers, _ = _account(client)
    with closing(open_db()) as db, db:
        row = db.execute('SELECT * FROM local_account_sessions').fetchone()
        assert bool(row['token_hash'] == _digest(headers['Authorization'][7:]))
        assert row['expires_at'] - row['created_at'] == 3600
        if state == 'expiry':
            db.execute('UPDATE local_account_sessions SET created_at=0, expires_at=1')
        elif state == 'revocation':
            db.execute('UPDATE local_account_sessions SET revoked_at=1')
        else:
            db.execute('UPDATE local_accounts SET active=0')
    assert client.get('/api/households', headers=headers).status_code == 401
    assert client.post('/api/households', headers=headers, json={'name': '가족'}).status_code == 401
    with closing(open_db()) as db:
        assert db.execute('SELECT COUNT(*) FROM households').fetchone()[0] == 0


def test_cross_household_access_and_mass_assignment_denied(client):
    hid, headers, _, _ = _family(client)
    other_id, other_headers, other_account, _ = _family(client, 'other-owner')
    assert client.get(f'/api/households/{hid}', headers=other_headers).status_code == 403
    assert client.get(f'/api/households/{hid}/members', headers=other_headers).status_code == 403
    assert client.post(f'/api/households/{hid}/members', headers=other_headers, json={'name': '침입', 'age': 20}).status_code == 403
    assert client.get(f'/api/members?household_id={hid}', headers=other_headers).status_code == 403
    assert client.get('/api/households/18446744073709551616', headers=headers).status_code == 400
    assert client.get('/api/households/0/members', headers=headers).status_code == 400
    assert [h['id'] for h in client.get('/api/households', headers=other_account).json['households']] == [other_id]
    assert client.post('/api/households', headers=other_account, json={'name': '가족', 'account_id': 'spoof'}).status_code == 400
    assert client.post(f'/api/households/{hid}/members', headers=headers, json={
        'name': '합성', 'age': 20, 'household_id': other_id,
    }).status_code == 400


@pytest.mark.parametrize('role', ['editor', 'viewer'])
def test_current_membership_controls_member_write(client, role):
    hid, headers, _, account_id = _family(client)
    with closing(open_db()) as db, db:
        db.execute('UPDATE account_households SET role=? WHERE account_id=?', (role, account_id))
    assert client.get(f'/api/households/{hid}/members', headers=headers).status_code == 200
    result = client.post(f'/api/households/{hid}/members', headers=headers, json={'name': '구성원', 'age': 0})
    assert result.status_code == (201 if role == 'editor' else 403)


def test_optional_physical_profile_can_be_updated_and_cleared_within_family(client):
    hid, headers, _, account_id = _family(client)
    _, other_headers, _, _ = _family(client, 'other-owner')
    path = f'/api/households/{hid}/members'
    profile = client.post(path, headers=headers, json={
        'name': '구성원', 'age': 10, 'height_cm': 141.5, 'activity_level': 'low_active', 'allergies': ['새우'],
    }).json
    assert profile['height_cm'] == 141.5 and profile['weight_kg'] is None
    assert profile['activity_level'] == 'low_active'
    member_path = f"{path}/{profile['id']}"
    assert client.patch(member_path, headers=other_headers, json={'weight_kg': 40}).status_code == 403
    assert client.patch(member_path, headers=headers, json={'weight_kg': -1}).status_code == 400
    assert client.patch(member_path, headers=headers, json={'height_cm': True}).status_code == 400
    assert client.patch(member_path, headers=headers, json={'activity_level': 'unknown'}).status_code == 400
    updated = client.patch(member_path, headers=headers, json={'weight_kg': 39.2})
    assert updated.status_code == 200
    assert updated.json == {**profile, 'weight_kg': 39.2}
    assert client.patch(member_path, headers=headers, json={'activity_level': 'active'}).json['activity_level'] == 'active'
    assert client.patch(member_path, headers=headers, json={'height_cm': None, 'activity_level': None}).status_code == 200
    assert client.get(path, headers=headers).json['members'] == [
        {**updated.json, 'height_cm': None, 'activity_level': None},
    ]
    with closing(open_db()) as db, db:
        db.execute("UPDATE account_households SET role='viewer' WHERE account_id=?", (account_id,))
    assert client.patch(member_path, headers=headers, json={'height_cm': 142}).status_code == 403


def test_member_update_replaces_only_submitted_profile_fields(client):
    hid, headers, _, _ = _family(client)
    path = f'/api/households/{hid}/members'
    profile = client.post(path, headers=headers, json={
        'name': '첫째', 'age': 10, 'allergies': ['새우'], 'dislikes': ['버섯'],
    }).json
    member_id = profile['id']
    member_path = f'{path}/{member_id}'
    with closing(open_db()) as db, db:
        db.execute('INSERT INTO member_preferences VALUES (?,?,?)', (member_id, 'strategy', 'sodium_management'))
    assert client.patch(member_path, headers=headers, json={'name': None, 'allergies': []}).status_code == 400
    assert client.patch(member_path, headers=headers, json={'age': None}).status_code == 400
    assert client.get(path, headers=headers).json['members'][0]['allergies'] == ['새우']

    update = client.patch(member_path, headers=headers, json={
        'name': '아들', 'age': 11, 'allergies': ['게', ' 게 '], 'dislikes': [],
    })
    assert update.status_code == 200
    assert update.json == {
        **profile, 'name': '아들', 'age': 11, 'allergies': ['게'],
        'preferences': [{'kind': 'strategy', 'value': 'sodium_management'}],
    }
    assert client.get(path, headers=headers).json['members'] == [update.json]


@pytest.mark.parametrize('changes', [
    {'name': ' '}, {'age': True}, {'age': '12'}, {'age': -1}, {'age': 131},
    {'allergies': ['']}, {'allergies': [1]}, {'allergies': '새우'},
    {'dislikes': ['a'] * 33}, {'role': 'owner'}, {'activity_level': 3},
])
def test_invalid_profile_does_not_partially_write(client, changes):
    hid, headers, _, _ = _family(client)
    response = client.post(f'/api/households/{hid}/members', headers=headers,
                           json={'name': '구성원', 'age': 10, **changes})
    assert response.status_code == 400
    with closing(open_db()) as db:
        assert db.execute('SELECT COUNT(*) FROM members').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM member_allergies').fetchone()[0] == 0


def test_malformed_json_and_missing_auth_are_safe(client):
    for path in ['/api/auth/register', '/api/auth/account-login']:
        for payload in [None, [], 'text', {}]:
            assert client.post(path, json=payload).status_code == 400
        assert client.post(path, data='{', content_type='application/json').status_code == 400
    for path in ['/api/households', '/api/households/1', '/api/households/1/members']:
        assert client.get(path).status_code == 401
    assert client.post('/api/households', json={'name': '가족'}).status_code == 401
    assert client.post('/api/households/1/members', json={'name': '구성원', 'age': 1}).status_code == 401


def test_existing_database_migration_preserves_accounts_profiles_and_tokens(client):
    hid, headers, _, _ = _family(client)
    member = client.post(f'/api/households/{hid}/members', headers=headers, json={'name': '보존', 'age': 10})
    assert member.status_code == 201
    with closing(open_db()) as db, db:
        db.execute('DROP TABLE local_account_sessions')
        db.execute('DELETE FROM platform_migrations WHERE version=3')
        db.execute('ALTER TABLE members DROP COLUMN height_cm')
        db.execute('ALTER TABLE members DROP COLUMN weight_kg')
        db.execute('ALTER TABLE members DROP COLUMN activity_level')
    # Reopening upgrades a database at the prior schema without changing household sessions.
    response = client.get(f'/api/households/{hid}/members', headers=headers)
    assert response.status_code == 200
    assert response.json['members'][0]['id'] == member.json['id']
    assert response.json['members'][0]['height_cm'] is None
    assert response.json['members'][0]['weight_kg'] is None
    assert response.json['members'][0]['activity_level'] is None
    with closing(open_db()) as db:
        assert [r[0] for r in db.execute('SELECT version FROM platform_migrations ORDER BY version')] == [1, 2, 3]
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
