"""HTTP-only session issuance with temporary SQLite and synthetic identities."""
from contextlib import closing
import secrets

import pytest

from app.services.recommendation_repository import open_db


@pytest.fixture
def auth(monkeypatch, tmp_path):
    monkeypatch.setenv('KINI_DB_PATH', str(tmp_path / 'auth.sqlite3'))
    monkeypatch.setenv('KINI_USE_OPENAI', '0')
    from app.main import app
    from app.services.local_identity import create_account

    password = secrets.token_urlsafe(24)
    with closing(open_db()) as db:
        with db:
            db.execute("INSERT INTO households VALUES (2,'synthetic household')")
        account = create_account(db, 'owner', password, 1)
    return app.test_client(), password, account


def _login(auth, **changes):
    client, password, _ = auth
    return client.post('/api/auth/login', json={
        'login_name': 'owner', 'password': password, 'household_id': 1, **changes,
    })


def _headers(response):
    return {'Authorization': 'Bearer ' + response.json['access_token']}


def test_login_contract_storage_and_no_secret_logging(auth, caplog):
    from app.services.local_identity import _digest

    response = _login(auth, login_name=' OWNER ')
    assert response.status_code == 200
    body = response.json
    assert set(body) == {'access_token', 'token_type', 'expires_in', 'expires_at', 'household_id', 'role'}
    assert body['token_type'] == 'Bearer'
    assert body['expires_in'] == 3600
    assert body['household_id'] == 1
    assert body['role'] == 'owner'
    assert response.headers['Cache-Control'] == 'no-store'
    with closing(open_db()) as db:
        row = db.execute('SELECT * FROM auth_sessions').fetchone()
        assert bool(row['token_hash'] == _digest(body['access_token']))
        assert bool(row['token_hash'] != body['access_token'])
        assert row['expires_at'] == body['expires_at']
        assert row['expires_at'] - row['created_at'] == body['expires_in']
    assert auth[0].get('/api/data/inventory', headers=_headers(response)).status_code == 200
    assert bool(auth[1] not in caplog.text)
    assert bool(body['access_token'] not in caplog.text)


@pytest.mark.parametrize('origin', ['http://localhost', 'https://localhost'])
def test_browser_session_restores_until_logout(auth, origin):
    client, password, _ = auth
    login = client.post('/api/auth/login', base_url=origin, json={
        'login_name': 'owner', 'password': password, 'household_id': 1,
    })
    assert login.status_code == 200
    cookie = login.headers['Set-Cookie']
    assert all(flag in cookie for flag in ('HttpOnly', 'SameSite=Strict', 'Path=/api/auth', 'Max-Age=3600'))
    assert ('; Secure' in cookie) == origin.startswith('https:')
    assert client.get('/api/data/inventory', base_url=origin).status_code == 401

    restored = client.get('/api/auth/session', base_url=origin)
    assert restored.status_code == 200
    assert restored.headers['Cache-Control'] == 'no-store'
    assert 'Set-Cookie' not in restored.headers
    assert restored.json['access_token'] == login.json['access_token']
    assert restored.json['expires_at'] == login.json['expires_at']
    assert restored.json['household_id'] == login.json['household_id']

    assert client.post('/api/auth/logout', base_url=origin, data={'form': 'value'}).status_code == 400
    assert client.post('/api/auth/logout', base_url=origin, json={}).status_code == 200
    assert client.get('/api/auth/session', base_url=origin).status_code == 401
    assert client.get('/api/data/inventory', base_url=origin, headers=_headers(login)).status_code == 403


