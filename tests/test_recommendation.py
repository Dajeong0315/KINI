import pytest
from app.services.recommendation_repository import open_db
from app.services.recommendation_engine import recommend

@pytest.fixture
def db(tmp_path):
    db=open_db(str(tmp_path/'test.sqlite3'))
    yield db
    db.close()

def request(ids):
    return {'household_id':1,'participant_ids':ids,'meal_type':'dinner','purchase_allowed':False}

def test_allergy_is_limited_to_participants(db):
    child=recommend(db,request([3])); adults=recommend(db,request([1]))
    assert {3,4} <= {x['recipe_id'] for x in child['excluded'] if any('알레르기' in r for r in x['reasons'])}
    assert not ({3,4} & {x['recipe_id'] for x in adults['excluded']})
    assert [p['id'] for p in child['profile_snapshot']]==[3]

def test_sodium_limit_is_hard_filter(db):
    assert 5 in {x['recipe_id'] for x in recommend(db,request([4]))['excluded']}
    assert 5 not in {x['recipe_id'] for x in recommend(db,request([1]))['excluded']}

def test_urgent_inventory_increases_score_and_order_is_stable(db):
    first=recommend(db,request([1])); again=recommend(db,request([1]))
    assert [x['recipe_id'] for x in first['recommendations']]==[x['recipe_id'] for x in again['recommendations']]
    before=next(x for x in first['recommendations'] if x['recipe_id']==1)['scores']['inventory']
    db.execute("UPDATE inventory_items SET freshness_state='good' WHERE ingredient_id=3")
    after=next(x for x in recommend(db,request([1]))['recommendations'] if x['recipe_id']==1)['scores']['inventory']
    assert before>after

def test_api_and_llm_failure_fallback(monkeypatch,tmp_path):
    monkeypatch.setenv('KINI_DB_PATH',str(tmp_path/'api.sqlite3'))
    import sys
    monkeypatch.setitem(sys.modules, 'langchain_ollama', None)
    import app.main as main
    client=main.app.test_client()
    response=client.post('/api/recommendations',json=request([3]))
    assert response.status_code==201 and response.json['recommendations'] and response.json['explanation']
    assert client.get('/api/recommendations/'+str(response.json['request_id'])).status_code==200
    session=client.post('/api/meal-sessions',json=request([3])).json
    record=client.post('/api/meal-records',json={'session_id':session['session_id'],'recipe_id':1,'eaten_at':'2026-11-25T12:00:00','items':[{'member_id':3,'servings':1}]})
    assert record.status_code==201

def test_snapshot_and_more_candidates(db):
    first=recommend(db,request([1]))
    again=recommend(db,request([1]))
    assert first['input_snapshot_hash']==again['input_snapshot_hash']
    assert first['recommendations'][0]['adjustments'][0]['meal_target_estimate']
    more=recommend(db,{**request([1]),'excluded_recipe_ids':[x['recipe_id'] for x in first['recommendations']]})
    assert not ({x['recipe_id'] for x in first['recommendations']} & {x['recipe_id'] for x in more['recommendations']})
