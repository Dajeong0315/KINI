"""N2: full local rule computation on actual PostgreSQL, synthetic records only."""
import os
from contextlib import closing
from datetime import date, timedelta

import pytest

from app.services.recommendation_repository import open_db
from app.services.recommendation_engine import recommend
from app.services.personalization import add_evidence
from test_recommendation_read_postgres import seed

pytestmark = pytest.mark.skipif(os.getenv('KINI_DB_BACKEND') != 'postgres', reason='requires disposable PostgreSQL')


def seed_family(db):
    release = seed(db)
    db.execute("INSERT INTO metadata(key,value) VALUES ('db_version','synthetic-rules') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
    db.execute("INSERT INTO households(id,name) VALUES (1,'synthetic A'),(2,'synthetic B')")
    db.execute("INSERT INTO members(id,household_id,name,age) VALUES (11,1,'synthetic adult',40),(22,2,'synthetic other',30)")
    db.executemany('INSERT INTO member_nutrition_targets VALUES (?,?,?)', [(11, 'energy_kcal', 1800), (11, 'protein_g', 60), (11, 'sodium_mg', 2000)])
    db.execute("INSERT INTO inventory_items(id,household_id,ingredient_id,quantity_g,quantity,unit,freshness_state,data_status,use_by,received_at) VALUES (1001,1,901,1000,1000,'g','good','verified','2099-01-01',?)", (date.today().isoformat(),))
    db.commit()
    return release


@pytest.fixture
def rule_db():
    with closing(open_db()) as db:
        db.execute('TRUNCATE local_accounts, households, active_data_releases, meal_data_context, catalog_data_sources, ingredients, recipes CASCADE')
        db.executemany('INSERT INTO catalog_data_sources VALUES (?,?,?,?)', [(s,s,'https://example.invalid','review_required') for s in ('mfds_nutrition','mfds_recipe','10000recipe')])
        db.execute('UPDATE data_release_pointers SET nutrition_release_id=NULL,allergen_reference_version_id=NULL')
        db.commit()
        seed_family(db)
        yield db


def request():
    return dict(household_id=1, participant_ids=[11], meal_type='dinner', purchase_allowed=False)


def test_rule_scores_nutrition_and_sqlite_parity(rule_db, tmp_path, monkeypatch):
    result = recommend(rule_db, request(), persist=False)
    assert result['status'] == 'recommendations_available'
    assert result['recommendations'][0]['recipe_id'] == 501
    assert result['recommendations'][0]['nutrition_per_serving']['energy_kcal'] == 100
    assert rule_db.execute('SELECT COUNT(*) FROM recommendation_requests').fetchone()[0] == 0
    monkeypatch.setenv('KINI_DB_BACKEND', 'sqlite')
    with closing(open_db(str(tmp_path/'rules.sqlite3'), seed_demo=False)) as sqlite:
        seed_family(sqlite)
        other = recommend(sqlite, request(), persist=False)
        assert other['status'] == result['status']
        assert other['recommendations'] == result['recommendations']


@pytest.mark.parametrize('condition,code', [
    ('allergy','allergen_conflict'), ('expired','freshness_unsafe'),
    ('missing_nutrition','nutrition_unknown'), ('missing_mass','unit_confirmation_required'),
    ('missing_stock','inventory_insufficient'), ('sodium','health_constraint'),
    ('excluded','user_exclusion'),
])
def test_rule_exclusions_are_not_bypassed(rule_db, condition, code):
    req = request()
    if condition == 'allergy': rule_db.execute("INSERT INTO member_allergies VALUES (11,'synthetic')")
    elif condition == 'expired': rule_db.execute("UPDATE inventory_items SET use_by='2000-01-01'")
    elif condition == 'missing_nutrition':
        rule_db.execute('DELETE FROM catalog_ingredient_links')
        rule_db.execute('UPDATE ingredient_nutrition SET energy_kcal=NULL')
    elif condition == 'missing_mass': rule_db.execute('UPDATE recipe_ingredients SET quantity_g=NULL')
    elif condition == 'missing_stock': rule_db.execute('UPDATE inventory_items SET quantity_g=1')
    elif condition == 'sodium':
        rule_db.execute("INSERT INTO member_health_constraints VALUES (11,'meal_sodium_max_mg','0')")
        rule_db.execute("UPDATE catalog_food_nutrients SET amount=100 WHERE nutrient='sodium_mg'")
    else: req['excluded_ingredients'] = ['synthetic']
    rule_db.commit()
    result = recommend(rule_db, req, persist=False)
    assert result['status'] == 'no_safe_recommendation' and not result['recommendations']
    # Expired stock is unusable and consequently insufficient; preserve existing rule codes.
    codes = result['excluded'][0]['reason_codes']
    assert (code in codes) if condition != 'expired' else ('inventory_insufficient' in codes)


def test_personalization_null_member_history_and_household_isolation(rule_db):
    base = recommend(rule_db, request(), persist=False)['recommendations'][0]
    for _ in range(3):
        add_evidence(rule_db, 1, None, 'conversation', 'ingredient', 'synthetic', 'prefer')
    rule_db.commit()
    personalized = recommend(rule_db, request(), persist=False)['recommendations'][0]
    assert personalized['personalization_score'] > 0 and personalized['applied_preference_signals']
    rule_db.execute("INSERT INTO meal_sessions(id,household_id,meal_type) VALUES (8001,2,'dinner')")
    rule_db.execute('INSERT INTO meal_records(id,session_id,recipe_id,eaten_at) VALUES (8002,8001,501,?)', (date.today().isoformat(),))
    rule_db.commit()
    assert recommend(rule_db, request(), persist=False)['recommendations'][0]['scores']['variety'] == base['scores']['variety']
    rule_db.execute('UPDATE meal_sessions SET household_id=1 WHERE id=8001')
    rule_db.execute('INSERT INTO meal_record_items(id,record_id,member_id,servings) VALUES (8003,8002,11,1)')
    rule_db.execute("INSERT INTO nutrition_intakes VALUES (8003,'protein_g',20)")
    rule_db.commit()
    latest = recommend(rule_db, request(), persist=False)['recommendations'][0]
    assert latest['scores']['variety'] == 30
    assert latest['adjustments'][0]['remaining_targets']['protein_g'] == 40


def test_official_mapping_requires_published_current_release(rule_db):
    rule_db.execute('DELETE FROM catalog_ingredient_links')
    rule_db.execute("INSERT INTO data_sources(id,source_key,provider_name,dataset_name,source_url,expected_format) VALUES (70,'synthetic','synthetic','synthetic','https://example.invalid','json')")
    rule_db.execute("INSERT INTO dataset_releases(id,data_source_id,checksum,status) VALUES (71,70,'synthetic','published')")
    rule_db.execute("INSERT INTO official_food_items(id,release_id,source_food_code,food_name,normalized_name,basis_amount_g) VALUES (72,71,'F-OFFICIAL','synthetic official','synthetic official',100)")
    rule_db.executemany('INSERT INTO official_nutrient_values(official_food_item_id,nutrient_code,amount,unit) VALUES (72,?,?,?)', [(n,v,'kcal' if n=='energy_kcal' else 'mg' if n=='sodium_mg' else 'g') for n,v in [('energy_kcal',300),('protein_g',30),('carbohydrate_g',10),('fat_g',0),('sodium_mg',0)]])
    rule_db.execute("INSERT INTO ingredient_official_food_mappings(ingredient_id,official_food_item_id,mapping_method,mapping_confidence) VALUES (901,72,'manual',1)")
    rule_db.execute('UPDATE data_release_pointers SET nutrition_release_id=71'); rule_db.commit()
    official = recommend(rule_db, request(), persist=False)['recommendations'][0]
    assert official['nutrition_per_serving']['energy_kcal'] == 300
    assert official['inventory_usage'][0]['nutrition_source'] == 'official_imported'
    rule_db.execute("UPDATE dataset_releases SET status='retired'"); rule_db.commit()
    fallback = recommend(rule_db, request(), persist=False)['recommendations'][0]
    assert fallback['data_quality'] == 'demo_data' and fallback['nutrition_per_serving']['energy_kcal'] == 50


@pytest.mark.parametrize('target', ['stock_nan', 'stock_inf', 'servings_nan', 'servings_inf'])
def test_nonfinite_quantities_never_produce_candidates(rule_db, target):
    value = float('nan') if target.endswith('nan') else float('inf')
    if target.startswith('stock'):
        rule_db.execute('UPDATE inventory_items SET quantity_g=?', (value,))
    else:
        rule_db.execute('UPDATE recipes SET servings=?', (value,))
    rule_db.commit()
    assert recommend(rule_db, request(), persist=False)['recommendations'] == []


def test_latest_storage_guideline_is_used(rule_db):
    rule_db.execute('UPDATE inventory_items SET received_at=?', ((date.today()-timedelta(days=10)).isoformat(),))
    rule_db.execute("INSERT INTO ingredient_storage_guidelines(ingredient_id,guideline,unopened_days) VALUES (901,'older',100)")
    rule_db.execute("INSERT INTO ingredient_storage_guidelines(ingredient_id,guideline,unopened_days) VALUES (901,'latest',1)")
    rule_db.commit()
    result = recommend(rule_db, request(), persist=False)
    assert result['recommendations'] == []
    assert 'food_safety_confirmation_required' in result['excluded'][0]['reason_codes']
