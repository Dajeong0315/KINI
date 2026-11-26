"""Cooking draft scope on real loopback HTTP and disposable PostgreSQL."""
import json
import os
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
import subprocess
import sys
from threading import Barrier
from uuid import uuid4

import pytest

from app.services.catalog_repository import stage, validate, publish
from test_recommendation_read_postgres import food
from test_recommendation_rules_postgres import rule_db, seed_family, request as request_body
from test_recommendation_http_postgres import http

pytestmark = pytest.mark.skipif(os.getenv('KINI_DB_BACKEND') != 'postgres', reason='requires disposable PostgreSQL')


def sources(api, auth, participants=None):
    body = request_body()
    if participants is not None: body['participant_ids'] = participants
    rec = api('POST', '/api/recommendations', body, auth)
    assert rec.status_code == 201 and rec.json()['status'] == 'recommendations_available'
    meal = api('POST', '/api/meal-sessions', body, auth)
    assert meal.status_code == 201
    return {'meal_session_id':meal.json()['session_id'], 'recommendation_request_id':rec.json()['request_id'], 'recipe_id':501}


def draft(api, auth, participants=None):
    response = api('POST', '/api/cooking-sessions', sources(api, auth, participants), auth)
    assert response.status_code == 201
    return response.json()


def stock_state(db):
    return {
        'items': db.execute('SELECT id,household_id,ingredient_id,quantity_g FROM inventory_items ORDER BY id').fetchall(),
        'events': db.execute('SELECT * FROM inventory_events ORDER BY id').fetchall(),
        'changes': db.execute('SELECT * FROM inventory_event_changes ORDER BY event_id').fetchall(),
        'versions': db.execute('SELECT * FROM inventory_sync_state ORDER BY household_id').fetchall(),
        'meals': db.execute('SELECT * FROM meal_records ORDER BY id').fetchall(),
        'intakes': db.execute('SELECT * FROM nutrition_intakes ORDER BY record_item_id,nutrient').fetchall(),
    }


def add_ingredient(db):
    db.execute("INSERT INTO ingredients(id,name,canonical_name,category,source_type,data_confidence) VALUES (902,'synthetic added','synthetic added','vegetable','verified_product',1)")
    db.execute("INSERT INTO ingredient_nutrition(ingredient_id,energy_kcal,protein_g,carbohydrate_g,fat_g,sodium_mg,basis_amount_g,source_type,confidence) VALUES (902,200,20,10,0,10,100,'verified_product',1)")
    db.execute("INSERT INTO inventory_items(id,household_id,ingredient_id,quantity_g,quantity,unit,freshness_state,data_status,use_by) VALUES (1002,1,902,1000,1000,'g','good','verified','2099-01-01')")
    db.commit()


def test_create_query_change_and_remove_without_stock_or_intake_writes(http, rule_db, caplog):
    api, login = http; auth = login()
    add_ingredient(rule_db)
    before = stock_state(rule_db)
    created = draft(api, auth); sid = created['id']
    assert created['planned_servings'] == 1 and created['status'] == 'active'
    assert created['ingredients'] == [{'ingredient_id':901,'quantity_g':100}]
    assert created['nutrition_per_serving']['protein_g'] == 10
    assert api('GET',f'/api/cooking-sessions/{sid}',auth=auth).json() == created
    changed = api('POST',f'/api/cooking-sessions/{sid}/ingredients',{'ingredient':'synthetic added','quantity_g':50},auth)
    assert changed.status_code == 200 and changed.json()['nutrition_per_serving']['protein_g'] == 20
    removed = api('POST',f'/api/cooking-sessions/{sid}/ingredients',{'ingredient':'synthetic added','quantity_g':0},auth)
    assert removed.status_code == 200 and removed.json()['ingredients'] == created['ingredients']
    assert stock_state(rule_db) == before
    assert api('GET',f'/api/cooking-sessions/{sid}',auth=auth).headers['Cache-Control'] == 'no-store'
    assert auth['Authorization'][7:] not in caplog.text


