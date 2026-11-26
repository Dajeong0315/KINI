"""Local identity repository shared by the HTTP login adapter and local services."""
import hashlib
import secrets
import time
import uuid
from werkzeug.security import generate_password_hash, check_password_hash


class AuthenticationError(PermissionError):
    pass


class AuthorizationError(PermissionError):
    pass


def create_account(db, login_name, password, household_id):
    if not login_name.strip() or len(password) < 12:
        raise ValueError('로그인 이름과 12자 이상 비밀번호가 필요합니다.')
    account_id = str(uuid.uuid4())
    with db:
        db.execute('INSERT INTO local_accounts(id,login_name,password_hash) VALUES (?,?,?)',
                   (account_id, login_name.strip().casefold(), generate_password_hash(password)))
        db.execute('INSERT INTO account_households VALUES (?,?,?)', (account_id, household_id, 'owner'))
    return account_id


def login(db, login_name, password, household_id, *, ttl_seconds=3600, now=None):
    if not 0 < ttl_seconds <= 86400:
        raise ValueError('세션 수명은 1초~24시간이어야 합니다.')
    account = db.execute('''SELECT a.* FROM local_accounts a JOIN account_households h
        ON h.account_id=a.id WHERE a.login_name=? AND a.active=TRUE AND h.household_id=?''',
        (login_name.strip().casefold(), household_id)).fetchone()
    # Always perform a password hash check to reduce username enumeration timing.
    stored_hash = account['password_hash'] if account else _DUMMY_HASH
    valid = check_password_hash(stored_hash, password)
    if not account or not valid:
        raise AuthenticationError('로그인 정보를 확인해 주세요.')
    token = secrets.token_urlsafe(32)
    issued = int(time.time() if now is None else now)
    with db:
        db.execute('INSERT INTO auth_sessions(token_hash,account_id,household_id,created_at,expires_at) VALUES (?,?,?,?,?)',
                   (_digest(token), account['id'], household_id, issued, issued + ttl_seconds))
    return token


def _digest(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


_DUMMY_HASH = generate_password_hash('unused-' + secrets.token_hex(24))


def authorize(db, token, *, household_id=None, write=False, now=None):
    current = int(time.time() if now is None else now)
    row = db.execute('''SELECT s.account_id,s.household_id,h.role FROM auth_sessions s
        JOIN local_accounts a ON a.id=s.account_id
        JOIN account_households h ON h.account_id=s.account_id AND h.household_id=s.household_id
        WHERE s.token_hash=? AND s.revoked_at IS NULL AND s.expires_at>? AND a.active=TRUE''',
        (_digest(token), current)).fetchone()
    if not row:
        raise AuthenticationError('유효한 가구 세션이 필요합니다.')
    if household_id is not None and row['household_id'] != household_id:
        raise AuthorizationError('해당 가구에 접근할 수 없습니다.')
    if write and row['role'] == 'viewer':
        raise AuthorizationError('쓰기 권한이 없습니다.')
    return dict(row)


def revoke(db, token):
    with db:
        db.execute('UPDATE auth_sessions SET revoked_at=? WHERE token_hash=?', (int(time.time()), _digest(token)))


def create_meal_for_session(db, token, member_ids, meal_type):
    """Resolve household on the server, then join only that household's members."""
    if meal_type not in ('breakfast','lunch','dinner','snack'):
        raise ValueError('식사 유형을 확인해 주세요.')
    ids = sorted(set(member_ids))
    if not ids:
        raise ValueError('참여자를 선택하세요.')
    with db:
        identity = authorize(db, token, write=True)
        household_id = identity['household_id']
        placeholders = ','.join('?' for _ in ids)
        count = db.execute(f'SELECT COUNT(*) FROM members WHERE household_id=? AND id IN ({placeholders})',
                           (household_id, *ids)).fetchone()[0]
        if count != len(ids):
            raise PermissionError('다른 가구의 참여자를 사용할 수 없습니다.')
        meal_id = db.execute('INSERT INTO meal_sessions(household_id,meal_type) VALUES (?,?)',
                             (household_id, meal_type)).lastrowid
        db.executemany('INSERT INTO meal_session_participants VALUES (?,?)', [(meal_id, mid) for mid in ids])
        db.execute('''INSERT INTO meal_data_context SELECT ?,source_code,release_id
            FROM active_data_releases''', (meal_id,))
    return meal_id


def meal_participants(db, token, meal_id):
    identity = authorize(db, token)
    meal = db.execute('SELECT household_id FROM meal_sessions WHERE id=?', (meal_id,)).fetchone()
    if not meal or meal[0] != identity['household_id']:
        raise PermissionError('해당 식사 세션에 접근할 수 없습니다.')
    return [dict(row) for row in db.execute('''SELECT m.id,m.name,m.age FROM meal_sessions s
        JOIN meal_session_participants p ON p.session_id=s.id
        JOIN members m ON m.id=p.member_id AND m.household_id=s.household_id
        WHERE s.id=? AND s.household_id=? ORDER BY m.id''', (meal_id, identity['household_id']))]
