from contextlib import closing
from datetime import datetime
import json, sqlite3, tempfile, os
from uuid import uuid4
from pathlib import Path
from flask import Blueprint, current_app, jsonify, request
from pydantic import ValidationError
from .recommendation_repository import open_db, members
from .recommendation_models import RecommendationRequest, MealSessionRequest, MealRecordRequest, ConversationRequest, MessageRequest, FeedbackRequest, CookingAdjustmentRequest, CookingSessionRequest, CookingChangeRequest, CookingCompleteRequest, CookingQuestionRequest, InventoryNormalizeRequest, InventoryItemRequest
from .recommendation_engine import recommend, persist_recommendation, NUTRIENTS
from .recommendation_llm import explain_recommendation, parse_recommendation_request, extract_preference_signal
from .ai.recommendation_chain import enrich_with_ai
from .personalization import add_evidence, aggregate, record_recipe, recipe_features, update_summary
from . import cooking
from .errors import ServiceError
from .local_stt import transcribe_audio
from .local_identity import authorize, AuthenticationError, AuthorizationError
from . import inventory_events
from .ingredient_normalization import normalize_ingredient
from .unit_conversion import convert_quantity

api=Blueprint('recommendations',__name__)


@api.before_request
def postgres_supported_routes():
    # Do not expose private tables through legacy routes whose PostgreSQL
    # persistence/authorization has not been migrated and verified yet.
    supported = {'privacy_capabilities', 'demo', 'get_members', 'create_recommendation',
                 'get_recommendation', 'add_feedback', 'data_releases', 'data_release', 'data_quality_summary',
                 'create_session', 'cooking_create', 'cooking_get', 'cooking_change', 'cooking_complete'}
    if os.getenv('KINI_DB_BACKEND', 'sqlite').lower() == 'postgres' and request.endpoint.rsplit('.', 1)[-1] not in supported:
        return jsonify({'error': '이 API의 PostgreSQL 이전은 아직 완료되지 않았습니다.',
                        'status': 'not_implemented'}), 501


@api.after_request
def private_recommendation_no_cache(response):
    if os.getenv('KINI_DB_BACKEND', 'sqlite').lower() == 'postgres':
        response.headers['Cache-Control'] = 'no-store'
    return response


def postgres_identity(db, household_id=None, *, write=False):
    if not getattr(db, 'is_postgres', False):
        return None
    scheme, _, token = request.headers.get('Authorization', '').partition(' ')
    if scheme.lower() != 'bearer' or not token.strip():
        raise AuthenticationError('가구 세션이 필요합니다.')
    return authorize(db, token.strip(), household_id=household_id, write=write)


def postgres_cooking_identity(db, session_id, *, write=False):
    if getattr(db, 'is_postgres', False):
        postgres_identity(db, write=write)
        row = db.execute('SELECT household_id FROM cooking_sessions WHERE id=?', (session_id,)).fetchone()
        if not row:
            raise ValueError('조리 세션을 찾을 수 없습니다.')
        postgres_identity(db, row['household_id'], write=write)

def validated(model):
    return model.model_validate(request.get_json(silent=True) or {})

def errors(fn):
    from functools import wraps
    @wraps(fn)
    def wrapped(*args,**kwargs):
        try: return fn(*args,**kwargs)
        except ValidationError: return jsonify({'error':'요청 값의 형식을 확인해 주세요.'}),400
        except AuthenticationError: return jsonify({'error':'인증이 필요합니다.'}),401
        except AuthorizationError: return jsonify({'error':'가구 쓰기 권한이 필요합니다.'}),403
        except (ValueError, sqlite3.IntegrityError, ServiceError): return jsonify({'error':'요청을 처리할 수 없습니다. 입력과 제한 조건을 확인해 주세요.'}),400
    return wrapped

@api.get('/api/privacy/capabilities')
def privacy_capabilities():
    return jsonify({'delete_available':False,'export_available':False,'reason':'인증, 권한 및 암호화 정책이 필요합니다.'})

