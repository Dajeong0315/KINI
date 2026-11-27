"""Stored intake reporting through real loopback HTTP and disposable PostgreSQL."""
import os
from datetime import date
import pytest

from test_recommendation_rules_postgres import rule_db
from test_recommendation_http_postgres import http
from test_cooking_postgres_http import draft, stock_state
from test_cooking_completion_postgres import event, payload

pytestmark = pytest.mark.skipif(os.getenv('KINI_DB_BACKEND') != 'postgres', reason='requires disposable PostgreSQL')
PERIOD = '?start_date=2026-11-26&end_date=2026-11-26'


def recorded(api, auth, *, at='2026-11-26T12:00:00+09:00', portions=None):
    portions = portions or [{'member_id':11,'servings':1}]
    created = draft(api,auth,[p['member_id'] for p in portions])
    eid = event(api,auth,quantity=created['ingredients'][0]['quantity_g'])
    body = payload(created,[eid],portions); body['eaten_at'] = at
    response = api('POST',f"/api/cooking-sessions/{created['id']}/complete",body,auth)
    assert response.status_code == 201, response.text
    return response.json()['record_id'], created


def test_stored_actual_intake_snapshot_and_read_only_inventory(http,rule_db):
    api, login = http; auth = login()
    rule_db.execute("INSERT INTO members(id,household_id,name,age) VALUES (12,1,'synthetic child',10)"); rule_db.commit()
    rid, created = recorded(api,auth,portions=[{'member_id':11,'servings':1.5},{'member_id':12,'servings':0}])
    rule_db.execute("UPDATE recipes SET name='changed current recipe' WHERE id=501")
    rule_db.execute('UPDATE catalog_food_nutrients SET amount=999'); rule_db.commit()
    saved = stock_state(rule_db)
    for actor in [auth,login('rec-viewer')]:
        history = api('GET','/api/meal-records'+PERIOD,auth=actor)
        assert history.status_code == 200 and history.headers['Cache-Control']=='no-store'
        meal = history.json()['records'][0]
        assert meal['recipe_name'] != 'changed current recipe'
        assert meal['recipe_name_source']=='recommendation_snapshot'
        assert meal['nutrition_totals']['energy_kcal']==150
        assert 'result_json' not in meal and 'ingredients_json' not in meal
        detail = api('GET',f'/api/meal-records/{rid}',auth=actor)
        assert detail.status_code == 200 and detail.json()['items']==meal['items']
        report = api('GET','/api/reports/nutrition'+PERIOD,auth=actor).json()
        assert report['nutrition_totals']['energy_kcal']==150
        assert report['total_servings']==1.5 and report['zero_serving_item_count']==1
        assert report['members'][0]['nutrition_totals']['energy_kcal']==150
        assert report['members'][1]['nutrition_totals']['energy_kcal']==0
        assert report['data_quality']=='recorded'
        child = api('GET','/api/reports/nutrition'+PERIOD+'&member_id=12',auth=actor).json()
        assert child['nutrition_totals']['energy_kcal']==0 and len(child['members'])==1
    assert stock_state(rule_db)==saved


@pytest.mark.parametrize('at,included',[
    ('2026-11-25T14:59:59Z',False),('2026-11-25T15:00:00Z',True),
    ('2026-11-26T14:59:59Z',True),('2026-11-26T15:00:00Z',False),
    ('2026-11-26T00:00:00',True),('2026-11-26T23:59:59+09:00',True),
])
def test_kst_boundaries(http,rule_db,at,included,monkeypatch):
    from app.services import meal_reporting
    monkeypatch.setattr(meal_reporting,'kst_today',lambda:date(2026,11,26))
    api, login=http; auth=login(); recorded(api,auth,at=at)
    for query in [PERIOD,'?period=today']:
        response=api('GET','/api/meal-records'+query,auth=auth)
        assert response.status_code==200 and response.json()['total']==int(included)


