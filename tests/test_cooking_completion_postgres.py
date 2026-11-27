"""Real A outflow -> B completion over loopback HTTP; stock is never deducted by B."""
import os
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
import subprocess
import sys
from threading import Barrier
from uuid import uuid4

import pytest

from app.services.postgres_repository import PostgresConnection
from test_recommendation_rules_postgres import rule_db, seed_family, request as request_body
from test_recommendation_http_postgres import http
from test_cooking_postgres_http import draft, add_ingredient

pytestmark = pytest.mark.skipif(os.getenv('KINI_DB_BACKEND') != 'postgres', reason='requires disposable PostgreSQL')


def snapshot(api, auth):
    response = api('GET','/api/data/inventory',auth=auth)
    assert response.status_code == 200 and response.json()['source'] == 'postgresql'
    return response.json()


def event(api, auth, *, action='stock_out', ingredient=901, lot=1001, quantity=100, confirm=True):
    received = api('POST','/api/data/inventory/events',{
        'client_event_id':str(uuid4()),'source':'manual','action':action,'detected_label':'synthetic',
    },auth)
    assert received.status_code == 200
    eid = received.json()['event_id']
    if confirm:
        body = {'ingredient_id':ingredient,'quantity_g':quantity}
        if action == 'stock_out': body['inventory_item_id'] = lot
        applied = api('POST',f'/api/data/inventory/events/{eid}/confirm',body,auth)
        assert applied.status_code == 200
    return eid


def payload(created, event_ids, portions=None):
    return {'confirmed':True,'eaten_at':date.today().isoformat()+'T12:00:00+09:00',
            'items': portions if portions is not None else [{'member_id':11,'servings':1}],
            'inventory_event_ids':event_ids}


def complete(api, auth, created, event_ids, portions=None):
    return api('POST',f"/api/cooking-sessions/{created['id']}/complete",payload(created,event_ids,portions),auth)


def b_state(db):
    return {table:[dict(row) for row in db.execute(f'SELECT * FROM {table} ORDER BY {key}')]
            for table,key in [('meal_records','id'),('meal_record_items','id'),
                              ('nutrition_intakes','record_item_id,nutrient'),
                              ('cooking_inventory_event_links','event_id'),('preference_evidence','id'),
                              ('inferred_preferences','id'),('household_behavior_summary','household_id'),
                              ('cooking_sessions','id')]}


@pytest.mark.parametrize('servings', [[0],[0.5],[1],[1.5,0],[1,1]])
def test_a_deducts_once_b_records_actual_member_intake(http, rule_db, servings):
    api, login = http; auth = login()
    ids = [11]
    if len(servings)==2:
        rule_db.execute("INSERT INTO members(id,household_id,name,age) VALUES (12,1,'synthetic child',10)"); rule_db.commit()
        ids.append(12)
    created = draft(api,auth,ids)
    before = snapshot(api,auth)
    assert before['items'][0]['quantity_g'] == 1000 and before['inventory_version'] == 0
    eid = event(api,auth,quantity=created['ingredients'][0]['quantity_g'],confirm=False)
    pending = snapshot(api,auth)
    assert pending['items'] == before['items'] and pending['inventory_version'] == 0
    confirm_body = {'ingredient_id':901,'inventory_item_id':1001,'quantity_g':created['ingredients'][0]['quantity_g']}
    assert api('POST',f'/api/data/inventory/events/{eid}/confirm',confirm_body,auth).status_code == 200
    after_a = snapshot(api,auth)
    assert after_a['items'][0]['quantity_g'] == 1000-100*len(ids)
    assert after_a['inventory_version'] == 1
    assert api('POST',f'/api/data/inventory/events/{eid}/confirm',confirm_body,auth).status_code == 200
    assert snapshot(api,auth) == after_a
    portions = [{'member_id':mid,'servings':amount} for mid,amount in zip(ids,servings)]
    result = complete(api,auth,created,[eid],portions)
    assert result.status_code == 201
    assert snapshot(api,auth) == after_a
    rid = result.json()['record_id']
    stored = rule_db.execute('''SELECT member_id,nutrient,amount FROM meal_record_items
        JOIN nutrition_intakes ON record_item_id=meal_record_items.id WHERE record_id=?''',(rid,)).fetchall()
    assert len(stored) == 5*len(ids)
    for row in stored:
        actual_servings = dict(zip(ids,servings))[row['member_id']]
        assert row['amount'] == round(created['nutrition_per_serving'][row['nutrient']]*actual_servings,2)
    assert rule_db.execute('SELECT status,meal_record_id FROM cooking_sessions WHERE id=?',(created['id'],)).fetchone()['meal_record_id'] == rid
    saved = b_state(rule_db)
    assert complete(api,auth,created,[eid],portions).status_code == 400
    assert b_state(rule_db) == saved and snapshot(api,auth) == after_a
    if not any(servings):
        assert not saved['preference_evidence']