@api.get('/api/data-releases')
def data_releases():
    with open_db() as db:
        pointer=dict(db.execute('SELECT * FROM data_release_pointers WHERE id=1').fetchone())
        rows=[dict(row) for row in db.execute('''SELECT r.id,r.external_release_id,r.source_published_at,r.imported_at,r.checksum,r.status,r.record_count,r.accepted_count,r.quarantined_count,s.source_key,s.provider_name,s.dataset_name
            FROM dataset_releases r JOIN data_sources s ON s.id=r.data_source_id ORDER BY r.id DESC''')]
        for row in rows: row['is_current_published']=row['id']==pointer['nutrition_release_id']
        allergens=[dict(row) for row in db.execute('SELECT id,source_name,source_url,legal_or_reference_version,effective_date,imported_at,checksum,status FROM allergen_reference_versions ORDER BY id DESC')]
        for row in allergens: row['is_current_published']=row['id']==pointer['allergen_reference_version_id']
        return jsonify({'nutrition_releases':rows,'allergen_reference_versions':allergens,'current':pointer})

@api.get('/api/data-releases/<int:release_id>')
def data_release(release_id):
    with open_db() as db:
        row=db.execute('''SELECT r.id,r.external_release_id,r.source_published_at,r.imported_at,r.checksum,r.status,r.record_count,r.accepted_count,r.quarantined_count,r.notes,s.source_key,s.provider_name,s.dataset_name,s.source_url
            FROM dataset_releases r JOIN data_sources s ON s.id=r.data_source_id WHERE r.id=?''',(release_id,)).fetchone()
        if not row: return jsonify({'error':'release를 찾을 수 없습니다.'}),404
        pointer=db.execute('SELECT nutrition_release_id FROM data_release_pointers WHERE id=1').fetchone()
        value=dict(row); value['is_current_published']=value['id']==pointer['nutrition_release_id']
        return jsonify(value)

@api.get('/api/data-quality/summary')
def data_quality_summary():
    with open_db() as db:
        pointer=dict(db.execute('SELECT * FROM data_release_pointers WHERE id=1').fetchone())
        return jsonify({'current':pointer,'ingredients_by_source':[dict(r) for r in db.execute('SELECT source_type,COUNT(*) count FROM ingredients GROUP BY source_type')], 'official_food_items':db.execute('SELECT COUNT(*) FROM official_food_items').fetchone()[0], 'active_mappings':db.execute('SELECT COUNT(*) FROM ingredient_official_food_mappings WHERE active=1').fetchone()[0], 'note':'Product allergens and cross-contact are not inferred from nutrition imports.'})

@api.post('/api/inventory/normalize')
@errors
def inventory_normalize():
    obj=validated(InventoryNormalizeRequest)
    with open_db() as db:
        ingredient=normalize_ingredient(db,obj.name)
        conversion=convert_quantity(db,obj.quantity,obj.unit,ingredient['ingredient_id'])
        return jsonify({'ingredient':ingredient,'conversion':conversion,'requires_confirmation':ingredient['match_status']!='matched' or ingredient['warning'] is not None or conversion['quantity_g'] is None})

@api.get('/api/ingredients/search')
@errors
def ingredient_search():
    query=request.args.get('q','').strip()
    if not query or len(query)>150: raise ValueError('검색어를 입력해 주세요.')
    with open_db() as db:
        rows=db.execute('SELECT id,canonical_name,category,source_type,is_composite,requires_label_check FROM ingredients WHERE active=1 AND instr(canonical_name,?)>0 ORDER BY canonical_name,id LIMIT 30',(query,))
        return jsonify({'ingredients':[dict(row) for row in rows]})

