from flask import Flask
import pytest

@pytest.fixture
def setup(monkeypatch,tmp_path):
    monkeypatch.setenv('KINI_DB_PATH',str(tmp_path/'db.sqlite3'))
    import app.services.recommendation_api as api
    monkeypatch.setattr(api,'explain_recommendation',lambda _: '추천')
    app = Flask(__name__)
    app.register_blueprint(api.api)
    client=app.test_client()
    request={'household_id':1,'participant_ids':[3],'meal_type':'dinner'}
    recommendation=client.post('/api/recommendations',json=request).json
    meal=client.post('/api/meal-sessions',json=request).json
    result=client.post('/api/cooking-sessions',json={'meal_session_id':meal['session_id'],'recommendation_request_id':recommendation['request_id'],'recipe_id':2})
    assert result.status_code==201
    return client,result.json

def test_change_nutrition_and_confirm(setup):
    client,session=setup
    sid=session['id']; before=session['nutrition_per_serving']['protein_g']
    response=client.post(f'/api/cooking-sessions/{sid}/ingredients',json={'ingredient':'버섯','quantity_g':0})
    assert response.status_code==200 and response.json['nutrition_per_serving']['protein_g'] < before
    assert client.post(f'/api/cooking-sessions/{sid}/ingredients',json={'ingredient':'새우','quantity_g':100}).status_code==400
    after_failure=client.get(f'/api/cooking-sessions/{sid}').json
    assert after_failure['ingredients']==response.json['ingredients'] and after_failure['nutrition_per_serving']==response.json['nutrition_per_serving']
    assert client.post(f'/api/cooking-sessions/{sid}/ingredients',json={'ingredient':'스팸','quantity_g':100}).status_code==400
    assert client.post(f'/api/cooking-sessions/{sid}/ingredients',json={'ingredient':'두부','quantity_g':None}).status_code==400
    assert client.post(f'/api/cooking-sessions/{sid}/ingredients',json={'ingredient':'두부','quantity_g':1,'unit':'개'}).status_code==400
    final=client.post(f'/api/cooking-sessions/{sid}/complete',json={'confirmed':True,'eaten_at':'2026-11-25T12:00:00','items':[{'member_id':3,'servings':1}]})
    assert final.status_code==201 and final.json['nutrition_per_serving']==response.json['nutrition_per_serving']
    from app.services.recommendation_repository import open_db
    with open_db() as db:
        recorded=db.execute("SELECT amount FROM nutrition_intakes WHERE nutrient='protein_g'").fetchone()[0]
        assert recorded==response.json['nutrition_per_serving']['protein_g']
    assert client.post(f'/api/cooking-sessions/{sid}/complete',json={'confirmed':True,'eaten_at':'2026-11-25T12:00:00','items':[{'member_id':3,'servings':1}]}).status_code==400

def test_reject_different_participants(setup):
    client,session=setup
    other=client.post('/api/meal-sessions',json={'household_id':1,'participant_ids':[1],'meal_type':'dinner'}).json
    response=client.post('/api/cooking-sessions',json={'meal_session_id':other['session_id'],'recommendation_request_id':session['request_id'],'recipe_id':2})
    assert response.status_code==400

def test_cooking_questions_and_rag_failure(setup,monkeypatch):
    client,session=setup; sid=session['id']
    url=f'/api/cooking-sessions/{sid}/questions'
    for text in ('스팸 반 통만','버섯 빼고'):
        result=client.post(url,json={'question':text})
        assert result.status_code==200 and result.json['requires_confirmation']
    result=client.post(url,json={'question':'국물이 짜요'})
    assert result.status_code==200 and result.json['sources'][0]['version']
    from app.services.recommendation_rag import SQLiteRetriever
    monkeypatch.setattr(SQLiteRetriever,'search',lambda *_: (_ for _ in ()).throw(RuntimeError('offline')))
    assert client.post(url,json={'question':'국물이 짜요'}).json['sources']==[]

def test_rag_does_not_change_recommendation(setup):
    client,session=setup
    request={'household_id':1,'participant_ids':[3],'meal_type':'dinner'}
    before=client.post('/api/recommendations',json=request).json
    client.post(f"/api/cooking-sessions/{session['id']}/questions",json={'question':'보관은 어떻게 해요?'})
    after=client.post('/api/recommendations',json=request).json
    assert [x['recipe_id'] for x in before['recommendations']]==[x['recipe_id'] for x in after['recommendations']]
