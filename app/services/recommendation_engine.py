from __future__ import annotations
import json
import hashlib
import math
from datetime import date
from .recommendation_repository import members
from .personalization import aggregate, score_personalization
from .freshness import assess_inventory
from .recommendation_llm import PROMPT_VERSION
from .data_quality import inspect_ingredient

RULES_VERSION='1.3-data-quality'
NUTRIENTS=('energy_kcal','protein_g','carbohydrate_g','fat_g','sodium_mg')

def recommend(db, req, *, persist=True):
    release_pointer=db.execute('SELECT nutrition_release_id,allergen_reference_version_id FROM data_release_pointers WHERE id=1').fetchone()
    nutrition_release_id=release_pointer['nutrition_release_id'] if release_pointer else None
    allergen_reference_version_id=release_pointer['allergen_reference_version_id'] if release_pointer else None
    ids=sorted(set(req['participant_ids']))
    people=members(db,req['household_id'],ids)
    if not ids or len(people)!=len(ids): raise ValueError('참여자는 해당 가구의 구성원이어야 합니다.')
    for person in people:
        if any(c['kind'] not in ('meal_sodium_max_mg', 'excluded_ingredient') for c in person['constraints']):
            raise ValueError('지원하지 않는 건강 제한이 있어 안전하게 추천할 수 없습니다.')
    inventory=[dict(r) for r in db.execute('SELECT inventory_items.*, ingredients.name FROM inventory_items JOIN ingredients ON ingredients.id=ingredient_id WHERE household_id=? ORDER BY inventory_items.id',(req['household_id'],))]
    today=date.today().isoformat()
    aggregate(db, req['household_id'])
    remaining={}
    for p in people:
        consumed={r['nutrient']:r['total'] for r in db.execute('SELECT nutrient,SUM(amount) total FROM nutrition_intakes JOIN meal_record_items ON meal_record_items.id=record_item_id JOIN meal_records ON meal_records.id=record_id WHERE member_id=? AND substr(eaten_at,1,10)=? GROUP BY nutrient',(p['id'],today))}
        remaining[p['id']]={n:round(max(0,v-consumed.get(n,0)),2) for n,v in p['targets'].items()}
    candidates=[]; excluded=[]
    for recipe_row in db.execute('SELECT * FROM recipes ORDER BY id'):
        recipe=dict(recipe_row)
        if recipe['id'] in req.get('excluded_recipe_ids', []):
            excluded.append({'recipe_id':recipe['id'],'name':recipe['name'],'reasons':['이전에 표시한 메뉴 제외']})
            continue
        if not recipe['servings'] or recipe['servings']<=0:
            excluded.append({'recipe_id':recipe['id'],'name':recipe['name'],'reasons':['인분 확인 필요'],'reason_codes':['unit_confirmation_required']})
            continue
        ingredients=[dict(r) for r in db.execute('SELECT ri.quantity_g,i.id,i.name,i.category,n.energy_kcal,n.protein_g,n.carbohydrate_g,n.fat_g,n.sodium_mg FROM recipe_ingredients ri JOIN ingredients i ON i.id=ri.ingredient_id LEFT JOIN ingredient_nutrition n ON n.ingredient_id=i.id WHERE recipe_id=? ORDER BY i.id',(recipe['id'],))]
        reasons=[]; reason_codes=[]; warnings=[]; qualities=[]; usage=[]; missing=0; urgency=0
        for item in ingredients:
            name=item['name']; matching=[x for x in inventory if x['ingredient_id']==item['id']]
            codes, item_warnings, quality=inspect_ingredient(db,item['id'],people,matching)
            reason_codes.extend(codes); warnings.extend(item_warnings)
            if quality: qualities.append(quality)
            if codes: reasons.extend({'allergen_conflict':f'{name}: 알레르기','label_confirmation_required':f'{name}: 성분표 확인 필요','nutrition_unknown':f'{name}: 영양 정보 부족','ingredient_unknown':f'{name}: 재료 정보 부족'}.get(code,code) for code in codes)
            if item['quantity_g'] is None or not math.isfinite(item['quantity_g']) or item['quantity_g'] <= 0:
                reasons.append(f'{name}: 중량 확인 필요'); reason_codes.append('unit_confirmation_required'); continue
            guideline_row=db.execute('SELECT * FROM ingredient_storage_guidelines WHERE ingredient_id=? ORDER BY rowid DESC LIMIT 1',(item['id'],)).fetchone()
            guideline=dict(guideline_row) if guideline_row else None
            assessed=[(x, assess_inventory(x, date.fromisoformat(today), guideline)) for x in matching]
            safe=[x for x,assessment in assessed if assessment['usable']]
            if matching and len(safe)!=len(matching): reasons.append(f'{name}: 안전 또는 기한 확인 필요'); reason_codes.append('food_safety_confirmation_required')
            available=sum(x['quantity_g'] or 0 for x in safe)
            needed=item['quantity_g']*len(ids)/recipe['servings']
            missing+=max(0,needed-available)
            priority=max((assessment['priority'] for _,assessment in assessed if assessment['usable']),default=0)
            urgency+=priority*min(needed/available,1) if available else 0
            usage.append({'ingredient':name,'needed_g':round(needed,2),'available_g':available,'priority':priority, 'uncertainty_flags':sorted({flag for _,assessment in assessed for flag in assessment['flags']})})
            if name in req.get('excluded_ingredients',[]): reasons.append(f'{name}: 사용자 제외'); reason_codes.append('user_exclusion')
            for p in people:
                if name in p['allergies'] or item['category'] in p['allergies']: reasons.append(f'{name}: 알레르기')
                for c in p['constraints']:
                    if c['kind']=='excluded_ingredient' and c['value'] in (name,item['category']): reasons.append(f'{name}: 식이 제한'); reason_codes.append('dietary_constraint')
        if not ingredients or any(any(x[n] is None for n in NUTRIENTS) for x in ingredients): reasons.append('영양 정보 부족'); reason_codes.append('nutrition_unknown')
        nutrition={n:round(sum((q[n] or 0)*x['quantity_g']/q['basis_amount_g'] for x,q in zip(ingredients,qualities) if x['quantity_g'] is not None and math.isfinite(x['quantity_g']) and q['basis_amount_g'] and math.isfinite(q['basis_amount_g']) and q['basis_amount_g']>0)/recipe['servings'],2) for n in NUTRIENTS} if len(ingredients)==len(qualities) else {n:0 for n in NUTRIENTS}
        for p in people:
            for c in p['constraints']:
                if c['kind']=='meal_sodium_max_mg' and nutrition['sodium_mg']>float(c['value']): reasons.append('식사 나트륨 제한 초과'); reason_codes.append('health_constraint')
        if req.get('cook_time_limit_minutes') and recipe['cook_time_minutes']>req['cook_time_limit_minutes']: reasons.append('조리 시간 초과'); reason_codes.append('cook_time_limit')
        if missing and not req.get('purchase_allowed',False): reasons.append('재료 부족'); reason_codes.append('inventory_insufficient')
        if reasons:
            excluded.append({'recipe_id':recipe['id'],'name':recipe['name'],'reasons':sorted(set(reasons)), 'reason_codes':sorted(set(reason_codes))}); continue
        inv=100*urgency/len(ingredients)
        protein=sum(min(1,nutrition['protein_g']/max(1,remaining[p['id']].get('protein_g',1)/3)) for p in people)/len(people)*100
        pref=max(0,100-20*sum(any(x['name']==v['value'] or (v['value']=='매운 음식' and recipe['spicy']) for x in ingredients) for p in people for v in p['preferences'] if v['kind']=='dislike'))
        if req.get('desired_menu'):
            pref = min(100, pref + 20) if req['desired_menu'] in recipe['name'] else max(0, pref - 20)
        recent=db.execute('SELECT COUNT(*) FROM meal_records WHERE recipe_id=? AND substr(eaten_at,1,10)>=date(?,\'-7 days\')',(recipe['id'],today)).fetchone()[0]
        variety=100 if not recent else 30
        imminent=any(x['priority']==1 for x in usage)
        weights=(.30,.15,.25,.10,.08,.07,.05) if imminent else (.15,.15,.30,.15,.10,.10,.05)
        balance=protein; primary=protein; secondary=protein; feasibility=max(0,100-missing/10)
        score=round(sum(a*b for a,b in zip(weights,(inv,balance,primary,secondary,pref,feasibility,variety))),4)
        personal_score, applied, ignored = score_personalization(db, req['household_id'], people, recipe, ingredients, today)
        score = round(score + personal_score, 4)
        official_used=any(q.get('nutrition_source')=='official_imported' for q in qualities)
        candidates.append({'recipe_id':recipe['id'],'name':recipe['name'],'cook_time_minutes':recipe['cook_time_minutes'],'nutrition_per_serving':nutrition,'nutrition_confidence':'official imported (explicit mapping)' if official_used else '데모 추정치','inventory_usage':usage,'scores':{'inventory':round(inv,2),'nutrition_balance':round(balance,2),'primary_strategy':round(primary,2),'secondary_strategy':round(secondary,2),'preference':pref,'feasibility':round(feasibility,2),'variety':variety,'total':score},'purchase_missing_g':round(missing,2),'adjustments':[{'member_id':p['id'],'servings':1,'sauce':'기본 분량','topping':'기본 구성','nutrition':nutrition,'remaining_targets':remaining[p['id']]} for p in people]})
        candidates[-1].update({'personalization_score':personal_score, 'applied_preference_signals':applied, 'ignored_preference_signals':ignored, 'explanation_facts':{'personalization_score':personal_score, 'preference_signals':applied}, 'recommendation_reason_codes':['PERSONALIZED'] if applied else []})
        quality_status='demo_data' if any(q['source_type']=='demo' or q['nutrition_source']=='demo' for q in qualities) else 'confirmation_required' if warnings else 'verified'
        candidates[-1].update({'data_quality':quality_status,'warnings':sorted(set(warnings)),'requires_confirmation':bool(warnings)})
    candidates.sort(key=lambda c:(-c['scores']['total'],-sum(x['needed_g'] for x in c['inventory_usage'] if x['priority']==1),-c['scores']['primary_strategy'],c['purchase_missing_g'],c['cook_time_minutes'],c['recipe_id']))
    for candidate in candidates:
        for adjustment in candidate['adjustments']:
            adjustment['meal_target_estimate']={n:round(v/3,2) for n,v in adjustment['remaining_targets'].items()}
    snapshot = {'request':req,'as_of':today,'participants':people,'inventory':inventory,'recipes':[dict(r) for r in db.execute('SELECT * FROM recipes ORDER BY id')], 'recipe_ingredients':[dict(r) for r in db.execute('SELECT * FROM recipe_ingredients ORDER BY recipe_id,ingredient_id')], 'nutrition':[dict(r) for r in db.execute('SELECT * FROM ingredient_nutrition ORDER BY ingredient_id')], 'storage_guidelines':[dict(r) for r in db.execute('SELECT * FROM ingredient_storage_guidelines ORDER BY rowid')], 'remaining':remaining, 'excluded':excluded, 'scores':[(c['recipe_id'],c['scores'],c['personalization_score']) for c in candidates]}
    snapshot['ingredients']=[dict(r) for r in db.execute('SELECT * FROM ingredients ORDER BY id')]
    snapshot['allergen_tags']=[dict(r) for r in db.execute('SELECT * FROM ingredient_allergen_tags ORDER BY ingredient_id,allergen_code')]
    snapshot['allergen_aliases']=[dict(r) for r in db.execute('SELECT * FROM allergen_aliases ORDER BY alias')]
    fingerprint=hashlib.sha256(json.dumps(snapshot,sort_keys=True,ensure_ascii=False).encode('utf-8')).hexdigest()
    provenance='published official nutrition is available only through explicit approved mappings' if nutrition_release_id else 'demo/local nutrition; not official data'
    validated_constraint_codes=[]
    for raw_ingredient in req.get('excluded_ingredients',[]):
        if not isinstance(raw_ingredient,str): continue
        row=db.execute('SELECT id FROM ingredients WHERE name=? AND active=1',(raw_ingredient,)).fetchone()
        if row: validated_constraint_codes.append(f'exclude_ingredient:ingredient_{row[0]}')
    if candidates: validated_constraint_codes.append('nutrition:locally_validated')
    result={'rules_version':RULES_VERSION,'prompt_version':PROMPT_VERSION,'db_version':db.execute("SELECT value FROM metadata WHERE key='db_version'").fetchone()[0], 'input_snapshot_hash':fingerprint, 'participant_ids':ids,'profile_snapshot':people,'inventory_snapshot':inventory,'recommendations':candidates[:3],'excluded':excluded,'as_of':today,'nutrition_data_release_id':nutrition_release_id,'allergen_reference_version_id':allergen_reference_version_id,'data_provenance_summary':provenance,'validated_constraint_codes':sorted(set(validated_constraint_codes))}
    result.update({'status':'recommendations_available' if candidates else 'no_safe_recommendation', 'data_quality':'demo_data' if any(c['data_quality']=='demo_data' for c in candidates[:3]) else 'confirmation_required' if not candidates or any(c['requires_confirmation'] for c in candidates[:3]) else 'verified', 'requires_confirmation':not candidates or any(c['requires_confirmation'] for c in candidates[:3]), 'warnings':sorted({w for c in candidates[:3] for w in c['warnings']}), 'excluded_candidates':excluded})
    return persist_recommendation(db, req, result) if persist else result


def persist_recommendation(db, req, result):
    """Publish only the final AI-chain outcome, atomically with visible candidates."""
    result = dict(result)
    candidates = result.get('recommendations', []) if result.get('status') == 'recommendations_available' else []
    with db:
        cur=db.execute('INSERT INTO recommendation_requests(household_id,request_json,result_json,nutrition_data_release_id,allergen_reference_version_id) VALUES (?,?,?,?,?)',(req['household_id'],json.dumps(req,ensure_ascii=False),'',result.get('nutrition_data_release_id'),result.get('allergen_reference_version_id')))
        result['request_id']=cur.lastrowid
        db.execute('UPDATE recommendation_requests SET result_json=? WHERE id=?',(json.dumps(result,ensure_ascii=False),cur.lastrowid))
        db.executemany('INSERT INTO recommendation_candidates VALUES (?,?,?,?)',[(cur.lastrowid,c['recipe_id'],i,json.dumps(c,ensure_ascii=False)) for i,c in enumerate(candidates,1)])
        db.executemany("INSERT INTO recommendation_feedback(recommendation_request_id,candidate_recipe_id,action) VALUES (?,?,'shown')", [(cur.lastrowid,c['recipe_id']) for c in candidates])
    return result
