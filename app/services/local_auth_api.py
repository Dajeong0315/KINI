"""HTTP adapter for the existing household-scoped local identity service."""
from contextlib import closing
import time

from flask import Blueprint, jsonify, request

from . import local_identity
from .recommendation_repository import open_db


api = Blueprint('local_auth', __name__)
SESSION_TTL_SECONDS = 3600
SESSION_COOKIE = 'kini_household_session'
COOKIE_PATH = '/api/auth'


@api.after_request
def prevent_auth_caching(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Pragma'] = 'no-cache'
    return response


def session_response(token, identity, now):
    return jsonify({
        'access_token': token,
        'token_type': 'Bearer',
        'expires_in': identity['expires_at'] - now,
        'expires_at': identity['expires_at'],
        'household_id': identity['household_id'],
        'role': identity['role'],
    })


def clear_session_cookie(response):
    response.delete_cookie(SESSION_COOKIE, path=COOKIE_PATH, httponly=True,
                           secure=request.is_secure, samesite='Strict')
    return response


@api.post('/api/auth/login')
def login():
    payload = request.get_json(silent=True)
    if (not isinstance(payload, dict)
            or set(payload) != {'login_name', 'password', 'household_id'}
            or not isinstance(payload['login_name'], str)
            or not payload['login_name'].strip()
            or len(payload['login_name']) > 256
            or not isinstance(payload['password'], str)
            or not payload['password']
            or len(payload['password']) > 4096
            or type(payload['household_id']) is not int
            or not 0 < payload['household_id'] <= 9223372036854775807):
        return jsonify({'error': '로그인 요청 형식을 확인해 주세요.'}), 400

    issued_at = int(time.time())
    try:
        with closing(open_db()) as db:
            token = local_identity.login(
                db, payload['login_name'], payload['password'], payload['household_id'],
                ttl_seconds=SESSION_TTL_SECONDS, now=issued_at,
            )
            identity = local_identity.authorize(db, token, now=issued_at)
    except PermissionError:
        return jsonify({'error': '로그인 정보를 확인해 주세요.'}), 401

    response = session_response(token, identity, issued_at)
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_TTL_SECONDS, path=COOKIE_PATH,
                        httponly=True, secure=request.is_secure, samesite='Strict')
    return response


@api.get('/api/auth/session')
def restore_session():
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        now = int(time.time())
        try:
            with closing(open_db(seed_demo=False)) as db:
                identity = local_identity.authorize(db, token, now=now)
        except local_identity.AuthenticationError:
            pass
        else:
            return session_response(token, identity, now)

    response = jsonify({'error': '다시 로그인해 주세요.'})
    response.status_code = 401
    return clear_session_cookie(response) if token else response


@api.post('/api/auth/logout')
def logout():
    # Require JSON so a cross-origin HTML form cannot submit this cookie-authenticated request.
    if not request.is_json:
        return jsonify({'error': '로그아웃 요청 형식을 확인해 주세요.'}), 400
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        with closing(open_db(seed_demo=False)) as db:
            local_identity.revoke(db, token)
    return clear_session_cookie(jsonify({'signed_out': True}))