def test_multiple_participants_scale_total_grams_not_per_serving(http, rule_db):
    rule_db.execute("INSERT INTO members(id,household_id,name,age) VALUES (12,1,'synthetic child',10)"); rule_db.commit()
    api, login = http
    before = stock_state(rule_db)
    created = draft(api, login(), [11,12])
    assert created['planned_servings'] == 2
    assert created['ingredients'] == [{'ingredient_id':901,'quantity_g':200}]
    assert created['nutrition_per_serving']['energy_kcal'] == 100
    assert stock_state(rule_db) == before


def test_permissions_and_cross_household_source_ids(http, rule_db):
    api, login = http; owner = login(); viewer = login('rec-viewer'); other = login('rec-other',2)
    body = sources(api, owner)
    assert api('POST','/api/meal-sessions',request_body()).status_code == 401
    assert api('POST','/api/meal-sessions',request_body(),viewer).status_code == 403
    assert api('POST','/api/meal-sessions',request_body(),other).status_code == 403
    assert api('POST','/api/cooking-sessions',body).status_code == 401
    assert api('POST','/api/cooking-sessions',body,viewer).status_code == 403
    assert api('POST','/api/cooking-sessions',body,other).status_code == 403
    created = api('POST','/api/cooking-sessions',body,owner).json(); sid = created['id']
    for auth, status in [(None,401),(other,403),(viewer,200)]:
        assert api('GET',f'/api/cooking-sessions/{sid}',auth=auth).status_code == status
    for auth, status in [(None,401),(other,403),(viewer,403)]:
        assert api('POST',f'/api/cooking-sessions/{sid}/ingredients',{'ingredient':'synthetic','quantity_g':80},auth).status_code == status
    assert api('GET',f'/api/cooking-sessions/{sid}',auth=owner).json()['ingredients'] == created['ingredients']


@pytest.mark.parametrize('mode', ['catalog', 'official', 'participants', 'candidate', 'allergy', 'missing_mass', 'invalid_servings'])
def test_invalid_sources_do_not_create_cooking_session(http, rule_db, mode):
    api, login = http; auth = login(); body = sources(api, auth)
    before = stock_state(rule_db)
    if mode == 'catalog':
        release = stage(rule_db,'mfds_nutrition',[food(200)],source_revision='next')
        assert not validate(rule_db,release); publish(rule_db,release)
    elif mode == 'official':
        rule_db.execute("INSERT INTO data_sources(id,source_key,provider_name,dataset_name,source_url,expected_format) VALUES (90,'synthetic-cook','synthetic','synthetic','https://example.invalid','json')")
        rule_db.execute("INSERT INTO dataset_releases(id,data_source_id,checksum,status) VALUES (91,90,'synthetic','published')")
        rule_db.execute('UPDATE data_release_pointers SET nutrition_release_id=91')
    elif mode == 'participants':
        rule_db.execute("INSERT INTO members(id,household_id,name,age) VALUES (12,1,'synthetic child',10)")
        rule_db.execute('UPDATE meal_session_participants SET member_id=12 WHERE session_id=?', (body['meal_session_id'],))
    elif mode == 'candidate': body['recipe_id'] = 99999
    elif mode == 'allergy': rule_db.execute("INSERT INTO member_allergies VALUES (11,'synthetic')")
    elif mode == 'missing_mass': rule_db.execute('UPDATE recipe_ingredients SET quantity_g=NULL')
    else: rule_db.execute('UPDATE recipes SET servings=?',(float('nan'),))
    rule_db.commit()
    result = api('POST','/api/cooking-sessions',body,auth)
    assert result.status_code == 400
    assert rule_db.execute('SELECT COUNT(*) FROM cooking_sessions').fetchone()[0] == 0
    assert stock_state(rule_db) == before