def test_completion_requires_household_and_write_authority(http, rule_db):
    api, login = http; auth = login(); created = draft(api,auth)
    eid = event(api,auth); before = snapshot(api,auth); saved = b_state(rule_db)
    for actor, status in [(None,401),(login('rec-viewer'),403),(login('rec-other',2),403),
                          ({'Authorization':'Bearer invalid-synthetic-token'},403)]:
        assert complete(api,actor,created,[eid]).status_code == status
    assert b_state(rule_db) == saved and snapshot(api,auth) == before


@pytest.mark.parametrize('kind', ['missing','pending','rejected','stock_in','foreign','short','wrong_ingredient','duplicate','nonexistent'])
def test_invalid_outflows_cannot_be_linked(http, rule_db, kind):
    api, login = http; auth = login(); created = draft(api,auth)
    if kind == 'missing': ids = []
    elif kind == 'nonexistent': ids = [9999999]
    elif kind in ('pending','rejected'):
        ids = [event(api,auth,confirm=False)]
        if kind == 'rejected': assert api('POST',f'/api/data/inventory/events/{ids[0]}/reject',auth=auth).status_code == 200
    elif kind == 'stock_in': ids = [event(api,auth,action='stock_in')]
    elif kind == 'foreign':
        other = login('rec-other',2)
        incoming = event(api,other,action='stock_in',quantity=1000)
        lot = api('POST',f'/api/data/inventory/events/{incoming}/confirm',{'ingredient_id':901,'quantity_g':1000},other).json()['inventory_item_id']
        ids = [event(api,other,lot=lot)]
    elif kind == 'wrong_ingredient':
        add_ingredient(rule_db); ids = [event(api,auth,ingredient=902,lot=1002)]
    else:
        ids = [event(api,auth,quantity=99 if kind=='short' else 100)]
        if kind == 'duplicate': ids *= 2
    after_a = snapshot(api,auth); saved = b_state(rule_db)
    result = complete(api,auth,created,ids)
    assert result.status_code == 409 and result.json()['code'] == 'inventory_confirmation_required'
    assert b_state(rule_db) == saved and snapshot(api,auth) == after_a


def test_same_outflow_cannot_complete_a_second_draft(http, rule_db):
    api, login = http; auth = login(); first = draft(api,auth); second = draft(api,auth)
    eid = event(api,auth)
    assert complete(api,auth,first,[eid]).status_code == 201
    saved = b_state(rule_db); after_a = snapshot(api,auth)
    assert complete(api,auth,second,[eid]).status_code == 409
    assert b_state(rule_db) == saved and snapshot(api,auth) == after_a


@pytest.mark.parametrize('bad', ['duplicate_member','foreign_member','negative','excess','nan','empty','timestamp','unconfirmed'])
def test_invalid_intake_does_not_undo_a_or_write_b(http, rule_db, bad):
    api, login = http; auth = login(); created = draft(api,auth); eid = event(api,auth)
    body = payload(created,[eid])
    if bad == 'duplicate_member': body['items'] *= 2
    elif bad == 'foreign_member': body['items'][0]['member_id'] = 22
    elif bad == 'negative': body['items'][0]['servings'] = -1
    elif bad == 'excess': body['items'][0]['servings'] = 1.5
    elif bad == 'nan': body['items'][0]['servings'] = 'NaN'
    elif bad == 'empty': body['items'] = []
    elif bad == 'timestamp': body['eaten_at'] = 'not-a-time'
    else: body['confirmed'] = False
    after_a = snapshot(api,auth); saved = b_state(rule_db)
    assert api('POST',f"/api/cooking-sessions/{created['id']}/complete",body,auth).status_code == 400
    assert b_state(rule_db) == saved and snapshot(api,auth) == after_a