@api.post('/api/inventory/items')
@errors
def inventory_add():
    obj=validated(InventoryItemRequest)
    for value in (obj.expiry_date,obj.opened_at):
        if value: datetime.strptime(value,'%Y-%m-%d')
    with open_db() as db:
        auth = request.headers.get('Authorization', '')
        if not auth.startswith('Bearer '): raise AuthenticationError('인증이 필요합니다.')
        identity = authorize(db, auth[7:].strip(), household_id=obj.household_id, write=True)
        if not db.execute('SELECT 1 FROM households WHERE id=?',(identity['household_id'],)).fetchone(): raise ValueError('가구가 없습니다.')
        ingredient=normalize_ingredient(db,obj.name)
        conversion=convert_quantity(db,obj.quantity,obj.unit,obj.ingredient_id)
        if ingredient['ingredient_id']!=obj.ingredient_id or ingredient['match_status'] not in ('matched','needs_label_check') or conversion['quantity_g'] is None: raise ValueError('재료와 중량을 확인해 주세요.')
        status='needs_confirmation' if obj.product_name or ingredient['match_status']=='needs_label_check' else 'verified'
        if status == 'verified':
            event = inventory_events.ingest(db, auth[7:].strip(), {
                'client_event_id': str(uuid4()), 'source': 'manual', 'action': 'stock_in',
                'detected_label': obj.name, 'confidence': 1.0,
            })
            event = inventory_events.confirm(db, auth[7:].strip(), event['event_id'], {
                'ingredient_id': obj.ingredient_id, 'quantity_g': conversion['quantity_g'],
                'expiry_date': obj.expiry_date,
            })
            item_id = event['inventory_item_id']
            db.execute('''UPDATE inventory_items SET original_name=?,quantity=?,unit=?,data_status=?,product_name=?,label_confirmed=?,expiry_date=?,use_by=?,opened_at=?,freshness_state=? WHERE id=?''',
                       (obj.name,obj.quantity,obj.unit,status,obj.product_name,int(obj.label_confirmed),obj.expiry_date,obj.expiry_date,obj.opened_at,obj.freshness_state,item_id))
        else:
            cur=db.execute('INSERT INTO inventory_items(household_id,ingredient_id,original_name,quantity,unit,quantity_g,data_status,product_name,label_confirmed,expiry_date,use_by,opened_at,freshness_state) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',(obj.household_id,obj.ingredient_id,obj.name,obj.quantity,obj.unit,conversion['quantity_g'],status,obj.product_name,int(obj.label_confirmed),obj.expiry_date,obj.expiry_date,obj.opened_at,obj.freshness_state))
            item_id=cur.lastrowid
        return jsonify({'id':item_id,'ingredient':ingredient,'conversion':conversion,'data_status':status,'requires_confirmation':status!='verified'}),201

@api.post('/api/voice/recommendations')
@errors
def voice_recommendation():
    uploaded=request.files.get('audio')
    suffix=Path(uploaded.filename or '').suffix.lower() if uploaded else ''
    if suffix not in {'.wav','.mp3','.m4a','.webm','.ogg','.mp4'}: raise ValueError('음성 파일을 확인해 주세요.')
    payload={'household_id':int(request.form['household_id']), 'participant_ids':json.loads(request.form['participant_ids']), 'meal_type':request.form['meal_type']}
    base=RecommendationRequest.model_validate(payload)
    temp_path=None
    try:
        with tempfile.NamedTemporaryFile(delete=False,suffix=suffix) as temp:
            temp_path=Path(temp.name); uploaded.save(temp)
        transcript=transcribe_audio(temp_path)
    finally:
        if temp_path: temp_path.unlink(missing_ok=True)
    try:
        context=f'선택된 가구 ID: {base.household_id}, 참여자 ID: {base.participant_ids}, 식사 유형: {base.meal_type}. 발화: {transcript}'
        parsed=parse_recommendation_request(context)
        data=parsed.model_dump()
        data.update({'household_id':base.household_id,'participant_ids':base.participant_ids,'meal_type':base.meal_type})
        parsed=RecommendationRequest.model_validate(data)
    except Exception:
        return jsonify({'transcript':transcript,'error':'음성 요청을 해석하지 못했습니다. 조건을 확인해 구조화된 추천 요청으로 다시 보내 주세요.'}),422
    with open_db() as db:
        result=recommend(db,parsed.model_dump(),persist=False)
        result=enrich_with_ai(parsed.model_dump(),result)
        result=persist_recommendation(db,parsed.model_dump(),result)
    # Transcript remains in the local API response for this legacy voice flow;
    # it is never included in the external-AI payload.
    result.update({'transcript':transcript,'interpreted_request':parsed.model_dump(),'interpretation':'ollama'})
    return jsonify(result),201

