"""Isolated text-only B WSGI entrypoint; does not import app.main or load .env.

One Gunicorn worker is required: its admission gate serializes model inference.
Every admitted request runs in a disposable process, bounded by a wall deadline.
"""
from contextlib import closing
import logging
import multiprocessing as mp
import os
from pathlib import Path
import re
import sqlite3
import threading
import time

from flask import Flask, jsonify, request, render_template

MODES = {'synthetic-fake', 'synthetic-ollama', 'real', 'rules-only'}
ROUTES = {
    ('POST', '/api/auth/login'), ('POST', '/api/recommendations'),
    ('GET', '/api/members'), ('POST', '/api/meal-sessions'),
    ('POST', '/api/cooking-sessions'),
    ('GET', '/api/data/inventory'), ('POST', '/api/data/inventory/events'),
    ('POST', '/api/inventory/normalize'),
    ('GET', '/api/meal-records'), ('GET', '/api/reports/nutrition'),
    ('GET', '/api/data-releases'), ('GET', '/api/data-quality/summary'), ('GET', '/api/data/status'),
}
RESOURCE_ROUTES = (
    ('GET', r'/api/households/[1-9][0-9]*'),
    ('POST', r'/api/households/[1-9][0-9]*/members'),
    ('GET', r'/api/meal-records/[1-9][0-9]*'),
    ('POST', r'/api/data/inventory/events/[1-9][0-9]*/confirm'),
    ('POST', r'/api/data/inventory/events/[1-9][0-9]*/reject'),
    ('GET', r'/api/recommendations/[1-9][0-9]*'),
    ('GET', r'/api/cooking-sessions/[1-9][0-9]*'),
    ('POST', r'/api/cooking-sessions/[1-9][0-9]*/ingredients'),
    ('POST', r'/api/cooking-sessions/[1-9][0-9]*/complete'),
)
FEEDBACK_ROUTES = (
    ('GET', r'/api/meal-records/[1-9][0-9]*/participants/[1-9][0-9]*/feedback'),
    ('PUT', r'/api/meal-records/[1-9][0-9]*/participants/[1-9][0-9]*/feedback'),
)


def feedback_enabled():
    value = os.getenv('KINI_B_MEAL_FEEDBACK_ENABLED', '0')
    if value not in {'0', '1'}:
        raise RuntimeError('Explicit feedback feature flag must be 0 or 1')
    return value == '1'


def failure(code):
    return {'status': 'api_error', 'ai_error_code': code, 'recommendations': [],
            'excluded': [], 'excluded_candidates': [],
            'message': 'AI 연결 오류로 추천을 만들지 못했습니다. 잠시 후 다시 시도해 주세요.'}


def settings():
    feedback_enabled()
    mode = os.environ.get('KINI_B_AI_MODE')
    storage = os.environ.get('KINI_B_STORAGE')
    if mode not in MODES or storage not in {'synthetic-sqlite', 'postgres'}:
        raise RuntimeError('Explicit B AI and storage modes are required')
    fault = os.getenv('KINI_B_SYNTHETIC_EXTERNAL_FAULT', '')
    if fault and (mode in {'real', 'rules-only'} or fault not in {'error', 'timeout'}):
        raise RuntimeError('Fault injection is synthetic-only')
    if storage == 'synthetic-sqlite':
        path = Path(os.environ['KINI_DB_PATH'])
        if not path.is_absolute() or not path.is_file():
            raise RuntimeError('Prepare an isolated synthetic DB first')
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
            marker = db.execute("SELECT value FROM metadata WHERE key='b_store_purpose'").fetchone()
            if marker != ('synthetic-only',):
                raise RuntimeError('Synthetic store marker is required')
        if os.getenv('KINI_DB_BACKEND', 'sqlite') != 'sqlite':
            raise RuntimeError('Storage configuration mismatch')
        if mode == 'real' and os.getenv('KINI_B_EXTERNAL_APPROVED') != '1':
            raise RuntimeError('External account/project/balance review is required')
    else:
        if os.getenv('KINI_DB_BACKEND') != 'postgres' or os.getenv('KINI_SEED_DEMO') != '0':
            raise RuntimeError('Postgres requires explicit backend and seed disabled')
        if mode not in {'real', 'rules-only'}:
            raise RuntimeError('Synthetic AI modes require isolated synthetic storage')
    if mode not in {'synthetic-fake', 'rules-only'} and not os.getenv('OLLAMA_MODEL'):
        raise RuntimeError('Required Ollama model is missing')
    if mode == 'real' and (os.getenv('KINI_B_EXTERNAL_APPROVED') != '1' or
                           not os.getenv('OPENAI_API_KEY') or not os.getenv('OPENAI_RECOMMENDATION_MODEL')):
        raise RuntimeError('Approved external project configuration is required')
    budget = float(os.getenv('KINI_B_DEADLINE_SECONDS', '15'))
    if not 1 <= budget <= 18:
        raise RuntimeError('Deadline must be between 1 and 18 seconds until C agreement')
    if any(os.getenv(key, '').lower() in {'1', 'true'} for key in
           ('LANGCHAIN_TRACING', 'LANGCHAIN_TRACING_V2', 'LANGSMITH_TRACING')):
        raise RuntimeError('External tracing must be disabled')
    return mode, storage, budget


