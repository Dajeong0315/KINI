"""Validated cooking drafts; all ingredient quantities are recipe-total grams."""
import json
from collections import defaultdict
from datetime import date, datetime
from .freshness import assess_inventory
from .recommendation_repository import members
from .recommendation_engine import NUTRIENTS
from .personalization import add_evidence, aggregate, update_summary
from .recommendation_provider import RecommendationNutritionProvider
from .data_quality import inspect_ingredient


def state(db, session_id):
    row = db.execute('SELECT * FROM cooking_sessions WHERE id=?', (session_id,)).fetchone()
    if not row: raise ValueError('조리 세션을 찾을 수 없습니다.')
    result = dict(row)
    result['ingredients'] = json.loads(result.pop('ingredients_json'))
    result['nutrition_per_serving'] = validate(db, result)
    result['nutrition_confidence'] = '데모 추정치'
    people=members(db,result['household_id'],[r[0] for r in db.execute('SELECT member_id FROM meal_session_participants WHERE session_id=?',(result['meal_session_id'],))])
    warnings=[]; demo=False
    for entry in result['ingredients']:
        inventory=[dict(r) for r in db.execute('SELECT * FROM inventory_items WHERE household_id=? AND ingredient_id=?',(result['household_id'],entry['ingredient_id']))]
        _, flags, quality=inspect_ingredient(db,entry['ingredient_id'],people,inventory,RecommendationNutritionProvider())
        warnings.extend(flags)
        demo=demo or bool(quality and (quality['source_type']=='demo' or quality['nutrition_source']=='demo'))
    result.update({'warnings':sorted(set(warnings)),'requires_confirmation':bool(warnings),'data_quality':'demo_data' if demo else 'confirmation_required' if warnings else 'verified'})
    return result


def validate(db, session):
    recipe = db.execute('SELECT servings FROM recipes WHERE id=?', (session['recipe_id'],)).fetchone()
    ids = [r[0] for r in db.execute('SELECT member_id FROM meal_session_participants WHERE session_id=? ORDER BY member_id', (session['meal_session_id'],))]
    portions = session.get('planned_servings') or recipe['servings']
    if not portions or portions <= 0: raise ValueError('조리 인분 확인이 필요합니다.')
    people = members(db, session['household_id'], ids)
    if not people or len(people)!=len(ids): raise ValueError('식사 참여자 정보가 부족합니다.')
    nutrients = {n:0.0 for n in NUTRIENTS}
    if not session['ingredients']: raise ValueError('재료가 없습니다.')
    for entry in session['ingredients']:
        item = db.execute('SELECT i.*,n.energy_kcal,n.protein_g,n.carbohydrate_g,n.fat_g,n.sodium_mg FROM ingredients i LEFT JOIN ingredient_nutrition n ON n.ingredient_id=i.id WHERE i.id=?',(entry['ingredient_id'],)).fetchone()
        if not item or entry['quantity_g'] <= 0: raise ValueError('재료 수량 또는 영양 정보 확인이 필요합니다.')
        for person in people:
            if item['name'] in person['allergies'] or item['category'] in person['allergies']: raise ValueError('참여자 알레르기 재료는 사용할 수 없습니다.')
            for constraint in person['constraints']:
                if constraint['kind'] not in ('excluded_ingredient','meal_sodium_max_mg'): raise ValueError('지원하지 않는 건강 제한이 있습니다.')
                if constraint['kind']=='excluded_ingredient' and constraint['value'] in (item['name'],item['category']): raise ValueError('참여자의 식이 제한에 해당합니다.')
        inventory = [dict(r) for r in db.execute('SELECT * FROM inventory_items WHERE household_id=? AND ingredient_id=?',(session['household_id'],item['id']))]
        codes,_,quality=inspect_ingredient(db,item['id'],people,inventory,RecommendationNutritionProvider())
        if codes: raise ValueError('재료의 알레르기, 라벨 또는 영양 정보 확인이 필요합니다.')
        guideline_row=db.execute('SELECT * FROM ingredient_storage_guidelines WHERE ingredient_id=? ORDER BY rowid DESC LIMIT 1',(item['id'],)).fetchone()
        guideline=dict(guideline_row) if guideline_row else None
        if any(not assess_inventory(x,guideline=guideline)['usable'] for x in inventory): raise ValueError('재료의 상태 또는 소비기한 확인이 필요합니다.')
        for n in NUTRIENTS: nutrients[n] += quality[n]*entry['quantity_g']/quality['basis_amount_g']/portions
    for person in people:
        for constraint in person['constraints']:
            if constraint['kind']=='meal_sodium_max_mg' and nutrients['sodium_mg']>float(constraint['value']): raise ValueError('참여자의 식사 나트륨 제한을 초과합니다.')
    return {n:round(v,2) for n,v in nutrients.items()}


