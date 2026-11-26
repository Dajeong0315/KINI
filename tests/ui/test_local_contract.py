"""Observed C integration contract: additive fields remain allowed for A/B work."""
import json
from contextlib import closing
from pathlib import Path
import secrets
from uuid import uuid4


def assert_shape(actual, example):
    if isinstance(example, dict):
        assert isinstance(actual, dict)
        assert example.keys() <= actual.keys()
        for key, value in example.items():
            assert_shape(actual[key], value)
    elif isinstance(example, list):
        assert isinstance(actual, list)
        if example and actual:
            assert_shape(actual[0], example[0])
    elif example is not None:
        assert isinstance(actual, type(example))


def test_observed_data_and_cooking_contract(monkeypatch,tmp_path):
    monkeypatch.setenv('KINI_DB_PATH',str(tmp_path/'contract.sqlite3'))
    monkeypatch.setenv('KINI_USE_OPENAI','0')
    from app.main import app
    from app.services.local_identity import create_account
    from app.services.recommendation_repository import open_db
    client=app.test_client()
    password=secrets.token_urlsafe(24)
    with closing(open_db()) as db:
        create_account(db,'contract-owner',password,1)
    login=client.post('/api/auth/login',json={'login_name':'contract-owner','password':password,'household_id':1})
    assert login.status_code==200
    headers={'Authorization':'Bearer '+login.json['access_token']}
    examples=json.loads((Path(__file__).parents[1]/'fixtures/api_contract/local_ui_v1.json').read_text(encoding='utf-8'))
    for path,key in [('/api/data-releases','data_releases'),('/api/data-quality/summary','data_quality_summary')]:
        response=client.get(path)
        assert response.status_code==200
        assert_shape(response.json,examples[key])
    req={'household_id':1,'participant_ids':[3],'meal_type':'dinner'}
    recommendation=client.post('/api/recommendations',json=req).json
    meal=client.post('/api/meal-sessions',json=req).json
    assert_shape(meal,examples['meal_session'])
    response=client.post('/api/cooking-sessions',json={'meal_session_id':meal['session_id'],'recommendation_request_id':recommendation['request_id'],'recipe_id':2})
    assert response.status_code==201
    assert_shape(response.json,examples['cooking_session'])
    sid=response.json['id']
    changed=client.post(f'/api/cooking-sessions/{sid}/ingredients',json={'ingredient':'버섯','quantity_g':0})
    assert changed.status_code==200
    assert_shape(changed.json,examples['cooking_changed'])
    inventory=client.get('/api/data/inventory',headers=headers)
    assert inventory.status_code==200
    lots={item['ingredient_id']:item for item in inventory.json['items']}
    event_ids=[]
    for item in changed.json['ingredients']:
        lot=lots[item['ingredient_id']]
        event=client.post('/api/data/inventory/events',headers=headers,json={
            'client_event_id':str(uuid4()),'source':'manual','action':'stock_out','detected_label':lot['name'],
        })
        assert event.status_code==200
        event_id=event.json['event_id']
        confirmed=client.post(f'/api/data/inventory/events/{event_id}/confirm',headers=headers,json={
            'ingredient_id':item['ingredient_id'],'inventory_item_id':lot['id'],'quantity_g':item['quantity_g'],
        })
        assert confirmed.status_code==200
        event_ids.append(event_id)
    after_outflow=client.get('/api/data/inventory',headers=headers).json
    done=client.post(f'/api/cooking-sessions/{sid}/complete',headers=headers,json={'confirmed':True,'eaten_at':'2026-11-25T12:00:00+09:00','items':[{'member_id':3,'servings':.5}],'inventory_event_ids':event_ids})
    assert done.status_code==201
    assert_shape(done.json,examples['cooking_completed'])
    assert client.get('/api/data/inventory',headers=headers).json==after_outflow