def internal_app(mode):
    # Imports are lazy; no audio/video models are loaded for this text service.
    from app.services import recommendation_api as api
    from app.services.local_auth_api import api as auth
    from app.services.family_api import api as families
    from app.services.meal_reporting_api import api as reports
    from app.services.data_status_api import api as data_status
    from app.services.local_identity import authorize, AuthenticationError, AuthorizationError
    from app.services.recommendation_repository import open_db
    from app.services.ai.privacy_gateway import PrivacyGateway
    from app.services.ai.openai_client import FakeOpenAIRecommendationClient, HttpOpenAIRecommendationClient, OpenAIClientError, OpenAITimeoutError
    from app.services.ai.runtime_inspector import OllamaPrivacyInspector, SyntheticPrivacyInspector
    inspector = None if mode == 'rules-only' else SyntheticPrivacyInspector() if mode == 'synthetic-fake' else OllamaPrivacyInspector(timeout=10)
    external = None if mode == 'rules-only' else HttpOpenAIRecommendationClient(timeout_seconds=5) if mode == 'real' else FakeOpenAIRecommendationClient()
    fault = os.getenv('KINI_B_SYNTHETIC_EXTERNAL_FAULT', '')
    if fault:
        if mode == 'real' or fault not in {'error', 'timeout'}:
            raise RuntimeError('Fault injection is synthetic-only')
        class FailedSyntheticExternal:
            def explain(self, payload):
                if fault == 'timeout':
                    time.sleep(5)
                    raise OpenAITimeoutError('synthetic timeout')
                raise OpenAIClientError('synthetic failure')
        external = FailedSyntheticExternal()
    app = Flask('kini_b_internal')
    app.config['B_RULES_ONLY'] = mode == 'rules-only'
    app.config['B_PRIVACY_GATEWAY'] = PrivacyGateway(inspector, required=True)
    app.config['B_AI_CLIENT'] = external
    app.config['MAX_CONTENT_LENGTH'] = 16384

    @app.before_request
    def identity_boundary():
        if request.path == '/api/auth/login':
            return None
        scheme, _, token = request.headers.get('Authorization', '').partition(' ')
        if scheme.lower() != 'bearer' or not token.strip():
            return jsonify({'error': '인증이 필요합니다.'}), 401
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            return jsonify({'error': '요청 형식을 확인해 주세요.'}), 400
        try:
            with closing(open_db()) as db:
                household = body.get('household_id', request.args.get('household_id', type=int))
                if request.path == '/api/members' and type(household) is not int:
                    return jsonify({'error': '가구를 지정해 주세요.'}), 400
                identity = authorize(db, token.strip(), household_id=household, write=request.method != 'GET')
                match = re.fullmatch(r'/api/(recommendations|cooking-sessions)/([1-9][0-9]*)(?:/ingredients|/complete)?', request.path)
                resources = []
                if match:
                    resources.append(('recommendation_requests' if match[1] == 'recommendations' else 'cooking_sessions', int(match[2])))
                if request.path == '/api/cooking-sessions':
                    resources.extend([('meal_sessions', body.get('meal_session_id')),
                                      ('recommendation_requests', body.get('recommendation_request_id'))])
                for table, resource_id in resources:
                    row = db.execute(f'SELECT household_id FROM {table} WHERE id=?', (resource_id,)).fetchone()
                    if not row or row['household_id'] != identity['household_id']:
                        return jsonify({'error': '접근할 수 없습니다.'}), 403
                if request.path == '/api/recommendations' and request.method == 'POST':
                    # Reject unknown/raw input rather than letting Pydantic silently
                    # remove it before the privacy gateway sees the source.
                    from app.services.recommendation_models import RecommendationRequest
                    from app.services.ai.privacy_gateway import _unsafe_source_field
                    if _unsafe_source_field(body) or set(body) - set(RecommendationRequest.model_fields):
                        return jsonify({'status': 'privacy_blocked', 'recommendations': [],
                                        'excluded': [], 'excluded_candidates': []}), 201
        except AuthenticationError:
            return jsonify({'error': '인증이 필요합니다.'}), 401
        except AuthorizationError:
            return jsonify({'error': '접근 권한이 없습니다.'}), 403
    app.register_blueprint(auth)
    app.register_blueprint(families)
    app.register_blueprint(reports)
    # Preserve the existing A-confirmed-outflow completion contract. The outer
    # dispatcher still excludes all A inventory mutation endpoints.
    app.register_blueprint(data_status)
    app.register_blueprint(api.api)
    if feedback_enabled():
        from app.meal_feedback_app import load_feedback_options
        from app.services.meal_feedback import MealFeedbackService
        from app.services.meal_feedback_api import create_meal_feedback_blueprint
        from app.services.meal_feedback_store import SqlFeedbackStore
        options_path = os.getenv('KINI_MEAL_FEEDBACK_OPTIONS_FILE')
        options = load_feedback_options(options_path) if options_path else None
        feedback = MealFeedbackService(SqlFeedbackStore().transaction, options=options)
        app.register_blueprint(create_meal_feedback_blueprint(feedback))
    return app