def test_split_lots_and_a_return_are_accounted_for_only_by_a(http, rule_db):
    api, login = http; auth = login(); created = draft(api,auth)
    incoming = event(api,auth,action='stock_in',quantity=200)
    second_lot = api('POST',f'/api/data/inventory/events/{incoming}/confirm',{'ingredient_id':901,'quantity_g':200},auth).json()['inventory_item_id']
    ids = [event(api,auth,quantity=40),event(api,auth,lot=second_lot,quantity=110)]
    event(api,auth,action='stock_in',quantity=50)  # explicit A return of unused mass
    after_a = snapshot(api,auth)
    assert sum(item['quantity_g'] for item in after_a['items']) == 1100
    assert after_a['inventory_version'] == 4
    assert complete(api,auth,created,ids).status_code == 201
    assert snapshot(api,auth) == after_a
    assert rule_db.execute('SELECT COUNT(*) FROM cooking_inventory_event_links').fetchone()[0] == 2


@pytest.mark.parametrize('target', ['same_session','shared_events','edit'])
def test_concurrent_completion_is_single_use(http, rule_db, target):
    api, login = http; auth = login(); first = draft(api,auth)
    second = first if target!='shared_events' else draft(api,auth)
    eid = event(api,auth); after_a = snapshot(api,auth); barrier = Barrier(2)
    def run(which):
        barrier.wait(timeout=5)
        if target=='edit' and which==1:
            return api('POST',f"/api/cooking-sessions/{first['id']}/ingredients",{'ingredient':'synthetic','quantity_g':80},auth).status_code
        return complete(api,auth,first if which==0 else second,[eid]).status_code
    with ThreadPoolExecutor(max_workers=2) as pool: statuses = list(pool.map(run,[0,1]))
    if target=='edit': assert statuses[0] == 201 and statuses[1] in (200,400)
    else: assert sorted(statuses) == [201,400 if target=='same_session' else 409]
    assert snapshot(api,auth) == after_a
    assert rule_db.execute('SELECT COUNT(*) FROM meal_records').fetchone()[0] == 1
    assert rule_db.execute('SELECT COUNT(*) FROM cooking_inventory_event_links').fetchone()[0] == 1


@pytest.mark.parametrize('stage', ['record','item','intake','evidence','finish'])
def test_b_failure_rolls_back_all_b_writes_but_not_confirmed_a(http, rule_db, stage):
    api, login = http; auth = login(); created = draft(api,auth); eid = event(api,auth)
    table = {'record':'meal_records','item':'meal_record_items','intake':'nutrition_intakes',
             'evidence':'preference_evidence','finish':'cooking_sessions'}[stage]
    action = 'UPDATE' if stage=='finish' else 'INSERT'
    condition = "WHEN (NEW.status='completed')" if stage=='finish' else "WHEN (NEW.nutrient='sodium_mg')" if stage=='intake' else ''
    rule_db.execute("""CREATE FUNCTION fail_completion_write() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'synthetic completion failure'; END $$""")
    rule_db.execute(f'CREATE TRIGGER fail_completion_write BEFORE {action} ON {table} FOR EACH ROW {condition} EXECUTE FUNCTION fail_completion_write()'); rule_db.commit()
    after_a = snapshot(api,auth); saved = b_state(rule_db)
    try:
        assert complete(api,auth,created,[eid]).status_code == 500
        assert b_state(rule_db) == saved and snapshot(api,auth) == after_a
    finally:
        rule_db.execute(f'DROP TRIGGER fail_completion_write ON {table}')
        rule_db.execute('DROP FUNCTION fail_completion_write()'); rule_db.commit()
    assert complete(api,auth,created,[eid]).status_code == 201
    assert snapshot(api,auth) == after_a


def test_b_completion_has_no_runtime_ddl_or_inventory_mutation(http, rule_db, monkeypatch):
    api, login = http; auth = login(); created = draft(api,auth); eid = event(api,auth)
    statements = []; original = PostgresConnection.execute
    def traced(db, query, params=()):
        statements.append(' '.join(query.upper().split()))
        return original(db,query,params)
    after_a = snapshot(api,auth)
    with monkeypatch.context() as trace:
        trace.setattr(PostgresConnection,'execute',traced)
        assert complete(api,auth,created,[eid]).status_code == 201
    assert not any(q.startswith(('CREATE ','ALTER ','DROP ','UPDATE INVENTORY_','INSERT INTO INVENTORY_','DELETE FROM INVENTORY_')) for q in statements)
    assert snapshot(api,auth) == after_a


