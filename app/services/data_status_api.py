from contextlib import closing
from functools import wraps
from flask import Blueprint, jsonify, request
from pydantic import ValidationError
from .recommendation_repository import open_db
from . import inventory_events
from .public_catalog import service as catalog_service,PublicRecordMissing

api = Blueprint('data_status', __name__)


@api.get('/api/data/status')
def data_status():
    with closing(open_db()) as db:
        mode = db.execute("SELECT value FROM metadata WHERE key='dataset_mode'").fetchone()
        releases = [dict(r) for r in db.execute('SELECT source_code,release_id,cache_version FROM active_data_releases ORDER BY source_code')]
    return jsonify({'dataset_mode': mode[0] if mode else 'demo', 'releases': releases,
                    'catalog_projection': 'not_connected_to_recommendation_engine',
                    'supabase': {'purpose': 'public_food_catalog_and_daily_metrics', 'automatic_export_enabled': False,
                                 'catalog_sync_enabled': False},
                    'authentication': {'repository_available': True, 'http_enforced': False,
                                       'inventory_events_http_enforced': True}})


def local_session(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        header = request.headers.get('Authorization', '')
        if not header.startswith('Bearer ') or not header[7:].strip():
            return jsonify({'error': '로컬 로그인 세션이 필요합니다.'}), 401
        try:
            with closing(open_db()) as db:
                return fn(db, header[7:].strip(), *args, **kwargs)
        except PermissionError:
            return jsonify({'error': '세션 또는 가구 권한을 확인하세요.'}), 403
        except LookupError:
            return jsonify({'error': '이벤트를 찾을 수 없습니다.'}), 404
        except inventory_events.EventConflict:
            return jsonify({'error': '이벤트 ID 또는 반영 상태가 충돌합니다.'}), 409
        except (ValueError, ValidationError):
            return jsonify({'error': '재료·수량·이벤트 형식을 확인하세요.'}), 400
    return wrapped


@api.post('/api/data/inventory/events')
@local_session
def inventory_event_ingest(db, token):
    return jsonify(inventory_events.ingest(db, token, request.get_json(silent=True) or {})), 200


@api.post('/api/data/inventory/events/<int:event_id>/confirm')
@local_session
def inventory_event_confirm(db, token, event_id):
    return jsonify(inventory_events.confirm(db, token, event_id, request.get_json(silent=True) or {}))


@api.post('/api/data/inventory/events/<int:event_id>/reject')
@local_session
def inventory_event_reject(db, token, event_id):
    return jsonify(inventory_events.reject(db, token, event_id))


@api.get('/api/data/inventory')
@local_session
def inventory_snapshot(db, token):
    return jsonify(inventory_events.snapshot(db, token))


def _public_detail(kind,external_id):
    expected={'source'}
    if set(request.args)-expected:
        return jsonify({'error':'공개 조회에는 개인 조건이나 원문을 전달할 수 없습니다.'}),400
    source=request.args.get('source','mfds_nutrition' if kind=='food' else 'mfds_recipe')
    try:
        with closing(open_db()) as db:
            result=catalog_service().read(db,source,kind,external_id)
        return jsonify({'item':result.item,'cache':{'status':result.cache_status,'backend':result.backend}})
    except PublicRecordMissing:
        return jsonify({'error':'배포 승인된 공개 자료를 찾을 수 없습니다.'}),404
    except ValueError:
        return jsonify({'error':'공개 자료 ID 또는 서버 설정을 확인하세요.'}),400


@api.get('/api/data/catalog/foods/<external_id>')
def public_food(external_id):
    return _public_detail('food',external_id)


@api.get('/api/data/catalog/recipes/<external_id>')
def public_recipe(external_id):
    return _public_detail('recipe',external_id)


@api.get('/api/data/cache/status')
@local_session
def cache_status(db,token):
    from .local_identity import authorize
    authorize(db,token)
    current=catalog_service()
    return jsonify({'backend':current.cache.backend,'statistics':current.stats.summary(),
                    'personal_data_cached':False,'pattern_scope':'bounded_process_local_counts'})
