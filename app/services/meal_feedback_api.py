"""Dedicated authenticated GET/PUT blueprint; no app.main/AI imports."""
from functools import wraps

from flask import Blueprint, jsonify, request
from pydantic import ValidationError
from werkzeug.exceptions import BadRequest, UnsupportedMediaType

from .local_identity import AuthenticationError, AuthorizationError
from .meal_feedback import FeedbackError


def create_meal_feedback_blueprint(service):
    api = Blueprint('meal_feedback', __name__)

    @api.after_request
    def no_cache(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Pragma'] = 'no-cache'
        return response

    def boundary(fn):
        @wraps(fn)
        def wrapped(record_id, member_id):
            try:
                scheme, _, token = request.headers.get('Authorization', '').partition(' ')
                if scheme.lower() != 'bearer' or not token.strip():
                    raise AuthenticationError
                if request.args or not all(0 < value <= 9223372036854775807 for value in (record_id, member_id)):
                    raise ValueError
                return jsonify(fn(token.strip(), record_id, member_id))
            except AuthenticationError:
                return jsonify({'error': '인증이 필요합니다.', 'code': 'authentication_required'}), 401
            except AuthorizationError:
                return jsonify({'error': '가구 쓰기 권한이 필요합니다.', 'code': 'forbidden'}), 403
            except (ValidationError, ValueError, BadRequest, UnsupportedMediaType):
                return jsonify({'error': '요청 값의 형식을 확인해 주세요.', 'code': 'invalid_request'}), 400
            except FeedbackError as error:
                return jsonify(error.public_body()), error.status_code
        return wrapped

    path = '/api/meal-records/<int:record_id>/participants/<int:member_id>/feedback'

    @api.get(path)
    @boundary
    def get_feedback(token, record_id, member_id):
        return service.get(token, record_id, member_id)

    @api.put(path)
    @boundary
    def put_feedback(token, record_id, member_id):
        payload = request.get_json()
        if not isinstance(payload, dict):
            raise ValueError
        return service.put(token, record_id, member_id, payload)

    return api
