"""Local, deterministic preference evidence; never modifies explicit profiles."""
from datetime import datetime, timezone
import json
import math

FEATURES = {'ingredient', 'recipe', 'cuisine', 'cooking_method', 'taste', 'cook_time', 'meal_type'}


def add_evidence(db, household_id, member_id, source, feature, value, signal, strength=1.0, confidence=1.0, observed_at=None):
    if feature not in FEATURES or signal not in ('prefer', 'avoid', 'neutral') or not value:
        raise ValueError('지원하지 않는 선호 신호입니다.')
    if member_id is not None and not db.execute('SELECT 1 FROM members WHERE id=? AND household_id=?', (member_id, household_id)).fetchone():
        raise ValueError('해당 가구의 구성원이 아닙니다.')
    cur = db.execute('INSERT INTO preference_evidence(household_id,member_id,source_type,feature_type,feature_value,signal,strength,confidence,observed_at) VALUES (?,?,?,?,?,?,?,?,coalesce(?,CURRENT_TIMESTAMP))', (household_id, member_id, source, feature, str(value), signal, strength, confidence, observed_at))
    return cur.lastrowid


def aggregate(db, household_id, now=None):
    now = now or datetime.now(timezone.utc)
    rows = db.execute('SELECT * FROM preference_evidence WHERE household_id=? ORDER BY id', (household_id,)).fetchall()
    groups = {}
    for row in rows:
        try:
            observed = datetime.fromisoformat(row['observed_at'].replace('Z', '+00:00'))
            if observed.tzinfo is None: observed = observed.replace(tzinfo=timezone.utc)
            expiry = datetime.fromisoformat(row['expires_at'].replace('Z', '+00:00')) if row['expires_at'] else None
            if expiry and expiry.tzinfo is None: expiry = expiry.replace(tzinfo=timezone.utc)
        except (ValueError, AttributeError):
            continue
        if observed.date() > now.date() or (expiry and expiry.date() <= now.date()): continue
        age = (now.date() - observed.date()).days
        weight = math.pow(0.5, age / 90) * row['strength'] * row['confidence']
        key = (row['member_id'], row['feature_type'], row['feature_value'])
        groups.setdefault(key, []).append((row, weight))
    existing = db.execute("SELECT * FROM inferred_preferences WHERE household_id=? AND status!='dismissed'", (household_id,)).fetchall()
    for row in existing:
        db.execute("UPDATE inferred_preferences SET status='expired' WHERE id=?", (row['id'],))
    for (member_id, feature, value), entries in sorted(groups.items(), key=lambda x: (x[0][0] or 0, x[0][1], x[0][2])):
        previous = db.execute('SELECT id,status FROM inferred_preferences WHERE household_id=? AND member_id IS ? AND feature_type=? AND feature_value=?', (household_id, member_id, feature, value)).fetchone()
        if previous and previous['status'] == 'dismissed': continue
        net = sum(w * (1 if r['signal'] == 'prefer' else -1 if r['signal'] == 'avoid' else 0) for r, w in entries)
        total = sum(w for _, w in entries)
        confidence = min(1.0, abs(net) / 3) if total else 0
        status = 'active' if len(entries) >= 3 and confidence >= .65 else 'expired'
        dates = [r['observed_at'] for r, _ in entries]
        sources = sorted({r['source_type'] for r, _ in entries})
        values = ('prefer' if net >= 0 else 'avoid', round(confidence, 4), len(entries), round(min(5, confidence * 5), 4), min(dates), max(dates), json.dumps(sources), status)
        if previous:
            db.execute('UPDATE inferred_preferences SET inference=?,confidence=?,evidence_count=?,score_adjustment=?,first_observed_at=?,last_observed_at=?,source_summary=?,status=? WHERE id=?', (*values, previous['id']))
        else:
            db.execute('INSERT INTO inferred_preferences(household_id,member_id,feature_type,feature_value,inference,confidence,evidence_count,score_adjustment,first_observed_at,last_observed_at,source_summary,status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', (household_id, member_id, feature, value, *values))


def recipe_features(db, recipe_id):
    return [('recipe', str(recipe_id))] + [('ingredient', r[0]) for r in db.execute('SELECT i.name FROM recipe_ingredients ri JOIN ingredients i ON i.id=ri.ingredient_id WHERE ri.recipe_id=? ORDER BY i.id', (recipe_id,))]


def record_recipe(db, household_id, member_id, source, recipe_id):
    return [add_evidence(db, household_id, member_id, source, kind, value, 'prefer') for kind, value in recipe_features(db, recipe_id)]


def update_summary(db, household_id):
    rows = db.execute("SELECT f.candidate_recipe_id,r.cook_time_minutes,rr.request_json FROM recommendation_feedback f JOIN recommendation_requests rr ON rr.id=f.recommendation_request_id JOIN recipes r ON r.id=f.candidate_recipe_id WHERE rr.household_id=? AND f.action IN ('selected','cooked') ORDER BY f.id DESC LIMIT 30", (household_id,)).fetchall()
    times = [r['cook_time_minutes'] for r in rows]
    meals = [json.loads(r['request_json']).get('meal_type') for r in rows]
    ingredients = {}
    for r in rows:
        for feature, value in recipe_features(db, r['candidate_recipe_id']):
            if feature == 'ingredient': ingredients[value] = ingredients.get(value, 0) + 1
    rejected = [dict(r) for r in db.execute("SELECT feature_type,feature_value FROM preference_evidence WHERE household_id=? AND signal='avoid' ORDER BY id DESC LIMIT 10", (household_id,))]
    db.execute('INSERT INTO household_behavior_summary VALUES (?,?,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(household_id) DO UPDATE SET preferred_cook_time_minutes=excluded.preferred_cook_time_minutes,preferred_meal_types_json=excluded.preferred_meal_types_json,frequently_used_ingredients_json=excluded.frequently_used_ingredients_json,recently_rejected_features_json=excluded.recently_rejected_features_json,updated_at=CURRENT_TIMESTAMP', (household_id, round(sum(times)/len(times), 1) if times else None, json.dumps(sorted(set(x for x in meals if x))), json.dumps(sorted(ingredients, key=lambda x: (-ingredients[x], x))[:10], ensure_ascii=False), json.dumps(rejected, ensure_ascii=False)))


