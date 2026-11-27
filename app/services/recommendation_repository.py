from __future__ import annotations
import json, os, sqlite3
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / 'data'
SCHEMA = '''
CREATE TABLE IF NOT EXISTS households(id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS members(id INTEGER PRIMARY KEY, household_id INTEGER NOT NULL REFERENCES households(id), name TEXT NOT NULL, age INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS member_preferences(member_id INTEGER REFERENCES members(id), kind TEXT, value TEXT);
CREATE TABLE IF NOT EXISTS member_allergies(member_id INTEGER REFERENCES members(id), ingredient TEXT);
CREATE TABLE IF NOT EXISTS member_health_constraints(member_id INTEGER REFERENCES members(id), kind TEXT, value TEXT);
CREATE TABLE IF NOT EXISTS member_nutrition_targets(member_id INTEGER REFERENCES members(id), nutrient TEXT, amount REAL, PRIMARY KEY(member_id,nutrient));
CREATE TABLE IF NOT EXISTS ingredients(id INTEGER PRIMARY KEY, name TEXT UNIQUE, category TEXT);
CREATE TABLE IF NOT EXISTS ingredient_aliases(ingredient_id INTEGER NOT NULL REFERENCES ingredients(id), alias TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS ingredient_allergen_tags(ingredient_id INTEGER NOT NULL REFERENCES ingredients(id), allergen_code TEXT NOT NULL, source_type TEXT NOT NULL DEFAULT 'unknown', confidence REAL, label_required INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(ingredient_id,allergen_code));
CREATE TABLE IF NOT EXISTS allergen_aliases(alias TEXT PRIMARY KEY, allergen_code TEXT NOT NULL, source_type TEXT NOT NULL DEFAULT 'unknown', confidence REAL);
CREATE TABLE IF NOT EXISTS storage_guidance(ingredient_id INTEGER REFERENCES ingredients(id), storage_location TEXT, unopened_days INTEGER, opened_days INTEGER, source_type TEXT DEFAULT 'unknown', confidence REAL, note TEXT);
CREATE TABLE IF NOT EXISTS ingredient_nutrition(ingredient_id INTEGER PRIMARY KEY REFERENCES ingredients(id), energy_kcal REAL, protein_g REAL, carbohydrate_g REAL, fat_g REAL, sodium_mg REAL);
CREATE TABLE IF NOT EXISTS ingredient_storage_guidelines(ingredient_id INTEGER REFERENCES ingredients(id), guideline TEXT);
CREATE TABLE IF NOT EXISTS unit_conversions(ingredient_id INTEGER REFERENCES ingredients(id), unit TEXT, grams REAL);
CREATE TABLE IF NOT EXISTS inventory_items(id INTEGER PRIMARY KEY, household_id INTEGER REFERENCES households(id), ingredient_id INTEGER REFERENCES ingredients(id), quantity_g REAL, freshness_state TEXT, use_by TEXT);
CREATE TABLE IF NOT EXISTS recipes(id INTEGER PRIMARY KEY, name TEXT, servings INTEGER, cook_time_minutes INTEGER, spicy INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS recipe_ingredients(recipe_id INTEGER REFERENCES recipes(id), ingredient_id INTEGER REFERENCES ingredients(id), quantity_g REAL, PRIMARY KEY(recipe_id, ingredient_id));
CREATE TABLE IF NOT EXISTS meal_sessions(id INTEGER PRIMARY KEY, household_id INTEGER REFERENCES households(id), meal_type TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS meal_session_participants(session_id INTEGER REFERENCES meal_sessions(id), member_id INTEGER REFERENCES members(id), PRIMARY KEY(session_id,member_id));
CREATE TABLE IF NOT EXISTS meal_records(id INTEGER PRIMARY KEY, session_id INTEGER REFERENCES meal_sessions(id), recipe_id INTEGER REFERENCES recipes(id), eaten_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meal_record_items(id INTEGER PRIMARY KEY, record_id INTEGER REFERENCES meal_records(id), member_id INTEGER REFERENCES members(id), servings REAL NOT NULL);
CREATE TABLE IF NOT EXISTS nutrition_intakes(record_item_id INTEGER REFERENCES meal_record_items(id), nutrient TEXT, amount REAL);
CREATE TABLE IF NOT EXISTS recommendation_requests(id INTEGER PRIMARY KEY, household_id INTEGER REFERENCES households(id), request_json TEXT, result_json TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS recommendation_candidates(request_id INTEGER REFERENCES recommendation_requests(id), recipe_id INTEGER REFERENCES recipes(id), rank INTEGER, candidate_json TEXT);
CREATE TABLE IF NOT EXISTS rag_documents(id INTEGER PRIMARY KEY, title TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS rag_chunks(id INTEGER PRIMARY KEY, document_id INTEGER REFERENCES rag_documents(id), content TEXT);
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS conversation_sessions(id INTEGER PRIMARY KEY, household_id INTEGER NOT NULL REFERENCES households(id), meal_session_id INTEGER REFERENCES meal_sessions(id), purpose TEXT NOT NULL CHECK(purpose IN ('recommendation','cooking','feedback','general')), created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, closed_at TEXT);
CREATE TABLE IF NOT EXISTS conversation_messages(id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES conversation_sessions(id), role TEXT NOT NULL CHECK(role IN ('user','assistant','tool')), content TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, metadata_json TEXT);
CREATE TABLE IF NOT EXISTS recommendation_feedback(id INTEGER PRIMARY KEY, recommendation_request_id INTEGER NOT NULL REFERENCES recommendation_requests(id), candidate_recipe_id INTEGER REFERENCES recipes(id), member_id INTEGER REFERENCES members(id), action TEXT NOT NULL CHECK(action IN ('shown','selected','rejected','cooked','skipped','rated')), rating INTEGER CHECK(rating BETWEEN 1 AND 5), reason_code TEXT, reason_text TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS preference_evidence(id INTEGER PRIMARY KEY, household_id INTEGER NOT NULL REFERENCES households(id), member_id INTEGER REFERENCES members(id), source_type TEXT NOT NULL, feature_type TEXT NOT NULL, feature_value TEXT NOT NULL, signal TEXT NOT NULL CHECK(signal IN ('prefer','avoid','neutral')), strength REAL NOT NULL, confidence REAL NOT NULL, observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, expires_at TEXT, metadata_json TEXT);
CREATE TABLE IF NOT EXISTS inferred_preferences(id INTEGER PRIMARY KEY, household_id INTEGER NOT NULL REFERENCES households(id), member_id INTEGER REFERENCES members(id), feature_type TEXT NOT NULL, feature_value TEXT NOT NULL, inference TEXT NOT NULL, confidence REAL NOT NULL, evidence_count INTEGER NOT NULL, score_adjustment REAL NOT NULL, first_observed_at TEXT NOT NULL, last_observed_at TEXT NOT NULL, expires_at TEXT, source_summary TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('active','expired','dismissed')));
CREATE UNIQUE INDEX IF NOT EXISTS preference_identity ON inferred_preferences(household_id,ifnull(member_id,0),feature_type,feature_value);
CREATE TABLE IF NOT EXISTS household_behavior_summary(household_id INTEGER PRIMARY KEY REFERENCES households(id), preferred_cook_time_minutes REAL, preferred_meal_types_json TEXT, frequently_used_ingredients_json TEXT, recently_rejected_features_json TEXT, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS cooking_sessions(id INTEGER PRIMARY KEY, household_id INTEGER NOT NULL REFERENCES households(id), meal_session_id INTEGER NOT NULL REFERENCES meal_sessions(id), request_id INTEGER NOT NULL REFERENCES recommendation_requests(id), recipe_id INTEGER NOT NULL REFERENCES recipes(id), ingredients_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, completed_at TEXT, meal_record_id INTEGER REFERENCES meal_records(id));
CREATE TABLE IF NOT EXISTS cooking_inventory_event_links(event_id INTEGER PRIMARY KEY REFERENCES inventory_events(id), cooking_session_id INTEGER NOT NULL REFERENCES cooking_sessions(id));
-- Imported reference data is deliberately separate from household and demo data.
CREATE TABLE IF NOT EXISTS data_sources(id INTEGER PRIMARY KEY, source_key TEXT NOT NULL UNIQUE, provider_name TEXT NOT NULL, dataset_name TEXT NOT NULL, source_url TEXT NOT NULL, license_text TEXT, license_url TEXT, expected_format TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS dataset_releases(id INTEGER PRIMARY KEY, data_source_id INTEGER NOT NULL REFERENCES data_sources(id), external_release_id TEXT, source_published_at TEXT, imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, checksum TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('staging','validated','published','retired','failed')), record_count INTEGER NOT NULL DEFAULT 0, accepted_count INTEGER NOT NULL DEFAULT 0, quarantined_count INTEGER NOT NULL DEFAULT 0, notes TEXT);
CREATE TABLE IF NOT EXISTS import_runs(id INTEGER PRIMARY KEY, data_source_id INTEGER NOT NULL REFERENCES data_sources(id), release_id INTEGER REFERENCES dataset_releases(id), started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, finished_at TEXT, mode TEXT NOT NULL CHECK(mode IN ('dry_run','import','publish')), status TEXT NOT NULL, input_reference TEXT, error_summary TEXT);
CREATE TABLE IF NOT EXISTS import_quarantine(id INTEGER PRIMARY KEY, release_id INTEGER NOT NULL REFERENCES dataset_releases(id), source_record_key TEXT, reason_code TEXT NOT NULL, safe_error_detail TEXT, raw_reference TEXT, resolved_at TEXT);
CREATE TABLE IF NOT EXISTS official_food_items(id INTEGER PRIMARY KEY, release_id INTEGER NOT NULL REFERENCES dataset_releases(id), source_food_code TEXT NOT NULL, food_name TEXT NOT NULL, normalized_name TEXT NOT NULL, food_category TEXT, manufacturer TEXT, product_report_number TEXT, serving_reference TEXT, basis_amount_g REAL, source_record_date TEXT, active INTEGER NOT NULL DEFAULT 1, UNIQUE(release_id,source_food_code));
CREATE TABLE IF NOT EXISTS official_nutrient_values(id INTEGER PRIMARY KEY, official_food_item_id INTEGER NOT NULL REFERENCES official_food_items(id), nutrient_code TEXT NOT NULL, amount REAL NOT NULL, unit TEXT NOT NULL, basis_amount_g REAL, confidence REAL, source_field TEXT, UNIQUE(official_food_item_id,nutrient_code));
CREATE TABLE IF NOT EXISTS ingredient_official_food_mappings(id INTEGER PRIMARY KEY, ingredient_id INTEGER NOT NULL REFERENCES ingredients(id), official_food_item_id INTEGER NOT NULL REFERENCES official_food_items(id), mapping_method TEXT NOT NULL CHECK(mapping_method IN ('manual','approved_alias','imported_code')), mapping_confidence REAL NOT NULL, approved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, active INTEGER NOT NULL DEFAULT 1, UNIQUE(ingredient_id,official_food_item_id));
CREATE TABLE IF NOT EXISTS allergen_reference_versions(id INTEGER PRIMARY KEY, source_name TEXT NOT NULL, source_url TEXT NOT NULL, legal_or_reference_version TEXT NOT NULL, effective_date TEXT, imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, checksum TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('staging','validated','published','retired','failed')));
CREATE TABLE IF NOT EXISTS allergen_reference_items(id INTEGER PRIMARY KEY, version_id INTEGER NOT NULL REFERENCES allergen_reference_versions(id), allergen_code TEXT NOT NULL, display_name_ko TEXT NOT NULL, aliases_json TEXT NOT NULL DEFAULT '[]', active INTEGER NOT NULL DEFAULT 1, UNIQUE(version_id,allergen_code));
CREATE TABLE IF NOT EXISTS data_release_pointers(id INTEGER PRIMARY KEY CHECK(id=1), nutrition_release_id INTEGER REFERENCES dataset_releases(id), allergen_reference_version_id INTEGER REFERENCES allergen_reference_versions(id), updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
'''