@pytest.mark.parametrize('changes', [
    {'ingredient':'unknown','quantity_g':1}, {'ingredient':'synthetic','quantity_g':-1},
    {'ingredient':'synthetic','quantity_g':10001}, {'ingredient':'synthetic','quantity_g':None},
    {'ingredient':'synthetic','quantity_g':'NaN'}, {'ingredient':'synthetic','quantity_g':'Infinity'},
    {'ingredient':'synthetic','quantity_g':1,'unit':'g'}, {'ingredient':'synthetic','quantity_g':0},
])
def test_invalid_changes_preserve_last_draft(http, rule_db, changes):
    api, login = http; auth = login(); created = draft(api,auth); sid = created['id']
    before = stock_state(rule_db)
    assert api('POST',f'/api/cooking-sessions/{sid}/ingredients',changes,auth).status_code == 400
    assert api('GET',f'/api/cooking-sessions/{sid}',auth=auth).json() == created
    assert rule_db.execute('SELECT COUNT(*) FROM preference_evidence').fetchone()[0] == 0
    assert stock_state(rule_db) == before


@pytest.mark.parametrize('condition', ['allergy','sodium','expiry','guideline'])
def test_changed_ingredient_is_revalidated(http, rule_db, condition):
    api, login = http; auth = login(); add_ingredient(rule_db)
    created = draft(api,auth); sid = created['id']
    if condition == 'allergy': rule_db.execute("INSERT INTO member_allergies VALUES (11,'synthetic added')")
    elif condition == 'sodium': rule_db.execute("INSERT INTO member_health_constraints VALUES (11,'meal_sodium_max_mg','1')")
    elif condition == 'expiry': rule_db.execute("UPDATE inventory_items SET use_by='2000-01-01' WHERE ingredient_id=902")
    else:
        rule_db.execute('UPDATE inventory_items SET received_at=? WHERE ingredient_id=902',((date.today()-timedelta(days=10)).isoformat(),))
        rule_db.execute("INSERT INTO ingredient_storage_guidelines(ingredient_id,guideline,unopened_days) VALUES (902,'latest',1)")
    rule_db.commit()
    before = stock_state(rule_db)
    assert api('POST',f'/api/cooking-sessions/{sid}/ingredients',{'ingredient':'synthetic added','quantity_g':50},auth).status_code == 400
    assert api('GET',f'/api/cooking-sessions/{sid}',auth=auth).json()['ingredients'] == created['ingredients']
    assert stock_state(rule_db) == before


def test_change_failure_rolls_back_draft_evidence_and_summary(http, rule_db):
    api, login = http; auth = login(); add_ingredient(rule_db)
    created = draft(api,auth); sid = created['id']; before = stock_state(rule_db)
    rule_db.execute("""CREATE FUNCTION fail_cooking_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'synthetic cooking evidence failure'; END $$""")
    rule_db.execute('CREATE TRIGGER fail_cooking_evidence BEFORE INSERT ON preference_evidence FOR EACH ROW EXECUTE FUNCTION fail_cooking_evidence()'); rule_db.commit()
    try:
        assert api('POST',f'/api/cooking-sessions/{sid}/ingredients',{'ingredient':'synthetic added','quantity_g':50},auth).status_code == 500
        assert api('GET',f'/api/cooking-sessions/{sid}',auth=auth).json() == created
        assert rule_db.execute('SELECT COUNT(*) FROM preference_evidence').fetchone()[0] == 0
        assert rule_db.execute('SELECT COUNT(*) FROM household_behavior_summary').fetchone()[0] == 0
        assert stock_state(rule_db) == before
    finally:
        rule_db.execute('DROP TRIGGER fail_cooking_evidence ON preference_evidence')
        rule_db.execute('DROP FUNCTION fail_cooking_evidence()'); rule_db.commit()
    assert api('POST',f'/api/cooking-sessions/{sid}/ingredients',{'ingredient':'synthetic added','quantity_g':50},auth).status_code == 200