@api.post('/api/cooking-sessions')
@errors
def cooking_create():
    obj=validated(CookingSessionRequest)
    with closing(open_db()) as db, db:
        if getattr(db, 'is_postgres', False):
            postgres_identity(db, write=True)
            for table, resource_id in [('meal_sessions', obj.meal_session_id),
                                       ('recommendation_requests', obj.recommendation_request_id)]:
                row = db.execute(f'SELECT household_id FROM {table} WHERE id=?', (resource_id,)).fetchone()
                if not row:
                    raise ValueError('추천 또는 식사 세션을 찾을 수 없습니다.')
                postgres_identity(db, row['household_id'], write=True)
        return jsonify(cooking.create(db,obj)),201

@api.get('/api/cooking-sessions/<int:session_id>')
@errors
def cooking_get(session_id):
    with closing(open_db()) as db, db:
        postgres_cooking_identity(db, session_id)
        return jsonify(cooking.state(db,session_id))

@api.post('/api/cooking-sessions/<int:session_id>/ingredients')
@errors
def cooking_change(session_id):
    obj=validated(CookingChangeRequest)
    with closing(open_db()) as db, db:
        postgres_cooking_identity(db, session_id, write=True)
        return jsonify(cooking.change(db,session_id,obj))

@api.post('/api/cooking-sessions/<int:session_id>/complete')
@errors
def cooking_complete(session_id):
    obj = validated(CookingCompleteRequest)
    inventory_token = None
    if 'data_status' in current_app.blueprints or os.getenv('KINI_DB_BACKEND', 'sqlite').lower() == 'postgres':
        scheme, _, token = request.headers.get('Authorization', '').partition(' ')
        if scheme.lower() != 'bearer' or not token.strip():
            return jsonify({'error': '로컬 로그인 세션이 필요합니다.'}), 401
        inventory_token = token.strip()

    try:
        with closing(open_db()) as db, db:
            result = cooking.complete(db, session_id, obj, inventory_token=inventory_token)
    except PermissionError:
        return jsonify({'error': '세션 또는 가구 권한을 확인하세요.'}), 403
    except cooking.InventoryConfirmationRequired as error:
        return jsonify({
            'error': str(error),
            'code': 'inventory_confirmation_required',
            'ingredients': error.ingredients,
        }), 409
    return jsonify(result), 201

@api.post('/api/cooking-sessions/<int:session_id>/questions')
@errors
def cooking_question(session_id):
    obj=validated(CookingQuestionRequest)
    from .recommendation_rag import SQLiteRetriever
    with open_db() as db:
        cooking.state(db,session_id)
        question=obj.question
        if '스팸' in question or '반 통' in question:
            return jsonify({'answer':'제품 라벨의 중량과 재료를 확인한 뒤 실제 사용량을 g으로 입력해 주세요. 확인 전에는 영양이나 알레르기 안전을 확정할 수 없습니다.','sources':[],'requires_confirmation':True})
        if '빼' in question or '제외' in question:
            return jsonify({'answer':'제외할 재료를 선택하고 수량을 0g으로 입력해 주세요. 변경 전 제한 조건과 영양을 다시 검사합니다.','sources':[],'requires_confirmation':True})
        if '짜' in question:
            query='저염 조리'
            answer='소스를 더 넣기 전에 맛을 확인하고, 조절할 재료와 양은 먼저 확인해 주세요.'
        elif '보관' in question:
            query='보관 상태 확인'; answer='제품의 보관 안내를 확인하고 상태가 의심되면 사용하지 마세요.'
        else:
            query='조리 FAQ'; answer='조리 중에는 생재료와 조리된 음식의 도구를 분리해 주세요.'
        try: sources=SQLiteRetriever(db).search(query)
        except Exception: sources=[]
        return jsonify({'answer':answer,'sources':sources,'requires_confirmation':False})