def connect(path=None):
    if os.getenv('KINI_DB_BACKEND', 'sqlite').lower() == 'postgres':
        from .postgres_repository import connect_postgres
        return connect_postgres()
    path = path or os.getenv('KINI_DB_PATH') or str(DATA / 'kini.sqlite3')
    db=sqlite3.connect(path, timeout=10)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db

def initialize(db, *, seed_demo=None):
    # Earlier A-only databases used data_sources(code,...). main's official import
    # owns data_sources(id,source_key,...); preserve both without overwriting data.
    source_columns = {row['name'] for row in db.execute('PRAGMA table_info(data_sources)')}
    if 'code' in source_columns and 'source_key' not in source_columns:
        db.execute('ALTER TABLE data_sources RENAME TO catalog_data_sources')
    db.executescript(SCHEMA)
    db.execute('BEGIN IMMEDIATE')
    migrations = {
        'members': {
            'height_cm': 'REAL CHECK (height_cm BETWEEN 0.1 AND 300)',
            'weight_kg': 'REAL CHECK (weight_kg BETWEEN 0.1 AND 700)',
            'activity_level': "TEXT CHECK (activity_level IN ('sedentary', 'low_active', 'active', 'very_active'))",
        },
        'cooking_sessions': {'planned_servings':'INTEGER'},
        'ingredients': {'canonical_name':'TEXT', 'source_type':"TEXT NOT NULL DEFAULT 'unknown'", 'data_confidence':'REAL', 'is_composite':'INTEGER NOT NULL DEFAULT 0', 'requires_label_check':'INTEGER NOT NULL DEFAULT 0', 'active':'INTEGER NOT NULL DEFAULT 1'},
        'ingredient_aliases': {'normalized_alias':'TEXT', 'alias_type':"TEXT NOT NULL DEFAULT 'unknown'", 'confidence':'REAL', 'source_type':"TEXT NOT NULL DEFAULT 'unknown'"},
        'ingredient_nutrition': {'basis_amount_g':'REAL', 'source_type':"TEXT NOT NULL DEFAULT 'unknown'", 'source_reference':'TEXT', 'verified_at':'TEXT', 'confidence':'REAL'},
        'unit_conversions': {'from_unit':'TEXT', 'to_unit':'TEXT', 'factor':'REAL', 'confidence':'REAL', 'note':'TEXT', 'source_type':"TEXT NOT NULL DEFAULT 'unknown'"},
        'inventory_items': {'original_name':'TEXT', 'quantity':'REAL', 'unit':'TEXT', 'data_status':"TEXT NOT NULL DEFAULT 'unknown'", 'product_name':'TEXT', 'label_confirmed':'INTEGER NOT NULL DEFAULT 0', 'expiry_date':'TEXT'},
        'rag_documents': {'confidence':'REAL'}
    }
    for table, definitions in migrations.items():
        present={row['name'] for row in db.execute(f'PRAGMA table_info({table})')}
        for name, definition in definitions.items():
            if name not in present: db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
    db.execute('UPDATE ingredients SET canonical_name=name WHERE canonical_name IS NULL')
    columns = {row['name'] for row in db.execute('PRAGMA table_info(inventory_items)')}
    for name in ('received_at', 'opened_at', 'storage_location', 'package_state'):
        if name not in columns:
            db.execute(f'ALTER TABLE inventory_items ADD COLUMN {name} TEXT')
    doc_columns = {row['name'] for row in db.execute('PRAGMA table_info(rag_documents)')}
    if 'version' not in doc_columns:
        db.execute("ALTER TABLE rag_documents ADD COLUMN version TEXT NOT NULL DEFAULT 'demo-1'")
    guideline_columns = {row['name'] for row in db.execute('PRAGMA table_info(ingredient_storage_guidelines)')}
    for name in ('unopened_days', 'opened_days', 'frozen_days'):
        if name not in guideline_columns:
            db.execute(f'ALTER TABLE ingredient_storage_guidelines ADD COLUMN {name} INTEGER')
    if 'recommended_storage' not in guideline_columns:
        db.execute('ALTER TABLE ingredient_storage_guidelines ADD COLUMN recommended_storage TEXT')
    # Older previews used the shared-feedback table name for account-specific rows.
    feedback_columns = {row['name'] for row in db.execute('PRAGMA table_info(meal_feedback)')}
    if 'account_id' in feedback_columns:
        db.execute('ALTER TABLE meal_feedback RENAME TO meal_account_feedback')
    from .data_platform import migrate
    migrate(db)
    request_columns = {row['name'] for row in db.execute('PRAGMA table_info(recommendation_requests)')}
    for name, definition in {'nutrition_data_release_id':'INTEGER', 'allergen_reference_version_id':'INTEGER'}.items():
        if name not in request_columns:
            db.execute(f'ALTER TABLE recommendation_requests ADD COLUMN {name} {definition}')
    db.execute('INSERT OR IGNORE INTO data_release_pointers(id) VALUES (1)')
    db.commit()
    if db.execute('SELECT 1 FROM households LIMIT 1').fetchone(): return
    mode = db.execute("SELECT value FROM metadata WHERE key='dataset_mode'").fetchone()
    if seed_demo is None:
        seed_demo = os.getenv('KINI_SEED_DEMO', '1') == '1'
    if not seed_demo or (mode and mode[0] == 'private'):
        db.execute("INSERT OR REPLACE INTO metadata VALUES ('dataset_mode','private')")
        db.commit()
        return
    seed=json.loads((DATA/'demo_household.json').read_text(encoding='utf-8-sig'))
    with db:
        db.execute('INSERT INTO households VALUES (?,?)',(1,seed['household']['name']))
        for m in seed['members']:
            db.execute('INSERT INTO members(id,household_id,name,age) VALUES (?,?,?,?)',(m['id'],1,m['name'],m['age']))
            for s in m['strategies']: db.execute('INSERT INTO member_preferences VALUES (?,?,?)',(m['id'],'strategy',s))
            for s in m.get('dislikes',[]): db.execute('INSERT INTO member_preferences VALUES (?,?,?)',(m['id'],'dislike',s))
            for s in m.get('allergies',[]): db.execute('INSERT INTO member_allergies VALUES (?,?)',(m['id'],s))
            if m.get('sodium_limit_mg'): db.execute('INSERT INTO member_health_constraints VALUES (?,?,?)',(m['id'],'meal_sodium_max_mg',str(m['sodium_limit_mg'])))
            for n,v in [('energy_kcal',1800 if m['id'] != 3 else 1500),('protein_g',60 if m['id'] != 3 else 45),('sodium_mg',2000)]: db.execute('INSERT INTO member_nutrition_targets VALUES (?,?,?)',(m['id'],n,v))
        # Illustrative values per 100 g, not clinical nutrition data.
        ingredients=[('쌀','grain',130,2.7,28,0.3,1),('닭고기','protein',165,31,0,3.6,74),('당근','vegetable',41,0.9,10,0.2,69),('두부','protein',76,8,1.9,4.8,7),('버섯','vegetable',22,3.1,3.3,0.3,5),('새우','새우',99,24,0.2,0.3,111),('견과류','견과류',600,20,20,50,5),('간장','seasoning',53,8,5,0,5500),('피망','vegetable',20,0.9,4.6,0.2,3)]
        for i,(name,cat,e,p,c,f,s) in enumerate(ingredients,1):
            db.execute("INSERT INTO ingredients(id,name,category,canonical_name,source_type,data_confidence) VALUES (?,?,?,?,?,?)",(i,name,cat,name,'demo',.5)); db.execute("INSERT INTO ingredient_nutrition(ingredient_id,energy_kcal,protein_g,carbohydrate_g,fat_g,sodium_mg,basis_amount_g,source_type,confidence) VALUES (?,?,?,?,?,?,?,?,?)",(i,e,p,c,f,s,100,'demo',.5))
            db.execute("INSERT INTO inventory_items(household_id,ingredient_id,quantity_g,freshness_state,original_name,quantity,unit,data_status) VALUES (?,?,?,?,?,?,?,?)",(1,i,1000,'use_soon' if name=='당근' else 'good',name,1000,'g','verified'))
        db.executemany("INSERT INTO ingredient_allergen_tags(ingredient_id,allergen_code,source_type,confidence) VALUES (?,?,?,?)",[(6,'새우','demo',.5),(7,'견과류','demo',.5)])
        db.execute('UPDATE ingredients SET is_composite=1,requires_label_check=1 WHERE id=8')
        recipes=[(1,'닭고기 당근밥',25,[(1,400),(2,400),(3,300)]),(2,'두부 버섯밥',20,[(1,400),(4,400),(5,300)]),(3,'새우볶음밥',20,[(1,400),(6,400),(3,200)]),(4,'견과류 두부밥',15,[(1,400),(4,400),(7,100)]),(5,'짭짤한 닭고기밥',25,[(1,400),(2,400),(8,100)])]
        for rid,name,t,items in recipes:
            db.execute('INSERT INTO recipes VALUES (?,?,?,?,?)',(rid,name,4,t,0))
            db.executemany('INSERT INTO recipe_ingredients VALUES (?,?,?)',[(rid,i,q) for i,q in items])
        docs=[('식재료 보관과 상태 확인','표시된 보관법과 소비기한을 확인하고 변질이 의심되면 버립니다.'),('저염 조리 일반 가이드','소스는 따로 내고 향신채로 풍미를 더합니다.'),('알레르기 대체 조리','대체 재료와 도구의 교차 접촉 및 제품 표시를 확인합니다.'),('조리 단위 변환','식재료마다 부피와 무게가 다르므로 실제 무게를 측정합니다.'),('조리 중 FAQ','충분히 익히고 생식재료와 조리된 음식의 도구를 분리합니다.')]
        for title,content in docs:
            cur=db.execute('INSERT INTO rag_documents(title,source) VALUES (?,?)',(title,'데모 일반 안내'))
            db.execute('INSERT INTO rag_chunks(document_id,content) VALUES (?,?)',(cur.lastrowid,content))
        db.execute("INSERT INTO metadata VALUES ('db_version','demo-1')")

