"""Read stored intake snapshots. No recipe nutrition recalculation or private cache."""
from datetime import date, datetime, time, timedelta
import json
import math
from zoneinfo import ZoneInfo

from .recommendation_provider import NUTRIENTS

KST = ZoneInfo('Asia/Seoul')
UNITS = {'energy_kcal':'kcal', 'protein_g':'g', 'carbohydrate_g':'g', 'fat_g':'g', 'sodium_mg':'mg'}


def kst_today():
    return datetime.now(KST).date()


def eaten_time(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z','+00:00'))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=KST)
        return parsed.astimezone(KST)
    except (ValueError, TypeError, AttributeError, OverflowError):
        return None


def _number(value):
    return type(value) in (int,float) and math.isfinite(value) and value >= 0


def _sum(values):
    values = list(values)
    if not all(_number(v) for v in values):
        return None
    try:
        return round(math.fsum(values),2)
    except OverflowError:
        return None


def _nutrition(items):
    return {n:_sum(item['nutrition'][n] for item in items) for n in NUTRIENTS}


def _known_nutrition(items):
    return {n:_sum(item['nutrition'][n] for item in items if _number(item['nutrition'][n])) for n in NUTRIENTS}


def _name(row):
    try:
        saved = json.loads(row['result_json'] or '{}')
        candidates = saved.get('recommendations', [])
        for candidate in candidates:
            if isinstance(candidate,dict) and candidate.get('recipe_id') == row['recipe_id'] and isinstance(candidate.get('name'),str):
                return candidate['name'], 'recommendation_snapshot'
    except (ValueError, TypeError, AttributeError):
        pass
    return row['current_recipe_name'], 'current_catalog' if row['current_recipe_name'] else 'unavailable'


def _base(db, household_id, *, member_id=None, record_id=None):
    query = '''SELECT r.id AS record_id,r.session_id AS meal_session_id,r.recipe_id,r.eaten_at,
        s.meal_type,c.id AS cooking_session_id,c.ingredients_json,q.result_json,p.name AS current_recipe_name
        FROM meal_records r JOIN meal_sessions s ON s.id=r.session_id
        LEFT JOIN cooking_sessions c ON c.id=(SELECT MIN(c2.id) FROM cooking_sessions c2
            WHERE c2.meal_record_id=r.id AND c2.household_id=s.household_id
                AND c2.meal_session_id=r.session_id AND c2.recipe_id=r.recipe_id)
        LEFT JOIN recommendation_requests q ON q.id=c.request_id AND q.household_id=s.household_id
        LEFT JOIN recipes p ON p.id=r.recipe_id WHERE s.household_id=?'''
    params = [household_id]
    if record_id is not None:
        query += ' AND r.id=?'; params.append(record_id)
    if member_id is not None:
        query += ' AND EXISTS (SELECT 1 FROM meal_record_items m WHERE m.record_id=r.id AND m.member_id=?)'
        params.append(member_id)
    return [dict(row) for row in db.execute(query,params)]


def _hydrate(db, household_id, rows, member_id=None):
    by_record = {row['record_id']:[] for row in rows}
    ids = list(by_record)
    for start in range(0,len(ids),200):
        chunk = ids[start:start+200]
        query = f'''SELECT i.id,i.record_id,i.member_id,i.servings,m.name AS member_name,n.nutrient,n.amount
            FROM meal_record_items i JOIN meal_records r ON r.id=i.record_id
            JOIN meal_sessions s ON s.id=r.session_id
            JOIN members m ON m.id=i.member_id AND m.household_id=s.household_id
            LEFT JOIN nutrition_intakes n ON n.record_item_id=i.id
            WHERE s.household_id=? AND i.record_id IN ({','.join('?' for _ in chunk)})'''
        params = [household_id,*chunk]
        if member_id is not None:
            query += ' AND i.member_id=?'; params.append(member_id)
        query += ' ORDER BY i.record_id,i.member_id,i.id,n.nutrient'
        by_item = {}
        for row in db.execute(query,params):
            item = by_item.get(row['id'])
            if item is None:
                item = {'intake_item_id':row['id'], 'member_id':row['member_id'],
                        'member_name':row['member_name'], 'servings':row['servings'] if _number(row['servings']) else None,
                        'nutrition':dict.fromkeys(NUTRIENTS), '_seen':set()}
                by_item[row['id']] = item
                by_record[row['record_id']].append(item)
            nutrient = row['nutrient']
            if nutrient in NUTRIENTS:
                valid = nutrient not in item['_seen'] and _number(row['amount'])
                item['nutrition'][nutrient] = row['amount'] if valid else None
                item['_seen'].add(nutrient)
        for item in by_item.values():
            item.pop('_seen')
            item['data_quality'] = 'recorded' if item['servings'] is not None and all(v is not None for v in item['nutrition'].values()) else 'incomplete'
    result = []
    for row in rows:
        meal = dict(row)
        items = by_record[row['record_id']]
        at = eaten_time(row['eaten_at'])
        name, source = _name(row)
        for key in ('result_json','ingredients_json','current_recipe_name'): meal.pop(key,None)
        meal.update(recipe_name=name, recipe_name_source=source, eaten_at_kst=at.isoformat() if at else None,
                    meal_date=at.date().isoformat() if at else None, items=items,
                    nutrition_totals=_nutrition(items) if items else dict.fromkeys(NUTRIENTS),
                    known_nutrition_totals=_known_nutrition(items),
                    data_quality='recorded' if at and items and all(i['data_quality']=='recorded' for i in items) else 'incomplete')
        result.append(meal)
    return result


def history(db, household_id, start_date, end_date, *, member_id=None, limit=20, offset=0):
    rows, invalid = _period_rows(db,household_id,start_date,end_date,member_id)
    return {'total':len(rows),'limit':limit,'offset':offset,'has_more':offset+limit<len(rows),
            'excluded_invalid_timestamp_count':invalid,
            'records':_hydrate(db,household_id,rows[offset:offset+limit],member_id)}


def detail(db, household_id, record_id):
    rows = _base(db,household_id,record_id=record_id)
    if not rows:
        return None
    result = _hydrate(db,household_id,rows)[0]
    result['participants'] = [dict(row) for row in db.execute('''SELECT m.id,m.name
        FROM meal_session_participants p JOIN members m ON m.id=p.member_id
        WHERE p.session_id=? AND m.household_id=? ORDER BY m.id''',
        (result['meal_session_id'], household_id))]
    return result


def _period_rows(db, household_id, start_date, end_date, member_id):
    begin = datetime.combine(start_date,time.min,tzinfo=KST)
    until = datetime.combine(end_date+timedelta(days=1),time.min,tzinfo=KST)
    rows = []; invalid = 0
    for row in _base(db,household_id,member_id=member_id):
        at = eaten_time(row['eaten_at'])
        if at is None: invalid += 1
        elif begin <= at < until: rows.append((at,row))
    rows.sort(key=lambda pair:(pair[0],pair[1]['record_id']),reverse=True)
    return [row for _,row in rows], invalid


def _summary(records):
    items = [item for record in records for item in record['items']]
    empty_records = any(not record['items'] for record in records)
    totals = dict.fromkeys(NUTRIENTS) if empty_records else _nutrition(items)
    incomplete = any(record['data_quality']!='recorded' for record in records) or any(v is None for v in totals.values())
    return {'meal_record_count':len(records), 'intake_item_count':len(items),
            'consumed_meal_count':sum(any(i['servings'] and i['servings']>0 for i in r['items']) for r in records),
            'zero_serving_item_count':sum(i['servings']==0 for i in items),
            'total_servings':_sum(i['servings'] for i in items),
            'nutrition_totals':totals,
            'known_nutrition_totals':_known_nutrition(items),
            'incomplete_item_count':sum(i['data_quality']!='recorded' for i in items),
            'data_quality':'incomplete' if incomplete else 'recorded' if records else 'empty'}


def nutrition_report(db, household_id, start_date, end_date, *, member_id=None):
    rows, invalid = _period_rows(db,household_id,start_date,end_date,member_id)
    records = _hydrate(db,household_id,rows,member_id)
    result = _summary(records)
    days = []; day = start_date
    while day <= end_date:
        days.append({'date':day.isoformat(),**_summary([r for r in records if r['meal_date']==day.isoformat()])})
        day += timedelta(days=1)
    members = []
    for person in db.execute('SELECT id,name FROM members WHERE household_id=? ORDER BY id',(household_id,)):
        if member_id is not None and person['id']!=member_id: continue
        own = []
        for record in records:
            items = [i for i in record['items'] if i['member_id']==person['id']]
            if items:
                own.append({**record,'items':items,'data_quality':'recorded' if all(i['data_quality']=='recorded' for i in items) else 'incomplete'})
        members.append({'member_id':person['id'],'member_name':person['name'],**_summary(own)})
    if invalid: result['data_quality'] = 'incomplete'
    return {**result,'daily':days,'members':members,'excluded_invalid_timestamp_count':invalid}