def child(pipe, operation, args):
    # Suppress third-party/Flask traceback, body, token and exception logging.
    logging.disable(logging.CRITICAL)
    try:
        mode, storage, _ = settings()
        if operation == 'ready':
            from app.services.recommendation_repository import connect
            if storage == 'synthetic-sqlite':
                db = sqlite3.connect(Path(os.environ['KINI_DB_PATH']).as_uri() + '?mode=ro', uri=True)
            else:
                db = connect()
            with closing(db):
                if feedback_enabled():
                    options_path = os.getenv('KINI_MEAL_FEEDBACK_OPTIONS_FILE')
                    if options_path:
                        from app.meal_feedback_app import load_feedback_options
                        load_feedback_options(options_path)
                    db.execute('SELECT record_id,member_id,status,taste,seasoning,difficulty,revision,updated_at FROM meal_feedback LIMIT 0')
                    if storage == 'postgres':
                        allowed = all(db.execute("SELECT has_table_privilege(current_user,'meal_feedback',?)", (privilege,)).fetchone()[0]
                                      for privilege in ('SELECT', 'INSERT', 'UPDATE'))
                        if not allowed:
                            raise RuntimeError('Feedback runtime grants missing')
                for table in ('local_accounts', 'auth_sessions', 'account_households', 'members',
                              'recipes', 'recommendation_requests', 'cooking_sessions'):
                    db.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone()
            if mode not in {'synthetic-fake', 'rules-only'}:
                from app.services.ai.runtime_inspector import OllamaPrivacyInspector
                # Probe the real wire contract. An arbitrary health-labelled
                # object is not a valid AI request and can be rightly rejected.
                probe = {'schema_version': 'ai-recommendation-v1', 'participant_count': 1,
                         'meal_type': 'dinner', 'cook_time_limit_minutes': 20,
                         'purchase_allowed': False, 'constraint_codes': [],
                         'validated_candidates': [{'candidate_code': 'candidate_1',
                             'cook_time_minutes': 20, 'nutrition_per_serving': {'energy_kcal': 500},
                             'recommendation_reason_codes': []}], 'excluded_reason_codes': []}
                from app.services.ai.wire_schema import OutboundRecommendation
                OutboundRecommendation.model_validate(probe)
                if not OllamaPrivacyInspector(timeout=10).inspect(probe):
                    raise RuntimeError('inspector not ready')
            pipe.send(({'status': 'ready', 'storage': storage, 'ai_mode': mode,
                        'required_inspector': 'disabled' if mode == 'rules-only' else 'fake' if mode == 'synthetic-fake' else 'ollama',
                        'external_ai': 'disabled' if mode == 'rules-only' else 'openai' if mode == 'real' else 'fake',
                        'synthetic_external_fault': os.getenv('KINI_B_SYNTHETIC_EXTERNAL_FAULT') or None}, 200))
        else:
            method, path, query, body, authorization = args
            response = internal_app(mode).test_client().open(path + ('?' + query if query else ''),
                method=method, data=body, content_type='application/json',
                headers={'Authorization': authorization})
            pipe.send((response.get_json(silent=True) or failure('invalid_response'), response.status_code))
    except Exception:
        pipe.send((failure('dependency_unavailable'), 503))
    finally:
        pipe.close()