def test_empty_week_and_pagination(http,rule_db,monkeypatch):
    from app.services import meal_reporting
    monkeypatch.setattr(meal_reporting,'kst_today',lambda:date(2026,11,26))
    api,login=http; auth=login()
    empty=api('GET','/api/reports/nutrition',auth=auth).json()
    assert empty['start_date']=='2026-11-20' and len(empty['daily'])==7
    assert empty['data_quality']=='empty' and all(v==0 for v in empty['nutrition_totals'].values())
    first,_=recorded(api,auth); second,_=recorded(api,auth)
    one=api('GET','/api/meal-records'+PERIOD+'&limit=1',auth=auth).json()
    two=api('GET','/api/meal-records'+PERIOD+'&limit=1&offset=1',auth=auth).json()
    assert one['total']==2 and one['has_more'] and not two['has_more']
    assert [one['records'][0]['record_id'],two['records'][0]['record_id']]==[second,first]


@pytest.mark.parametrize('path',['/api/meal-records','/api/reports/nutrition','/api/meal-records/1'])
def test_authentication_household_and_member_boundary(http,rule_db,path):
    api,login=http; auth=login(); rid,_=recorded(api,auth)
    if path.endswith('/1'): path=f'/api/meal-records/{rid}'
    assert api('GET',path).status_code==401
    assert api('GET',path+'?household_id=2',auth=auth).status_code==403
    if not path.rsplit('/',1)[-1].isdigit():
        assert api('GET',path+'?member_id=22',auth=auth).status_code==403
    else:
        assert api('GET',path,auth=login('rec-other',2)).status_code==404
    invalid=api('GET',path,auth={'Authorization':'Bearer invalid-synthetic-token'})
    assert invalid.status_code==401 and invalid.headers['Cache-Control']=='no-store'


@pytest.mark.parametrize('query',[
    '?period=month','?period=custom','?start_date=2026-11-27&end_date=2026-11-26',
    '?start_date=2026-02-30&end_date=2026-11-26','?start_date=2025-01-01&end_date=2026-11-26',
    '?period=today&start_date=2026-11-26','?limit=0','?limit=101','?offset=-1',
    '?member_id=0','?household_id=1&household_id=2','?unknown=1',
])
def test_invalid_filters(http,query):
    api,login=http
    assert api('GET','/api/meal-records'+query,auth=login()).status_code==400


def test_missing_nutrition_is_unknown_not_zero_and_foreign_item_is_hidden(http,rule_db):
    api,login=http; auth=login(); rid,_=recorded(api,auth)
    rule_db.execute("DELETE FROM nutrition_intakes WHERE nutrient='protein_g'")
    foreign=rule_db.execute('INSERT INTO meal_record_items(record_id,member_id,servings) VALUES (?,22,1)',(rid,)).lastrowid
    rule_db.execute("INSERT INTO nutrition_intakes VALUES (?,'energy_kcal',9999)",(foreign,)); rule_db.commit()
    report=api('GET','/api/reports/nutrition'+PERIOD,auth=auth).json()
    assert report['nutrition_totals']['protein_g'] is None
    assert report['known_nutrition_totals']['protein_g']==0 and report['data_quality']=='incomplete'
    assert report['nutrition_totals']['energy_kcal']==100
    detail=api('GET',f'/api/meal-records/{rid}',auth=auth).json()
    assert len(detail['items'])==1 and 'synthetic other' not in str(detail)
    rule_db.execute("UPDATE meal_records SET eaten_at='legacy invalid' WHERE id=?",(rid,)); rule_db.commit()
    history=api('GET','/api/meal-records'+PERIOD,auth=auth).json()
    assert history['total']==0 and history['excluded_invalid_timestamp_count']==1
    assert api('GET',f'/api/meal-records/{rid}',auth=auth).json()['data_quality']=='incomplete'


