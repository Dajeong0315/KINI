"""Observed C integration contract: additive fields remain allowed for A/B work."""
import json
from pathlib import Path


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
    client=app.test_client()
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
    done=client.post(f'/api/cooking-sessions/{sid}/complete',json={'confirmed':True,'eaten_at':'2026-11-25T12:00:00+09:00','items':[{'member_id':3,'servings':.5}]})
    assert done.status_code==201
    assert_shape(done.json,examples['cooking_completed'])
