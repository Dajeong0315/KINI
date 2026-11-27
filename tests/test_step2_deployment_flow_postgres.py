"""Empty disposable project, synthetic household, representative public facts."""
import os
from pathlib import Path

import pytest

pytestmark=pytest.mark.skipif(not os.getenv('KINI_STEP2_DEPLOY_TEST_URL'), reason='empty disposable DB required')


def test_reference_setup_login_recommendation_and_saved_provenance(monkeypatch):
    import psycopg
    from scripts.prepare_private_deployment import build
    from scripts.prepare_step2_demo import prepare
    from app.services.recommendation_repository import open_db
    from app.b_runtime import internal_app
    url=os.environ['KINI_STEP2_DEPLOY_TEST_URL']
    release='1d211108-0849-479d-9205-ac5060529431'
    with psycopg.connect(url,autocommit=True) as db:
        db.execute('''CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role;
        CREATE TABLE public.data_sources(code text PRIMARY KEY,rights_status text);
        CREATE TABLE public.data_releases(id uuid PRIMARY KEY,source_code text,status text);
        CREATE TABLE public.active_data_releases(source_code text,release_id uuid,cache_version bigint);
        CREATE TABLE public.catalog_foods(release_id uuid,external_id text,name text,basis_amount numeric,
            basis_unit text,energy_kcal numeric,protein_g numeric,carbohydrate_g numeric,fat_g numeric,sodium_mg numeric,sugar_g numeric);
        CREATE TABLE public.catalog_recipes(release_id uuid,external_id text,name text,servings numeric,cook_time_minutes int,ingredients_text text);
        CREATE TABLE public.catalog_recipe_steps(release_id uuid,recipe_id text,step_no int,instruction text);
        CREATE TABLE public.catalog_recipe_nutrients(release_id uuid,recipe_id text,nutrient text,amount numeric,basis_amount numeric,basis_unit text);
        INSERT INTO public.data_sources VALUES ('mfds_nutrition','approved');''')
        db.execute("INSERT INTO public.data_releases VALUES (%s,'mfds_nutrition','published')",(release,))
        db.execute("INSERT INTO public.active_data_releases VALUES ('mfds_nutrition',%s,1)",(release,))
        for values in [
            ('R101-008000301-0003','멥쌀, 백미, 생것',366,6.38,79,1.06,2),
            ('R109-008000401-0000','닭고기, 가슴, 생것',106,22.97,0,.97,45),
            ('R106-041007601-0000','당근, 뿌리, 생것',33,.78,7.65,.18,25)]:
            db.execute("INSERT INTO public.catalog_foods VALUES (%s,%s,%s,100,'g',%s,%s,%s,%s,%s,NULL)",(release,*values))
        from scripts.prepare_private_deployment import PUBLIC_TABLES
        for table in PUBLIC_TABLES:
            db.execute(f'ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY')
        db.execute(build())
        db.execute((Path(__file__).resolve().parents[1]/'deploy/step2_demo_references.sql').read_text(encoding='utf-8'))
    monkeypatch.setenv('KINI_DB_BACKEND','postgres')
    monkeypatch.setenv('KINI_DATABASE_URL',psycopg.conninfo.make_conninfo(url,user='kini_app'))
    monkeypatch.setenv('KINI_DB_SCHEMA','kini_private')
    monkeypatch.setenv('KINI_PUBLIC_CATALOG_BACKEND','supabase')
    monkeypatch.setenv('KINI_SEED_DEMO','0')
    monkeypatch.setenv('KINI_B_MEAL_FEEDBACK_ENABLED','0')
    with open_db() as db:
        demo=prepare(db,'synthetic-step2','synthetic-password-for-test')
    client=internal_app('rules-only').test_client()
    login=client.post('/api/auth/login',json=dict(login_name='synthetic-step2',
        password='synthetic-password-for-test',household_id=demo['household_id']))
    assert login.status_code==200
    headers={'Authorization':'Bearer '+login.json['access_token']}
    response=client.post('/api/recommendations',headers=headers,json=dict(household_id=demo['household_id'],
        participant_ids=demo['participant_ids'],meal_type='dinner',purchase_allowed=False))
    assert response.status_code==201 and response.json['status']=='recommendations_available'
    candidate=response.json['recommendations'][0]
    assert candidate['nutrition_per_serving']==dict(energy_kcal=397.,protein_g=28.14,carbohydrate_g=63.08,fat_g=1.85,sodium_mg=59.)
    assert response.json['ai_status']=='disabled' and candidate['data_quality']=='demo_data'
    assert {x['nutrition_food_id'] for x in candidate['inventory_usage']}=={'R101-008000301-0003','R109-008000401-0000','R106-041007601-0000'}
    saved=client.get('/api/recommendations/'+str(response.json['request_id']),headers=headers)
    assert saved.status_code==200 and saved.json['status']=='recommendations_available'
    household=client.get('/api/households/'+str(demo['household_id']),headers=headers)
    assert household.status_code==200 and len(household.json['members'])==1
    query='?household_id='+str(demo['household_id'])+'&period=today'
    assert client.get('/api/meal-records'+query,headers=headers).json['total']==0
    meal=client.post('/api/meal-sessions',headers=headers,json=dict(household_id=demo['household_id'],
        participant_ids=demo['participant_ids'],meal_type='dinner')).json
    cooking=client.post('/api/cooking-sessions',headers=headers,json=dict(meal_session_id=meal['session_id'],
        recommendation_request_id=response.json['request_id'],recipe_id=candidate['recipe_id']))
    assert cooking.status_code==201
    snapshot=client.get('/api/data/inventory',headers=headers).json
    from uuid import uuid4
    from datetime import datetime,timezone
    events=[]
    for ingredient in cooking.json['ingredients']:
        lot=next(row for row in snapshot['items'] if row['ingredient_id']==ingredient['ingredient_id'])
        event=client.post('/api/data/inventory/events',headers=headers,json=dict(source='manual',action='stock_out',
            client_event_id=str(uuid4()),detected_label=lot['name'])).json
        confirmed=client.post('/api/data/inventory/events/'+str(event['event_id'])+'/confirm',headers=headers,
            json=dict(ingredient_id=ingredient['ingredient_id'],inventory_item_id=lot['id'],quantity_g=ingredient['quantity_g']))
        assert confirmed.status_code==200
        events.append(event['event_id'])
    after_a=client.get('/api/data/inventory',headers=headers).json
    completed=client.post('/api/cooking-sessions/'+str(cooking.json['id'])+'/complete',headers=headers,
        json=dict(confirmed=True,eaten_at=datetime.now(timezone.utc).isoformat(),
            items=[dict(member_id=demo['participant_ids'][0],servings=.5)],inventory_event_ids=events))
    assert completed.status_code==201
    assert client.get('/api/data/inventory',headers=headers).json==after_a
    records=client.get('/api/meal-records'+query,headers=headers)
    assert records.status_code==200 and records.json['total']==1
    record_id=completed.json['record_id']
    detail=client.get('/api/meal-records/'+str(record_id)+'?household_id='+str(demo['household_id']),headers=headers)
    assert detail.json['items'][0]['servings']==.5
    report=client.get('/api/reports/nutrition'+query,headers=headers)
    assert report.status_code==200 and report.json['nutrition_totals']['energy_kcal']==198.5
