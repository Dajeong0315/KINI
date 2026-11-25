"""Versioned public food catalog. Import does not overwrite live recommendation data."""
import hashlib
import json
import math
import uuid

NUTRIENTS = ('energy_kcal', 'protein_g', 'carbohydrate_g', 'fat_g', 'sodium_mg', 'sugar_g')


def stage(db, source_code, rows, *, source_revision):
    release_id = str(uuid.uuid4())
    with db:
        db.execute('INSERT INTO data_releases(id,source_code,status,source_revision) VALUES (?,?,?,?)',
                   (release_id, source_code, 'staging', source_revision))
        for row in rows:
            payload = json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False)
            db.execute('INSERT INTO source_records VALUES (?,?,?,?,?)',
                       (release_id, row['external_id'], row['kind'], payload, hashlib.sha256(payload.encode()).hexdigest()))
    return release_id


def _positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _nutrition_issue(row, units):
    if not isinstance(row, dict) or not _positive(row.get('basis_amount')) or row.get('basis_unit') not in units:
        return 'unknown_nutrition_basis'
    if not isinstance(row.get('nutrients'), dict) or not row['nutrients'] or not any(v is not None for v in row['nutrients'].values()):
        return 'missing_nutrition'
    for nutrient, value in row['nutrients'].items():
        if nutrient not in NUTRIENTS or (value is not None and not (_positive(value) or value == 0 and not isinstance(value, bool))):
            return 'invalid_nutrition'
    return None


def validate(db, release_id):
    release = db.execute('SELECT status FROM data_releases WHERE id=?', (release_id,)).fetchone()
    if not release or release[0] != 'staging':
        raise ValueError('staging release만 검증할 수 있습니다.')
    issues = []
    records = db.execute('SELECT * FROM source_records WHERE release_id=?', (release_id,)).fetchall()
    if not records:
        issues.append('empty_release')
    for record in records:
        row = json.loads(record['payload_json'])
        reason = None
        if not str(row.get('name', '')).strip():
            reason = 'missing_name'
        elif row['kind'] == 'food':
            reason = _nutrition_issue(row, ('g', 'ml'))
        elif row['kind'] == 'recipe':
            if not isinstance(row.get('ingredients_text'), str) or not row['ingredients_text'].strip():
                reason = 'missing_ingredients'
            elif not isinstance(row.get('steps'), list) or not row['steps'] or any(not isinstance(s, str) or not s.strip() for s in row['steps']):
                reason = 'missing_steps'
            elif row.get('servings') is not None and not _positive(row['servings']):
                reason = 'invalid_servings'
            elif row.get('cook_time_minutes') is not None and (not isinstance(row['cook_time_minutes'], int) or isinstance(row['cook_time_minutes'], bool) or row['cook_time_minutes'] <= 0):
                reason = 'invalid_cook_time'
            elif row.get('nutrition') is not None:
                reason = _nutrition_issue(row['nutrition'], ('g', 'ml', 'serving'))
        if reason:
            issues.append({'external_id': record['external_id'], 'reason': reason})
    with db:
        db.execute('UPDATE data_releases SET status=?,validation_report=? WHERE id=?',
                   ('quarantined' if issues else 'validated', json.dumps(issues), release_id))
    return issues


def publish(db, release_id):
    """Explicit operator action. Immutable releases; source pointer is the only swap."""
    with db:
        release = db.execute('''SELECT r.*,s.rights_status FROM data_releases r
            JOIN catalog_data_sources s ON s.code=r.source_code WHERE r.id=?''', (release_id,)).fetchone()
        if not release or release['status'] != 'validated':
            raise ValueError('검증을 통과한 release만 배포할 수 있습니다.')
        if release['rights_status'] != 'approved':
            raise ValueError('데이터 이용 조건을 먼저 확인하고 승인해야 합니다.')
        for record in db.execute('SELECT payload_json FROM source_records WHERE release_id=?', (release_id,)).fetchall():
            row = json.loads(record[0])
            rid = row['external_id']
            if row['kind'] == 'food':
                db.execute('INSERT INTO catalog_foods VALUES (?,?,?,?,?)',
                           (release_id, rid, row['name'], row['basis_amount'], row['basis_unit']))
                db.executemany('INSERT INTO catalog_food_nutrients VALUES (?,?,?,?)',
                               [(release_id, rid, n, v) for n, v in row['nutrients'].items() if v is not None])
            else:
                db.execute('INSERT INTO catalog_recipes VALUES (?,?,?,?,?,?)',
                           (release_id, rid, row['name'], row.get('servings'), row.get('cook_time_minutes'), row['ingredients_text']))
                db.executemany('INSERT INTO catalog_recipe_steps VALUES (?,?,?,?)',
                               [(release_id, rid, i, text) for i, text in enumerate(row['steps'], 1)])
                nutrition = row.get('nutrition')
                if nutrition:
                    db.executemany('INSERT INTO catalog_recipe_nutrients VALUES (?,?,?,?,?,?)',
                        [(release_id, rid, n, v, nutrition['basis_amount'], nutrition['basis_unit'])
                         for n, v in nutrition['nutrients'].items() if v is not None])
        db.execute("UPDATE data_releases SET status='published' WHERE id=?", (release_id,))
        _activate(db, release['source_code'], release_id)


def _activate(db, source_code, release_id):
    db.execute('''INSERT INTO active_data_releases VALUES (?,?,1)
        ON CONFLICT(source_code) DO UPDATE SET release_id=excluded.release_id,
        cache_version=active_data_releases.cache_version+1''', (source_code, release_id))


def rollback(db, source_code, release_id):
    with db:
        row = db.execute("SELECT 1 FROM data_releases WHERE id=? AND source_code=? AND status='published'",
                         (release_id, source_code)).fetchone()
        if not row:
            raise ValueError('같은 출처의 배포된 release만 복구할 수 있습니다.')
        _activate(db, source_code, release_id)