@api.get('/api/households/demo')
def demo():
    with closing(open_db()) as db:
        private = getattr(db, 'is_postgres', False) or db.execute("SELECT 1 FROM metadata WHERE key='dataset_mode' AND value='private'").fetchone()
        managed = db.execute('SELECT 1 FROM account_households WHERE household_id=1').fetchone()
        if private or managed:
            return jsonify({'error': '데모 가족을 사용할 수 없습니다.'}), 404
        return jsonify({'id':1,'name':'데모 가족','members':members(db,1)})

@api.get('/api/members')
@errors
def get_members():
    household_id=int(request.args.get('household_id',1))
    with closing(open_db()) as db:
        managed = db.execute('SELECT 1 FROM account_households WHERE household_id=?', (household_id,)).fetchone()
        private = getattr(db, 'is_postgres', False) or db.execute("SELECT 1 FROM metadata WHERE key='dataset_mode' AND value='private'").fetchone()
        if managed or private:
            scheme, _, token = request.headers.get('Authorization', '').partition(' ')
            if scheme.lower() != 'bearer' or not token.strip():
                return jsonify({'error': '로컬 로그인 세션이 필요합니다.'}), 401
            from .local_identity import authorize
            try:
                authorize(db, token.strip(), household_id=household_id)
            except PermissionError:
                return jsonify({'error': '세션 또는 가구 권한을 확인하세요.'}), 403
        return jsonify({'members':members(db,household_id)})

@api.post('/api/meal-sessions')
@errors
def create_session():
    obj=validated(MealSessionRequest); ids=sorted(set(obj.participant_ids))
    with closing(open_db()) as db, db:
        postgres_identity(db, obj.household_id, write=True)
        if len(members(db,obj.household_id,ids))!=len(ids): raise ValueError('해당 가구의 참여자를 선택하세요.')
        cur=db.execute('INSERT INTO meal_sessions(household_id,meal_type) VALUES (?,?)',(obj.household_id,obj.meal_type))
        db.executemany('INSERT INTO meal_session_participants VALUES (?,?)',[(cur.lastrowid,i) for i in ids])
        if getattr(db, 'is_postgres', False):
            db.execute('INSERT INTO meal_data_context SELECT ?,source_code,release_id FROM active_data_releases',
                       (cur.lastrowid,))
        return jsonify({'session_id':cur.lastrowid,'household_id':obj.household_id,'meal_type':obj.meal_type,'participant_ids':ids}),201

@api.post('/api/recommendations')
@errors
def create_recommendation():
    # Check the session before natural-language parsing can invoke a model.
    if os.getenv('KINI_DB_BACKEND', 'sqlite').lower() == 'postgres':
        with closing(open_db()) as db, db:
            postgres_identity(db, write=True)
    payload=request.get_json(silent=True) or {}
    if 'text' in payload:
        try: obj=parse_recommendation_request(payload['text'])
        except Exception: return jsonify({'error':'자연어 해석에 실패했습니다. 구조화된 요청을 입력해 주세요.'}),400
    else: obj=RecommendationRequest.model_validate(payload)
    with closing(open_db()) as db, db:
        postgres_identity(db, obj.household_id, write=True)
        result=recommend(db,obj.model_dump(),persist=False)
        result=enrich_with_ai(obj.model_dump(),result)
        result=persist_recommendation(db,obj.model_dump(),result)
    return jsonify(result),201

