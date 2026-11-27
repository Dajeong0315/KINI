"""Local account onboarding and household-scoped member registration."""
from contextlib import closing
from functools import wraps
import sqlite3
import time
from typing import Annotated, Literal

from flask import Blueprint, jsonify, request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from . import local_identity
from .recommendation_repository import members, open_db


api = Blueprint('families', __name__)
TTL_SECONDS = 3600
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Age = Annotated[int, Field(ge=0, le=130)]
ProfileValue = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class Credentials(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    login_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
    password: str = Field(min_length=1, max_length=4096)


class Registration(Credentials):
    password: str = Field(min_length=12, max_length=4096)


class HouseholdInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    name: Name


class MemberUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    name: Name | None = None
    age: Age | None = None
    height_cm: float | None = Field(default=None, ge=0.1, le=300)
    weight_kg: float | None = Field(default=None, ge=0.1, le=700)
    activity_level: Literal['sedentary', 'low_active', 'active', 'very_active'] | None = None
    allergies: list[ProfileValue] = Field(default_factory=list, max_length=32)
    dislikes: list[ProfileValue] = Field(default_factory=list, max_length=32)


class MemberInput(MemberUpdate):
    name: Name = Field()
    age: Age = Field()


@api.after_request
def prevent_profile_caching(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Pragma'] = 'no-cache'
    return response


def input_errors(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ValidationError:
            # Do not serialize Pydantic errors: they contain submitted credentials.
            return jsonify({'error': '요청 값의 형식을 확인해 주세요.'}), 400
    return wrapped


def session_required(*, account=False, write=False):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            scheme, _, token = request.headers.get('Authorization', '').partition(' ')
            if scheme.lower() != 'bearer' or not token.strip():
                return jsonify({'error': '로그인 세션이 필요합니다.'}), 401
            if not account and not 0 < kwargs['household_id'] <= 9223372036854775807:
                return jsonify({'error': '요청 값의 형식을 확인해 주세요.'}), 400
            with closing(open_db()) as db:
                try:
                    # Start writes before authorization so role/account changes cannot race them.
                    with db:
                        if write:
                            db.execute('BEGIN' if getattr(db, 'is_postgres', False) else 'BEGIN IMMEDIATE')
                        if account:
                            identity = local_identity.authorize_account(db, token.strip(), write=write)
                        else:
                            identity = local_identity.authorize(
                                db, token.strip(), household_id=kwargs['household_id'], write=write,
                            )
                        return fn(db, identity, *args, **kwargs)
                except PermissionError:
                    status = 401 if account else 403
                    return jsonify({'error': '세션 또는 가구 권한을 확인하세요.'}), status
        return wrapped
    return decorate


@api.post('/api/auth/register')
@input_errors
def register():
    obj = Registration.model_validate(request.get_json(silent=True))
    with closing(open_db(seed_demo=False)) as db:
        try:
            account_id = local_identity.create_account(db, obj.login_name, obj.password)
        except Exception as exc:
            # psycopg is optional for SQLite installs; recognize PostgreSQL's
            # unique violation without importing its driver on the SQLite path.
            if isinstance(exc, sqlite3.IntegrityError) or getattr(exc, 'sqlstate', None) == '23505':
                return jsonify({'error': '사용할 수 없는 로그인 이름입니다.'}), 409
            raise
    return jsonify({'account_id': account_id, 'login_name': obj.login_name.casefold()}), 201


@api.post('/api/auth/account-login')
@input_errors
def account_login():
    obj = Credentials.model_validate(request.get_json(silent=True))
    issued = int(time.time())
    try:
        with closing(open_db(seed_demo=False)) as db:
            token = local_identity.login_account(
                db, obj.login_name, obj.password, ttl_seconds=TTL_SECONDS, now=issued,
            )
    except PermissionError:
        return jsonify({'error': '로그인 정보를 확인해 주세요.'}), 401
    return jsonify({'access_token': token, 'token_type': 'Bearer', 'token_scope': 'account',
                    'expires_in': TTL_SECONDS, 'expires_at': issued + TTL_SECONDS})


@api.get('/api/households')
@session_required(account=True)
def households_list(db, identity):
    rows = db.execute('''SELECT h.id,h.name,a.role FROM households h JOIN account_households a
        ON a.household_id=h.id WHERE a.account_id=? ORDER BY h.id''', (identity['id'],))
    return jsonify({'households': [dict(row) for row in rows]})


@api.post('/api/households')
@input_errors
@session_required(account=True, write=True)
def household_create(db, identity):
    obj = HouseholdInput.model_validate(request.get_json(silent=True))
    household_id = db.execute('INSERT INTO households(name) VALUES (?)', (obj.name,)).lastrowid
    db.execute('INSERT INTO account_households VALUES (?,?,?)', (identity['id'], household_id, 'owner'))
    return jsonify({'id': household_id, 'name': obj.name, 'role': 'owner', 'members': []}), 201


@api.get('/api/households/<int:household_id>')
@session_required()
def household_get(db, identity, household_id):
    household = dict(db.execute('SELECT id,name FROM households WHERE id=?', (household_id,)).fetchone())
    return jsonify({**household, 'role': identity['role'], 'activity_level_supported': True,
                    'members': members(db, household_id, include_physical_profile=True)})


@api.get('/api/households/<int:household_id>/members')
@session_required()
def members_list(db, identity, household_id):
    return jsonify({'household_id': household_id, 'members': members(db, household_id, include_physical_profile=True)})


def replace_member_foods(db, member_id, *, allergies, dislikes):
    if allergies is not None:
        db.execute('DELETE FROM member_allergies WHERE member_id=?', (member_id,))
        db.executemany('INSERT INTO member_allergies(member_id,ingredient) VALUES (?,?)',
                       [(member_id, value) for value in sorted(set(allergies))])
    if dislikes is not None:
        db.execute("DELETE FROM member_preferences WHERE member_id=? AND kind='dislike'", (member_id,))
        db.executemany("INSERT INTO member_preferences(member_id,kind,value) VALUES (?,'dislike',?)",
                       [(member_id, value) for value in sorted(set(dislikes))])


@api.post('/api/households/<int:household_id>/members')
@input_errors
@session_required(write=True)
def member_create(db, identity, household_id):
    obj = MemberInput.model_validate(request.get_json(silent=True))
    member_id = db.execute('''INSERT INTO members(household_id,name,age,height_cm,weight_kg,activity_level)
                              VALUES (?,?,?,?,?,?)''',
                           (household_id, obj.name, obj.age, obj.height_cm, obj.weight_kg, obj.activity_level)).lastrowid
    replace_member_foods(db, member_id, allergies=obj.allergies or None, dislikes=obj.dislikes or None)
    return jsonify(members(db, household_id, [member_id], include_physical_profile=True)[0]), 201


@api.patch('/api/households/<int:household_id>/members/<int:member_id>')
@input_errors
@session_required(write=True)
def member_update(db, identity, household_id, member_id):
    profile = MemberUpdate.model_validate(request.get_json(silent=True))
    values = profile.model_dump(exclude_unset=True)
    if (not values or not 0 < member_id <= 9223372036854775807
            or any(key in values and values[key] is None for key in ('name', 'age'))):
        return jsonify({'error': '요청 값의 형식을 확인해 주세요.'}), 400
    # Serialize profile edits, including requests that only replace food lists.
    lock = ' FOR NO KEY UPDATE' if getattr(db, 'is_postgres', False) else ''
    scope = (household_id, member_id)
    if not db.execute('SELECT 1 FROM members WHERE household_id=? AND id=?' + lock, scope).fetchone():
        return jsonify({'error': '가족 구성원을 찾을 수 없습니다.'}), 404
    allergies = values.pop('allergies', None)
    dislikes = values.pop('dislikes', None)
    if values:
        # Remaining keys name member columns declared in MemberUpdate.
        assignments = ', '.join(f'{column}=?' for column in values)
        db.execute(f'UPDATE members SET {assignments} WHERE household_id=? AND id=?', (*values.values(), *scope))
    replace_member_foods(db, member_id, allergies=allergies, dislikes=dislikes)
    return jsonify(members(db, household_id, [member_id], include_physical_profile=True)[0])