def create(db, obj):
    meal = db.execute('SELECT * FROM meal_sessions WHERE id=?',(obj.meal_session_id,)).fetchone()
    request = db.execute('SELECT * FROM recommendation_requests WHERE id=?',(obj.recommendation_request_id,)).fetchone()
    if not meal or not request or meal['household_id']!=request['household_id']: raise ValueError('같은 가구의 추천과 식사 세션이 필요합니다.')
    stored = json.loads(request['result_json'])
    if stored.get('status') != 'recommendations_available' or not stored.get('ai_explanation'): raise ValueError('확인된 추천이 아닙니다.')
    ids = {r[0] for r in db.execute('SELECT member_id FROM meal_session_participants WHERE session_id=?',(obj.meal_session_id,))}
    if ids != set(json.loads(request['request_json'])['participant_ids']): raise ValueError('추천과 식사 참여자가 일치해야 합니다.')
    if not db.execute('SELECT 1 FROM recommendation_candidates WHERE request_id=? AND recipe_id=?',(obj.recommendation_request_id,obj.recipe_id)).fetchone(): raise ValueError('표시된 추천 후보가 아닙니다.')
    ingredients = [dict(r) for r in db.execute('SELECT ingredient_id,quantity_g FROM recipe_ingredients WHERE recipe_id=? ORDER BY ingredient_id',(obj.recipe_id,))]
    draft={'recipe_id':obj.recipe_id,'meal_session_id':obj.meal_session_id,'household_id':meal['household_id'],'ingredients':ingredients}
    base = db.execute('SELECT servings FROM recipes WHERE id=?',(obj.recipe_id,)).fetchone()[0]
    if not base or base <= 0: raise ValueError('레시피 인분 확인이 필요합니다.')
    draft['planned_servings'] = len(ids)
    for entry in ingredients:
        entry['quantity_g'] *= len(ids) / base
    validate(db,draft)
    cur=db.execute('INSERT INTO cooking_sessions(household_id,meal_session_id,request_id,recipe_id,ingredients_json,planned_servings) VALUES (?,?,?,?,?,?)',(meal['household_id'],obj.meal_session_id,obj.recommendation_request_id,obj.recipe_id,json.dumps(ingredients),len(ids)))
    return state(db,cur.lastrowid)


def change(db, session_id, obj):
    session=state(db,session_id)
    if session['status']!='active': raise ValueError('완료된 조리 세션은 수정할 수 없습니다.')
    ingredient=db.execute('SELECT id FROM ingredients WHERE name=?',(obj.ingredient,)).fetchone()
    if not ingredient: raise ValueError('등록된 재료가 아닙니다. 제품과 무게를 확인해 주세요.')
    old=next((x for x in session['ingredients'] if x['ingredient_id']==ingredient['id']),None)
    if old is None and obj.quantity_g==0: raise ValueError('재료가 조리안에 없습니다.')
    session['ingredients']=[x for x in session['ingredients'] if x['ingredient_id']!=ingredient['id']]
    if obj.quantity_g: session['ingredients'].append({'ingredient_id':ingredient['id'],'quantity_g':obj.quantity_g})
    session['ingredients'].sort(key=lambda x:x['ingredient_id'])
    validate(db,session)
    db.execute('UPDATE cooking_sessions SET ingredients_json=? WHERE id=?',(json.dumps(session['ingredients']),session_id))
    if (old is None and obj.quantity_g) or (old and obj.quantity_g==0):
        add_evidence(db,session['household_id'],None,'cooking_adjustment','ingredient',obj.ingredient,'prefer' if obj.quantity_g else 'avoid',strength=.4)
        aggregate(db,session['household_id']); update_summary(db,session['household_id'])
    return state(db,session_id)


class InventoryConfirmationRequired(ValueError):
    def __init__(self, message, ingredients):
        super().__init__(message)
        self.ingredients = ingredients