def test_reversed_multiple_event_sets_cannot_be_claimed_twice(http, rule_db):
    api, login = http; auth = login(); add_ingredient(rule_db)
    drafts = [draft(api,auth),draft(api,auth)]
    for created in drafts:
        assert api('POST',f"/api/cooking-sessions/{created['id']}/ingredients",{'ingredient':'synthetic added','quantity_g':50},auth).status_code == 200
    ids = [event(api,auth),event(api,auth,ingredient=902,lot=1002,quantity=50)]
    after_a = snapshot(api,auth); barrier = Barrier(2)
    def run(index):
        barrier.wait(timeout=5)
        return complete(api,auth,drafts[index],ids if index==0 else list(reversed(ids))).status_code
    with ThreadPoolExecutor(max_workers=2) as pool: assert sorted(pool.map(run,[0,1])) == [201,409]
    assert snapshot(api,auth) == after_a
    assert rule_db.execute('SELECT COUNT(*) FROM meal_records').fetchone()[0] == 1
    assert rule_db.execute('SELECT COUNT(*) FROM cooking_inventory_event_links').fetchone()[0] == 2


def test_actual_portion_health_limit_is_checked_before_linking(http, rule_db):
    api, login = http; auth = login()
    rule_db.execute("INSERT INTO members(id,household_id,name,age) VALUES (12,1,'synthetic child',10)")
    rule_db.execute("UPDATE catalog_food_nutrients SET amount=100 WHERE nutrient='sodium_mg'"); rule_db.commit()
    created = draft(api,auth,[11,12]); eid = event(api,auth,quantity=200)
    rule_db.execute("INSERT INTO member_health_constraints VALUES (11,'meal_sodium_max_mg','120')"); rule_db.commit()
    after_a = snapshot(api,auth); saved = b_state(rule_db)
    portions = [{'member_id':11,'servings':1.5},{'member_id':12,'servings':0}]
    assert complete(api,auth,created,[eid],portions).status_code == 400
    assert b_state(rule_db) == saved and snapshot(api,auth) == after_a


def test_migration_006_preserves_existing_drafts_inventory_and_intakes():
    import psycopg
    from psycopg import sql
    from app.services.postgres_repository import hybrid_row
    from app.services.recommendation_engine import recommend
    root = Path(__file__).resolve().parents[1]
    schema = 'test_completion_upgrade_'+uuid4().hex
    url = os.environ['KINI_DATABASE_URL']
    with psycopg.connect(url,autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        try:
            raw = psycopg.connect(url,options=f'-c search_path={schema}',row_factory=hybrid_row)
            with closing(PostgresConnection(raw)) as db:
                for migration in sorted((root/'db/migrations').glob('*.sql')):
                    if migration.name >= '006_': break
                    with db.cursor() as cursor: cursor.execute(migration.read_text(encoding='utf-8'))
                    db.execute('INSERT INTO schema_migrations(version) VALUES (?) ON CONFLICT DO NOTHING',(migration.stem,))
                db.commit(); seed_family(db)
                saved = recommend(db,request_body())
                meal = db.execute("INSERT INTO meal_sessions(household_id,meal_type) VALUES (1,'dinner')").lastrowid
                db.execute('INSERT INTO meal_session_participants VALUES (?,11)',(meal,))
                db.execute('''INSERT INTO cooking_sessions(household_id,meal_session_id,request_id,recipe_id,ingredients_json,planned_servings)
                    VALUES (1,?,?,501,?,1)''',(meal,saved['request_id'],'[{"ingredient_id":901,"quantity_g":100}]'))
                record = db.execute('INSERT INTO meal_records(session_id,recipe_id,eaten_at) VALUES (?,501,?)',(meal,date.today().isoformat())).lastrowid
                item = db.execute('INSERT INTO meal_record_items(record_id,member_id,servings) VALUES (?,11,0.5)',(record,)).lastrowid
                db.execute("INSERT INTO nutrition_intakes VALUES (?,'protein_g',5)",(item,)); db.commit()
                tables = ['meal_records','meal_record_items','nutrition_intakes','cooking_sessions','inventory_items']
                before = {table:[dict(row) for row in db.execute(f'SELECT * FROM {table}')] for table in tables}
                db.commit()
                env = {**os.environ,'KINI_DB_SCHEMA':schema}
                first = subprocess.run([sys.executable,str(root/'scripts/run_migrations.py')],env=env,capture_output=True,text=True)
                assert first.returncode == 0 and '006_cooking_completion' in first.stdout
                again = subprocess.run([sys.executable,str(root/'scripts/run_migrations.py')],env=env,capture_output=True,text=True)
                assert again.returncode == 0 and 'already up to date' in again.stdout
                assert {table:[dict(row) for row in db.execute(f'SELECT * FROM {table}')] for table in tables} == before
                assert db.execute('SELECT COUNT(*) FROM cooking_inventory_event_links').fetchone()[0] == 0
        finally:
            admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