def bounded(operation, args, budget):
    context = mp.get_context('spawn')
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=child, args=(sender, operation, args), daemon=True)
    started = time.monotonic()
    try:
        process.start()
        sender.close()
        if receiver.poll(max(0, budget - (time.monotonic() - started))):
            try:
                return receiver.recv()
            except EOFError:
                return failure('worker_failed'), 503
        return failure('deadline_exceeded'), 504
    finally:
        if process.pid:
            if process.is_alive():
                process.terminate()
            process.join(timeout=.2)
            if process.is_alive():
                process.kill()
                process.join(timeout=.2)
        receiver.close()
        sender.close()


def create_app():
    mode, storage, budget = settings()
    if storage == 'postgres':
        from scripts.check_server_db import check
        check()  # A's read-only TLS/schema/migration/restricted-role gate.
    app = Flask('kini_b_runtime', root_path=str(Path(__file__).resolve().parent))
    app.config['MAX_CONTENT_LENGTH'] = 16384
    gate = threading.BoundedSemaphore(1)

    @app.get('/')
    @app.get('/mobile')
    @app.get('/fridge')
    def screen():
        return render_template('index.html', device_mode=request.path[1:] or 'auto')

    @app.get('/health/live')
    def live():
        return jsonify({'status': 'alive', 'ai_mode': mode, 'storage': storage})

    @app.get('/health/ready')
    def ready():
        if not gate.acquire(blocking=False):
            return jsonify(failure('busy')), 503
        try:
            body, status = bounded('ready', None, 12)
            return jsonify(body), status
        finally:
            gate.release()

    @app.route('/api/<path:rest>', methods=['GET', 'POST', 'PUT'])
    def dispatch(rest):
        if (request.method, request.path) not in ROUTES and not any(
                method == request.method and re.fullmatch(pattern, request.path)
                for method, pattern in RESOURCE_ROUTES + (FEEDBACK_ROUTES if feedback_enabled() else ())):
            return jsonify({'error': '이 B 텍스트 배포에서 제공하지 않는 경로입니다.'}), 404
        body = request.get_data()
        # Read-only screen queries can run together. Only model-bearing
        # recommendation generation shares the inference admission gate.
        needs_gate = request.method == 'POST' and request.path == '/api/recommendations'
        if needs_gate and not gate.acquire(blocking=False):
            return jsonify(failure('busy')), 503
        try:
            value, status = bounded('http', (request.method, request.path,
                request.query_string.decode('ascii'), body, request.headers.get('Authorization', '')), budget)
            return jsonify(value), status
        finally:
            if needs_gate:
                gate.release()

    @app.after_request
    def no_cache(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.errorhandler(413)
    def too_large(_):
        return jsonify({'error': '요청 크기가 제한을 초과했습니다.'}), 413

    return app
