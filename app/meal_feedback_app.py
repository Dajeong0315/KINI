"""Standalone feedback app factory for integration, separate from deployed B runtime.

flask --app app.meal_feedback_app:create_app run --host 127.0.0.1
Requires existing DB/schema and household tokens issued by existing auth API.
Approved options are injected or loaded from KINI_MEAL_FEEDBACK_OPTIONS_FILE;
unset options reject new ratings. No .env or deployment files are loaded here.
"""
import json
import os
from pathlib import Path

from flask import Flask, jsonify

from .services.meal_feedback import FeedbackOptions, MealFeedbackService
from .services.meal_feedback_api import create_meal_feedback_blueprint
from .services.meal_feedback_store import SqlFeedbackStore


def load_feedback_options(path):
    """Explicit feature config only; never substitute test/mock defaults."""
    try:
        path = Path(path)
        if not path.is_absolute():
            raise ValueError
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict) or any(
            not isinstance(codes, list) or not all(isinstance(code, str) for code in codes)
            for codes in value.values()
        ):
            raise ValueError
        options = FeedbackOptions(value)
        if any(code.lower().startswith(('mock_', 'test_'))
               for codes in options.codes.values() for code in codes):
            raise ValueError
        return options
    except Exception:
        raise RuntimeError('Invalid approved feedback options file') from None


def create_app(*, options=None, connection_factory=None):
    if options is None and os.getenv('KINI_MEAL_FEEDBACK_OPTIONS_FILE'):
        options = load_feedback_options(os.environ['KINI_MEAL_FEEDBACK_OPTIONS_FILE'])
    app = Flask('kini_meal_feedback')
    app.config['MAX_CONTENT_LENGTH'] = 16384
    store = SqlFeedbackStore() if connection_factory is None else SqlFeedbackStore(connection_factory)
    app.register_blueprint(create_meal_feedback_blueprint(MealFeedbackService(store.transaction, options=options)))

    @app.errorhandler(413)
    def too_large(_error):
        return jsonify({'error': '요청 크기를 확인해 주세요.', 'code': 'invalid_request'}), 413

    @app.after_request
    def no_cache(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Pragma'] = 'no-cache'
        return response

    return app