@api.get('/api/recommendations/<int:request_id>')
@errors
def get_recommendation(request_id):
    from .recommendation_provider import catalog_context
    with closing(open_db()) as db, db:
        postgres_identity(db)
        row=db.execute('SELECT result_json,nutrition_data_release_id,allergen_reference_version_id,household_id FROM recommendation_requests WHERE id=?',(request_id,)).fetchone()
        if row:
            postgres_identity(db, row['household_id'])
        pointer=db.execute('SELECT nutrition_release_id,allergen_reference_version_id FROM data_release_pointers WHERE id=1').fetchone()
        current_catalog = catalog_context(db)
    if not row: return jsonify({'error':'추천 요청을 찾을 수 없습니다.'}),404
    result=json.loads(row[0])
    if result.pop('_catalog_context', None) != current_catalog and result.get('status') == 'recommendations_available':
        result.update(status='stale_unvalidated', recommendations=[], requires_confirmation=True)
    if result.get('status') == 'recommendations_available' and not result.get('ai_explanation'):
        result.update({'status':'stale_unvalidated','recommendations':[], 'requires_confirmation':True, 'message':'저장된 추천은 현재 정보로 다시 검증해야 합니다. 다시 추천받아 주세요.'})
    if 'data_quality' not in result:
        result.update({'status':'stale_unvalidated','data_quality':'confirmation_required','requires_confirmation':True,'warnings':['이전 규칙으로 저장된 결과입니다. 현재 정보로 다시 추천받으세요.']})
        for candidate in result.get('recommendations',[]):
            candidate.update({'data_quality':'confirmation_required','requires_confirmation':True,'warnings':['현재 기준으로 재검증되지 않았습니다.']})
    elif result.get('status') == 'recommendations_available' and (row['nutrition_data_release_id'] != pointer['nutrition_release_id'] or row['allergen_reference_version_id'] != pointer['allergen_reference_version_id']):
        result.update({'status':'stale_unvalidated','recommendations':[],'requires_confirmation':True})
        result.setdefault('warnings',[]).append('Published reference release changed; rerun the recommendation to revalidate.')
    return jsonify(result)

@api.post('/api/meal-records')
@errors
def create_record():
    obj=validated(MealRecordRequest)
    datetime.fromisoformat(obj.eaten_at.replace('Z','+00:00'))
    with open_db() as db:
        participants={r[0] for r in db.execute('SELECT member_id FROM meal_session_participants WHERE session_id=?',(obj.session_id,))}
        if not participants or len({i.member_id for i in obj.items})!=len(obj.items) or not all(i.member_id in participants for i in obj.items): raise ValueError('식사 기록 구성원은 중복 없는 식사 참여자여야 합니다.')
        recipe=db.execute('SELECT servings FROM recipes WHERE id=?',(obj.recipe_id,)).fetchone()
        if not recipe: raise ValueError('레시피를 찾을 수 없습니다.')
        rows=db.execute('SELECT ri.quantity_g,n.* FROM recipe_ingredients ri LEFT JOIN ingredient_nutrition n ON n.ingredient_id=ri.ingredient_id WHERE ri.recipe_id=?',(obj.recipe_id,)).fetchall()
        if not rows or any(not r['basis_amount_g'] or r['basis_amount_g']<=0 or r['quantity_g'] is None or any(r[n] is None for n in NUTRIENTS) for r in rows): raise ValueError('영양 정보가 부족합니다.')
        totals={n:sum(r[n]*r['quantity_g']/r['basis_amount_g'] for r in rows)/recipe['servings'] for n in NUTRIENTS}
        cur=db.execute('INSERT INTO meal_records(session_id,recipe_id,eaten_at) VALUES (?,?,?)',(obj.session_id,obj.recipe_id,obj.eaten_at))
        for item in obj.items:
            rec=db.execute('INSERT INTO meal_record_items(record_id,member_id,servings) VALUES (?,?,?)',(cur.lastrowid,item.member_id,item.servings))
            db.executemany('INSERT INTO nutrition_intakes VALUES (?,?,?)',[(rec.lastrowid,n,round(v*item.servings,2)) for n,v in totals.items()])
            household_id = db.execute('SELECT household_id FROM meal_sessions WHERE id=?', (obj.session_id,)).fetchone()[0]
            record_recipe(db, household_id, item.member_id, 'meal_record', obj.recipe_id)
        aggregate(db, household_id)
        update_summary(db, household_id)
        return jsonify({'record_id':cur.lastrowid,'nutrition_per_serving':totals}),201

@api.post('/api/conversations')
@errors
def create_conversation():
    obj=validated(ConversationRequest)
    with open_db() as db:
        if not db.execute('SELECT 1 FROM households WHERE id=?', (obj.household_id,)).fetchone(): raise ValueError('가구를 찾을 수 없습니다.')
        if obj.meal_session_id and not db.execute('SELECT 1 FROM meal_sessions WHERE id=? AND household_id=?', (obj.meal_session_id,obj.household_id)).fetchone(): raise ValueError('해당 가구의 식사 세션이 아닙니다.')
        cur=db.execute('INSERT INTO conversation_sessions(household_id,meal_session_id,purpose) VALUES (?,?,?)', (obj.household_id,obj.meal_session_id,obj.purpose))
        return jsonify({'session_id':cur.lastrowid, **obj.model_dump()}),201