def _link_confirmed_outflows(db, session, event_ids):
    ingredients = session['ingredients']
    if not event_ids or len(set(event_ids)) != len(event_ids):
        raise InventoryConfirmationRequired('확정된 출고 이벤트를 각각 한 번씩 선택하세요.', ingredients)

    placeholders = ','.join('?' for _ in event_ids)
    rows = db.execute(f"""
        SELECT e.id, c.ingredient_id, c.quantity_g
        FROM inventory_events e
        JOIN inventory_event_changes c ON c.event_id=e.id
        WHERE e.household_id=? AND e.status='applied' AND e.action='stock_out'
          AND e.id IN ({placeholders})
        """, (session['household_id'], *event_ids)).fetchall()
    if len(rows) != len(event_ids):
        raise InventoryConfirmationRequired('같은 가구의 확정 출고 내역을 확인하세요.', ingredients)

    withdrawn = defaultdict(float)
    for row in rows:
        withdrawn[row['ingredient_id']] += row['quantity_g']
    required = {item['ingredient_id']: item['quantity_g'] for item in ingredients}
    if withdrawn.keys() != required.keys() or any(
        withdrawn[ingredient_id] + 1e-9 < quantity_g for ingredient_id, quantity_g in required.items()
    ):
        raise InventoryConfirmationRequired('조리에 사용한 재료와 출고 중량을 확인하세요.', ingredients)

    # Only B owns these links. Claims and the meal record commit in the same transaction.
    db.execute("""
        CREATE TABLE IF NOT EXISTS cooking_inventory_event_links (
            event_id INTEGER PRIMARY KEY REFERENCES inventory_events(id),
            cooking_session_id INTEGER NOT NULL REFERENCES cooking_sessions(id)
        )
    """)
    if db.execute(
        f'SELECT 1 FROM cooking_inventory_event_links WHERE event_id IN ({placeholders})', event_ids
    ).fetchone():
        raise InventoryConfirmationRequired('이미 조리 기록에 연결된 출고 이벤트가 있습니다.', ingredients)
    db.executemany(
        'INSERT INTO cooking_inventory_event_links (event_id,cooking_session_id) VALUES (?,?)',
        [(event_id, session['id']) for event_id in event_ids],
    )


def complete(db, session_id, obj, *, inventory_token=None):
    datetime.fromisoformat(obj.eaten_at.replace('Z','+00:00'))
    # Serialize stock checks and session completion across concurrent requests.
    db.execute('BEGIN IMMEDIATE')
    if inventory_token is not None:
        from .local_identity import authorize

        identity = authorize(db, inventory_token, write=True)
        if not db.execute(
            'SELECT 1 FROM cooking_sessions WHERE id=? AND household_id=?',
            (session_id, identity['household_id']),
        ).fetchone():
            raise PermissionError('조리 세션의 가구 권한을 확인하세요.')
    session=state(db,session_id)
    if session['status']!='active': raise ValueError('이미 완료된 조리 세션입니다.')
    participants={r[0] for r in db.execute('SELECT member_id FROM meal_session_participants WHERE session_id=?',(session['meal_session_id'],))}
    if len({x.member_id for x in obj.items})!=len(obj.items) or not all(x.member_id in participants for x in obj.items): raise ValueError('식사 참여자를 중복 없이 선택해 주세요.')
    for item in obj.items:
        person=members(db,session['household_id'],[item.member_id])[0]
        for constraint in person['constraints']:
            if constraint['kind']=='meal_sodium_max_mg' and session['nutrition_per_serving']['sodium_mg']*item.servings>float(constraint['value']): raise ValueError('개인 섭취량이 나트륨 제한을 초과합니다.')
    if inventory_token is not None:
        _link_confirmed_outflows(db, session, obj.inventory_event_ids)
    elif obj.inventory_event_ids:
        raise InventoryConfirmationRequired('출고 이벤트를 사용할 수 없는 서버입니다.', session['ingredients'])
    else:
        raise InventoryConfirmationRequired('A 출고 확정이 필요합니다.', session['ingredients'])
    cur=db.execute('INSERT INTO meal_records(session_id,recipe_id,eaten_at) VALUES (?,?,?)',(session['meal_session_id'],session['recipe_id'],obj.eaten_at))
    for item in obj.items:
        rec=db.execute('INSERT INTO meal_record_items(record_id,member_id,servings) VALUES (?,?,?)',(cur.lastrowid,item.member_id,item.servings))
        db.executemany('INSERT INTO nutrition_intakes VALUES (?,?,?)',[(rec.lastrowid,n,round(v*item.servings,2)) for n,v in session['nutrition_per_serving'].items()])
        if item.servings == 0: continue
        for ingredient in session['ingredients']:
            name=db.execute('SELECT name FROM ingredients WHERE id=?',(ingredient['ingredient_id'],)).fetchone()[0]
            add_evidence(db,session['household_id'],item.member_id,'meal_record','ingredient',name,'prefer',observed_at=obj.eaten_at)
        add_evidence(db,session['household_id'],item.member_id,'meal_record','recipe',str(session['recipe_id']),'prefer',observed_at=obj.eaten_at)
    aggregate(db,session['household_id']); update_summary(db,session['household_id'])
    db.execute("UPDATE cooking_sessions SET status='completed',completed_at=CURRENT_TIMESTAMP,meal_record_id=? WHERE id=?",(cur.lastrowid,session_id))
    return {'record_id':cur.lastrowid,'nutrition_per_serving':session['nutrition_per_serving'],'ingredients':session['ingredients']}