def test_reporting_transaction_rejects_accidental_write(http,rule_db,monkeypatch):
    from app.services import meal_reporting
    api,login=http; auth=login(); recorded(api,auth)
    before=stock_state(rule_db)
    def accidental_write(db,*args,**kwargs):
        db.execute('UPDATE inventory_items SET quantity_g=0 WHERE household_id=1')
        return {}
    monkeypatch.setattr(meal_reporting,'history',accidental_write)
    assert api('GET','/api/meal-records'+PERIOD,auth=auth).status_code==500
    assert stock_state(rule_db)==before


def test_meal_feedback_persists_per_account_without_touching_stock_or_intake(http, rule_db):
    api, login = http
    owner = login()
    viewer = login('rec-viewer')
    other = login('rec-other', 2)
    rid, _ = recorded(api, owner)
    path = f'/api/meal-records/{rid}/feedback'
    feedback = {'taste': 'good', 'salt': None, 'difficulty': 'easy'}
    before = stock_state(rule_db)

    assert api('POST', path, feedback).status_code == 401
    assert api('POST', path, feedback, viewer).status_code == 403
    assert api('POST', path, feedback, other).status_code == 404
    assert api('GET', path, auth=other).status_code == 404
    assert api('POST', path, dict.fromkeys(feedback), owner).status_code == 400
    assert api('POST', path, {**feedback, 'salt': 'skip'}, owner).status_code == 400

    for expected in (feedback, feedback, {**feedback, 'taste': 'okay'}):
        assert api('POST', path, expected, owner).status_code == 200
        result = api('GET', path, auth=owner)
        assert result.json() == {'feedback': expected}
        assert result.headers['Cache-Control'] == 'no-store'
    assert api('GET', path, auth=viewer).json() == {'feedback': None}
    assert rule_db.execute('SELECT COUNT(*) FROM meal_account_feedback WHERE record_id=?', (rid,)).fetchone()[0] == 1
    assert stock_state(rule_db) == before


def test_007_upgrade_is_repeatable_and_preserves_stored_history():
    import subprocess
    import sys
    from pathlib import Path
    from uuid import uuid4
    from contextlib import closing
    import psycopg
    from psycopg import sql
    from app.services.postgres_repository import PostgresConnection, hybrid_row
    from test_recommendation_rules_postgres import seed_family
    root=Path(__file__).resolve().parents[1]
    schema='test_reports_upgrade_'+uuid4().hex
    url=os.environ['KINI_DATABASE_URL']
    with psycopg.connect(url,autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        try:
            raw=psycopg.connect(url,options=f'-c search_path={schema}',row_factory=hybrid_row)
            with closing(PostgresConnection(raw)) as db:
                for migration in sorted((root/'db/migrations').glob('*.sql')):
                    if migration.name>='007_': break
                    with db.cursor() as cursor: cursor.execute(migration.read_text(encoding='utf-8'))
                    db.execute('INSERT INTO schema_migrations(version) VALUES (?) ON CONFLICT DO NOTHING',(migration.stem,))
                db.commit(); seed_family(db)
                meal=db.execute("INSERT INTO meal_sessions(household_id,meal_type) VALUES (1,'dinner')").lastrowid
                record=db.execute("INSERT INTO meal_records(session_id,recipe_id,eaten_at) VALUES (?,501,'2026-11-26')",(meal,)).lastrowid
                item=db.execute('INSERT INTO meal_record_items(record_id,member_id,servings) VALUES (?,11,0.5)',(record,)).lastrowid
                db.execute("INSERT INTO nutrition_intakes VALUES (?,'protein_g',5)",(item,)); db.commit()
                before=stock_state(db); db.commit()
                env={**os.environ,'KINI_DB_SCHEMA':schema}
                for expected in ['007_meal_reporting_indexes','already up to date']:
                    result=subprocess.run([sys.executable,str(root/'scripts/run_migrations.py')],env=env,capture_output=True,text=True)
                    assert result.returncode==0 and expected in result.stdout
                assert stock_state(db)==before
                assert db.execute('SELECT COUNT(*) FROM pg_indexes WHERE schemaname=? AND indexname IN (?,?,?)',
                    (schema,'meal_history_household','meal_history_session','meal_history_member')).fetchone()[0]==3
        finally:
            admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