def open_db(path=None, *, seed_demo=None):
    db=connect(path)
    if os.getenv('KINI_DB_BACKEND', 'sqlite').lower() == 'postgres':
        # Production connections are schema-only at runtime. Migrations and
        # demo fixtures are explicit operator actions, never request actions.
        return db
    try:
        initialize(db, seed_demo=seed_demo)
    except Exception:
        db.close()
        raise
    return db

def recipe_candidates(db):
    """Public candidate rows only; caller still applies local safety rules."""
    return [dict(row) for row in db.execute('SELECT * FROM recipes ORDER BY id')]


def recipe_ingredient_rows(db, recipe_id):
    return [dict(row) for row in db.execute('''SELECT ri.quantity_g,i.id,i.name,i.category,
        n.energy_kcal,n.protein_g,n.carbohydrate_g,n.fat_g,n.sodium_mg
        FROM recipe_ingredients ri JOIN ingredients i ON i.id=ri.ingredient_id
        LEFT JOIN ingredient_nutrition n ON n.ingredient_id=i.id
        WHERE recipe_id=? ORDER BY i.id''', (recipe_id,))]


def ingredient_nutrition_row(db, ingredient_id):
    return db.execute('''SELECT i.*,n.basis_amount_g,n.source_type AS nutrition_source,
        n.confidence AS nutrition_confidence,n.energy_kcal,n.protein_g,n.carbohydrate_g,n.fat_g,n.sodium_mg
        FROM ingredients i LEFT JOIN ingredient_nutrition n ON n.ingredient_id=i.id WHERE i.id=?''',
        (ingredient_id,)).fetchone()


def members(db, household_id, ids=None, *, include_physical_profile=False):
    columns = 'id,household_id,name,age'
    if include_physical_profile:
        columns += ',height_cm,weight_kg,activity_level'
    rows = db.execute(f'SELECT {columns} FROM members WHERE household_id=? ORDER BY id', (household_id,)).fetchall()
    if ids is not None: rows=[r for r in rows if r['id'] in ids]
    result=[]
    for r in rows:
        m=dict(r); mid=m['id']
        m['preferences']=[dict(x) for x in db.execute('SELECT kind,value FROM member_preferences WHERE member_id=? ORDER BY kind,value',(mid,))]
        m['allergies']=[x[0] for x in db.execute('SELECT ingredient FROM member_allergies WHERE member_id=? ORDER BY ingredient',(mid,))]
        m['constraints']=[dict(x) for x in db.execute('SELECT kind,value FROM member_health_constraints WHERE member_id=? ORDER BY kind',(mid,))]
        m['targets']={x['nutrient']:x['amount'] for x in db.execute('SELECT nutrient,amount FROM member_nutrition_targets WHERE member_id=?',(mid,))}
        result.append(m)
    return result