def score_personalization(db, household_id, people, recipe, ingredients, today):
    ids = [p['id'] for p in people]
    features = {('recipe', str(recipe['id']))} | {('ingredient', i['name']) for i in ingredients}
    rows = [dict(r) for r in db.execute("SELECT * FROM inferred_preferences WHERE household_id=? AND status='active' AND (expires_at IS NULL OR expires_at>?) ORDER BY id", (household_id, today)) if r['member_id'] is None or r['member_id'] in ids]
    applied, ignored = [], []
    personal = {(r['member_id'], r['feature_type'], r['feature_value']) for r in rows if r['member_id'] is not None}
    score = 0.0
    for r in rows:
        if (r['feature_type'], r['feature_value']) not in features: continue
        signal = {k: r[k] for k in ('id','member_id','feature_type','feature_value','inference','confidence','evidence_count')}
        explicit = any(p['id'] == r['member_id'] or r['member_id'] is None for p in people if any(v['kind'] == 'dislike' and v['value'] == r['feature_value'] for v in p['preferences']))
        overridden = r['member_id'] is None and any((i, r['feature_type'], r['feature_value']) in personal for i in ids)
        if overridden or (explicit and r['inference'] == 'prefer'):
            signal['ignored_reason'] = '개인 선호 우선' if overridden else '명시 비선호 우선'
            ignored.append(signal); continue
        adjustment = r['score_adjustment'] * (1 if r['inference'] == 'prefer' else -1)
        signal['adjustment'] = adjustment; applied.append(signal); score += adjustment
    recent = [r for r in db.execute("SELECT f.member_id FROM recommendation_feedback f JOIN recommendation_requests q ON q.id=f.recommendation_request_id WHERE q.household_id=? AND f.candidate_recipe_id=? AND f.action='selected' AND date(f.created_at) BETWEEN date(?,'-7 days') AND date(?)", (household_id, recipe['id'], today, today)) if r['member_id'] is None or r['member_id'] in ids]
    if recent:
        adjustment = -min(3, len(recent)); score += adjustment
        applied.append({'feature_type':'recipe', 'feature_value':str(recipe['id']), 'inference':'recent_repeat', 'adjustment':adjustment})
    recent_adjustments = {}
    for row in db.execute("SELECT member_id,feature_type,feature_value,signal,source_type FROM preference_evidence WHERE household_id=? AND date(observed_at) BETWEEN date(?,'-30 days') AND date(?) AND (expires_at IS NULL OR date(expires_at)>date(?)) ORDER BY id", (household_id,today,today,today)):
        if row['member_id'] is not None and row['member_id'] not in ids: continue
        if (row['feature_type'],row['feature_value']) not in features: continue
        status = db.execute('SELECT status FROM inferred_preferences WHERE household_id=? AND member_id IS ? AND feature_type=? AND feature_value=?', (household_id,row['member_id'],row['feature_type'],row['feature_value'])).fetchone()
        if status and status['status'] == 'dismissed': continue
        if row['member_id'] is None and any((i,row['feature_type'],row['feature_value']) in personal for i in ids): continue
        if row['signal']=='prefer' and any(any(v['kind']=='dislike' and v['value']==row['feature_value'] for v in p['preferences']) for p in people if row['member_id'] is None or p['id']==row['member_id']): continue
        if row['signal']=='avoid' and row['source_type']=='recommendation_feedback':
            score -= .5
            recent_adjustments['recent_rejection'] = recent_adjustments.get('recent_rejection',0) - .5
        elif row['signal']=='prefer' and row['feature_type']=='ingredient' and row['source_type'] in ('meal_record','cooking_adjustment'):
            score += .25
            recent_adjustments['recent_ingredient'] = recent_adjustments.get('recent_ingredient',0) + .25
    for reason, adjustment in sorted(recent_adjustments.items()):
        applied.append({'feature_type':'behavior', 'inference':reason, 'adjustment':adjustment})
    time_rows = [r['cook_time_minutes'] for r in db.execute("SELECT f.member_id,r.cook_time_minutes FROM recommendation_feedback f JOIN recommendation_requests q ON q.id=f.recommendation_request_id JOIN recipes r ON r.id=f.candidate_recipe_id WHERE q.household_id=? AND f.action IN ('selected','cooked') ORDER BY f.id DESC LIMIT 100", (household_id,)) if r['member_id'] is None or r['member_id'] in ids]
    preferred_time = sum(time_rows)/len(time_rows) if time_rows else None
    if preferred_time is not None and abs(recipe['cook_time_minutes']-preferred_time) <= 5:
        score += 1
        applied.append({'feature_type':'cook_time', 'feature_value':str(round(preferred_time, 1)), 'inference':'time_fit', 'adjustment':1})
    return round(max(-15, min(15, score)), 4), applied, ignored