@api.post('/api/conversations/<int:session_id>/messages')
@errors
def add_message(session_id):
    obj=validated(MessageRequest)
    with open_db() as db:
        session=db.execute('SELECT * FROM conversation_sessions WHERE id=? AND closed_at IS NULL', (session_id,)).fetchone()
        if not session: return jsonify({'error':'대화 세션을 찾을 수 없습니다.'}),404
        household_id=session['household_id']
        if obj.member_id and not members(db,household_id,[obj.member_id]): raise ValueError('해당 가구의 구성원이 아닙니다.')
        if obj.member_id and session['meal_session_id'] and not db.execute('SELECT 1 FROM meal_session_participants WHERE session_id=? AND member_id=?', (session['meal_session_id'],obj.member_id)).fetchone(): raise ValueError('식사 참여자가 아닙니다.')
        names=[r[0] for r in db.execute('SELECT name FROM ingredients')]
        try: signal=extract_preference_signal(obj.content,names)
        except Exception: signal=None
        # Raw messages may contain medical or other sensitive data; retain only a marker.
        cur=db.execute('INSERT INTO conversation_messages(session_id,role,content) VALUES (?,?,?)', (session_id,'user','[원문 미보관]'))
        evidence=[]
        if signal:
            eid=add_evidence(db,household_id,obj.member_id,'conversation',signal.feature_type,signal.feature_value,signal.signal)
            evidence=[{'id':eid, **signal.model_dump()}]
            aggregate(db,household_id)
        return jsonify({'message_id':cur.lastrowid,'evidence':evidence,'ignored_reason':None if evidence else '명확한 음식 선호만 저장합니다.'}),201

@api.post('/api/recommendations/<int:request_id>/feedback')
@errors
def add_feedback(request_id):
    obj=validated(FeedbackRequest)
    with closing(open_db()) as db, db:
        postgres_identity(db, write=True)
        row=db.execute('SELECT household_id,request_json,result_json FROM recommendation_requests WHERE id=?', (request_id,)).fetchone()
        if not row: return jsonify({'error':'추천 요청을 찾을 수 없습니다.'}),404
        postgres_identity(db, row['household_id'], write=True)
        stored=json.loads(row['result_json'])
        if stored.get('status') != 'recommendations_available' or not stored.get('ai_explanation'): raise ValueError('확인된 추천이 아닙니다.')
        if not db.execute('SELECT 1 FROM recommendation_candidates WHERE request_id=? AND recipe_id=?', (request_id,obj.candidate_recipe_id)).fetchone(): raise ValueError('해당 요청에서 표시된 후보가 아닙니다.')
        if obj.member_id and obj.member_id not in json.loads(row['request_json'])['participant_ids']: raise ValueError('추천에 참여하지 않은 구성원입니다.')
        if obj.action=='rated' and obj.rating is None: raise ValueError('별점이 필요합니다.')
        if obj.action!='rated' and obj.rating is not None: raise ValueError('별점은 rated에만 사용할 수 있습니다.')
        features=recipe_features(db,obj.candidate_recipe_id)
        if obj.reason_code:
            parts=obj.reason_code.split(':',1)
            if len(parts)!=2 or tuple(parts) not in features: raise ValueError('이유 코드는 후보의 ingredient:이름 또는 recipe:ID여야 합니다.')
        # Free text can contain health information. It is intentionally not persisted.
        cur=db.execute('INSERT INTO recommendation_feedback(recommendation_request_id,candidate_recipe_id,member_id,action,rating,reason_code,reason_text) VALUES (?,?,?,?,?,?,NULL)', (request_id,obj.candidate_recipe_id,obj.member_id,obj.action,obj.rating,obj.reason_code))
        evidence=[]
        if obj.action in ('selected','cooked'):
            evidence=record_recipe(db,row['household_id'],obj.member_id,'recommendation_feedback',obj.candidate_recipe_id)
        elif obj.action=='rejected' and obj.reason_code:
            feature,value=obj.reason_code.split(':',1)
            evidence=[add_evidence(db,row['household_id'],obj.member_id,'recommendation_feedback',feature,value,'avoid')]
        aggregate(db,row['household_id']); update_summary(db,row['household_id'])
        return jsonify({'feedback_id':cur.lastrowid,'evidence_ids':evidence}),201