def test_concurrent_edits_are_serialized_without_lost_ingredients(http, rule_db):
    api, login = http; auth = login(); add_ingredient(rule_db)
    created = draft(api,auth); sid = created['id']; before = stock_state(rule_db)
    barrier = Barrier(2)
    def edit(body):
        barrier.wait(timeout=5)
        return api('POST',f'/api/cooking-sessions/{sid}/ingredients',body,auth).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(edit,[{'ingredient':'synthetic','quantity_g':80},{'ingredient':'synthetic added','quantity_g':50}])) == [200,200]
    final = api('GET',f'/api/cooking-sessions/{sid}',auth=auth).json()
    assert final['ingredients'] == [{'ingredient_id':901,'quantity_g':80},{'ingredient_id':902,'quantity_g':50}]
    assert final['nutrition_per_serving']['protein_g'] == 18
    assert stock_state(rule_db) == before


def test_completed_draft_cannot_be_changed_and_completion_stays_out_of_scope(http, rule_db):
    api, login = http; auth = login(); created = draft(api,auth); sid = created['id']
    rule_db.execute("UPDATE cooking_sessions SET status='completed' WHERE id=?",(sid,)); rule_db.commit()
    before = stock_state(rule_db)
    assert api('POST',f'/api/cooking-sessions/{sid}/ingredients',{'ingredient':'synthetic','quantity_g':80},auth).status_code == 400
    assert api('POST',f'/api/cooking-sessions/{sid}/complete',{},auth).status_code == 501
    assert stock_state(rule_db) == before


def test_migration_005_preserves_existing_recommendation_meal_and_inventory():
    import psycopg
    from psycopg import sql
    from app.services.postgres_repository import PostgresConnection, hybrid_row
    from app.services.recommendation_engine import recommend
    root = Path(__file__).resolve().parents[1]
    schema = 'test_cooking_upgrade_' + uuid4().hex
    url = os.environ['KINI_DATABASE_URL']
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        try:
            raw = psycopg.connect(url, options=f'-c search_path={schema}', row_factory=hybrid_row)
            with closing(PostgresConnection(raw)) as db:
                for migration in sorted((root/'db/migrations').glob('*.sql')):
                    if migration.name >= '005_': break
                    with db.cursor() as cursor: cursor.execute(migration.read_text(encoding='utf-8'))
                    db.execute('INSERT INTO schema_migrations(version) VALUES (?) ON CONFLICT DO NOTHING',(migration.stem,))
                db.commit()
                seed_family(db)
                saved = recommend(db, request_body())
                meal_id = db.execute("INSERT INTO meal_sessions(household_id,meal_type) VALUES (1,'dinner')").lastrowid
                db.execute('INSERT INTO meal_session_participants VALUES (?,11)',(meal_id,)); db.commit()
                before = stock_state(db)
                stored = db.execute('SELECT result_json FROM recommendation_requests WHERE id=?',(saved['request_id'],)).fetchone()[0]
                db.commit()
                env = {**os.environ, 'PGOPTIONS': f'-c search_path={schema}'}
                applied = subprocess.run([sys.executable,str(root/'scripts/run_migrations.py')],env=env,capture_output=True,text=True)
                assert applied.returncode == 0 and '005_cooking_sessions' in applied.stdout
                repeated = subprocess.run([sys.executable,str(root/'scripts/run_migrations.py')],env=env,capture_output=True,text=True)
                assert repeated.returncode == 0 and 'already up to date' in repeated.stdout
                assert stock_state(db) == before
                assert db.execute('SELECT result_json FROM recommendation_requests WHERE id=?',(saved['request_id'],)).fetchone()[0] == stored
                assert db.execute('SELECT member_id FROM meal_session_participants WHERE session_id=?',(meal_id,)).fetchone()[0] == 11
                assert db.execute('SELECT COUNT(*) FROM cooking_sessions').fetchone()[0] == 0
        finally:
            admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