@pytest.mark.parametrize('failure', ['password', 'account', 'household', 'inactive', 'membership'])
def test_login_failures_are_identical_and_do_not_issue_sessions(auth, failure, caplog):
    changes = {}
    if failure == 'password':
        changes['password'] = secrets.token_urlsafe(24)
    elif failure == 'account':
        changes['login_name'] = 'missing'
    elif failure == 'household':
        changes['household_id'] = 2
    else:
        with closing(open_db()) as db, db:
            if failure == 'inactive':
                db.execute('UPDATE local_accounts SET active=0')
            else:
                db.execute('DELETE FROM account_households')
    response = _login(auth, **changes)
    assert response.status_code == 401
    assert response.json == {'error': '로그인 정보를 확인해 주세요.'}
    assert response.headers['Cache-Control'] == 'no-store'
    with closing(open_db()) as db:
        assert db.execute('SELECT COUNT(*) FROM auth_sessions').fetchone()[0] == 0
    assert bool(auth[1] not in caplog.text)


@pytest.mark.parametrize('payload', [None, [], 'text', {},
    {'login_name': 1, 'password': 'placeholder', 'household_id': 1},
    {'login_name': 'owner', 'password': [], 'household_id': 1},
    {'login_name': 'owner', 'password': 'placeholder', 'household_id': True},
    {'login_name': 'owner', 'password': 'placeholder', 'household_id': '1'},
    {'login_name': 'owner', 'password': 'placeholder', 'household_id': 0},
    {'login_name': 'owner', 'password': 'placeholder', 'household_id': 2**64},
    {'login_name': 'owner', 'password': 'placeholder', 'household_id': 1, 'role': 'owner'},
])
def test_malformed_payload_is_safe(auth, payload):
    response = auth[0].post('/api/auth/login', json=payload)
    assert response.status_code == 400
    assert response.json == {'error': '로그인 요청 형식을 확인해 주세요.'}


def test_invalid_json_is_safe(auth):
    response = auth[0].post('/api/auth/login', data='{', content_type='application/json')
    assert response.status_code == 400


@pytest.mark.parametrize('role', ['editor', 'viewer'])
def test_server_role_controls_writes(auth, role):
    with closing(open_db()) as db, db:
        db.execute('UPDATE account_households SET role=?', (role,))
    response = _login(auth)
    assert response.status_code == 200
    assert response.json['role'] == role
    client = auth[0]
    headers = _headers(response)
    assert client.get('/api/data/inventory', headers=headers).status_code == 200
    result = client.post('/api/data/inventory/events', headers=headers, json={
        'client_event_id': '00000000-0000-4000-8000-000000000001', 'source': 'manual',
        'action': 'stock_out', 'detected_label': '두부',
    })
    assert result.status_code == (403 if role == 'viewer' else 200)


@pytest.mark.parametrize('change', ['expiry', 'revocation', 'inactive', 'membership'])
def test_session_stops_working_after_server_change(auth, change):
    response = _login(auth)
    assert response.status_code == 200
    with closing(open_db()) as db, db:
        if change == 'expiry':
            db.execute('UPDATE auth_sessions SET created_at=0, expires_at=1')
        elif change == 'revocation':
            db.execute('UPDATE auth_sessions SET revoked_at=1')
        elif change == 'inactive':
            db.execute('UPDATE local_accounts SET active=0')
        else:
            db.execute('DELETE FROM account_households')
    assert auth[0].get('/api/data/inventory', headers=_headers(response)).status_code == 403
    assert auth[0].get('/api/auth/session').status_code == 401


def test_multi_household_selection_issues_scoped_sessions(auth):
    with closing(open_db()) as db, db:
        db.execute("INSERT INTO account_households VALUES (?,2,'viewer')", (auth[2],))
    first = _login(auth)
    second = _login(auth, household_id=2)
    assert first.status_code == second.status_code == 200
    assert first.json['role'] == 'owner'
    assert second.json['role'] == 'viewer'
    for response, household_id in [(first, 1), (second, 2)]:
        snapshot = auth[0].get('/api/data/inventory', headers=_headers(response))
        assert snapshot.status_code == 200
        if household_id == 2:
            assert snapshot.json['items'] == []
        else:
            assert len(snapshot.json['items']) > 0