@api.get('/api/preferences')
@errors
def get_preferences():
    household_id=int(request.args['household_id'])
    member_id=int(request.args['member_id']) if request.args.get('member_id') else None
    with open_db() as db:
        if not db.execute('SELECT 1 FROM households WHERE id=?',(household_id,)).fetchone(): raise ValueError('가구를 찾을 수 없습니다.')
        if member_id and not members(db,household_id,[member_id]): raise ValueError('해당 가구의 구성원이 아닙니다.')
        aggregate(db,household_id)
        rows=db.execute("SELECT * FROM inferred_preferences WHERE household_id=? AND member_id IS ? AND status='active' ORDER BY id", (household_id,member_id))
        return jsonify({'preferences':[dict(r) for r in rows]})

@api.post('/api/preferences/<int:preference_id>/dismiss')
@errors
def dismiss_preference(preference_id):
    with open_db() as db:
        cur=db.execute("UPDATE inferred_preferences SET status='dismissed' WHERE id=?", (preference_id,))
        if not cur.rowcount: return jsonify({'error':'추론 선호를 찾을 수 없습니다.'}),404
        return jsonify({'preference_id':preference_id,'status':'dismissed'})

@api.get('/api/household-behavior-summary')
@errors
def behavior_summary():
    household_id=int(request.args['household_id'])
    with open_db() as db:
        if not db.execute('SELECT 1 FROM households WHERE id=?',(household_id,)).fetchone(): raise ValueError('가구를 찾을 수 없습니다.')
        update_summary(db,household_id)
        summary=dict(db.execute('SELECT * FROM household_behavior_summary WHERE household_id=?',(household_id,)).fetchone())
        for key in ('preferred_meal_types_json','frequently_used_ingredients_json','recently_rejected_features_json'):
            summary[key]=json.loads(summary[key] or '[]')
        return jsonify(summary)

@api.post('/api/meal-records/<int:record_id>/adjustments')
@errors
def cooking_adjustment(record_id):
    obj=validated(CookingAdjustmentRequest)
    with open_db() as db:
        record=db.execute('SELECT s.household_id, m.recipe_id, m.session_id FROM meal_records m JOIN meal_sessions s ON s.id=m.session_id WHERE m.id=?',(record_id,)).fetchone()
        if not record: return jsonify({'error':'식사 기록을 찾을 수 없습니다.'}),404
        if obj.member_id and not db.execute('SELECT 1 FROM meal_record_items WHERE record_id=? AND member_id=?',(record_id,obj.member_id)).fetchone(): raise ValueError('식사 기록의 구성원이 아닙니다.')
        ingredient=db.execute('SELECT id FROM ingredients WHERE name=?',(obj.ingredient,)).fetchone()
        if not ingredient: raise ValueError('등록된 재료가 아닙니다.')
        present=db.execute('SELECT 1 FROM recipe_ingredients WHERE recipe_id=? AND ingredient_id=?',(record['recipe_id'],ingredient['id'])).fetchone()
        if bool(present) != (obj.action=='removed'): raise ValueError('원래 레시피의 재료 구성과 변경 내용이 맞지 않습니다.')
        eid=add_evidence(db,record['household_id'],obj.member_id,'cooking_adjustment','ingredient',obj.ingredient,'prefer' if obj.action=='added' else 'avoid',strength=.4)
        aggregate(db,record['household_id']); update_summary(db,record['household_id'])
        return jsonify({'evidence_id':eid,'notice':'선호 신호만 기록했습니다. 식사 영양 수치는 재계산하지 않았습니다.'}),201
