"""HTTP adapter for the existing household-scoped local identity service."""
from contextlib import closing
import time

from flask import Blueprint, jsonify, request

from . import local_identity
from .recommendation_repository import open_db


api = Blueprint('local_auth', __name__)
SESSION_TTL_SECONDS = 3600


@api.after_request
def prevent_auth_caching(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Pragma'] = 'no-cache'
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

    return jsonify({
        'access_token': token,
        'token_type': 'Bearer',
        'expires_in': SESSION_TTL_SECONDS,
        'expires_at': issued_at + SESSION_TTL_SECONDS,
        'household_id': identity['household_id'],
        'role': identity['role'],
    })
