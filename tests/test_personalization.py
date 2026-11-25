from datetime import datetime, timedelta, timezone
import pytest
from app.services.personalization import add_evidence, aggregate, score_personalization
from app.services.recommendation_repository import open_db, members
from app.services.recommendation_engine import recommend


@pytest.fixture
def db(tmp_path):
    connection = open_db(str(tmp_path / 'db.sqlite3'))
    yield connection
    connection.close()


def req(ids):
    return {'household_id':1, 'participant_ids':ids, 'meal_type':'dinner', 'purchase_allowed':False}


def candidate(result, rid):
    return next(r for r in result['recommendations'] if r['recipe_id']==rid)


def test_preference_scope_explicit_dislike_and_dismiss(db):
    for _ in range(3):
        add_evidence(db,1,None,'conversation','ingredient','버섯','avoid')
        add_evidence(db,1,3,'conversation','ingredient','버섯','prefer')
        add_evidence(db,1,3,'conversation','ingredient','새우','prefer')
    aggregate(db,1)
    result=recommend(db,req([3]))
    mushroom=candidate(result,2)
    assert mushroom['ignored_preference_signals']
    assert 3 in [r['recipe_id'] for r in result['excluded']]
    assert next(p for p in result['profile_snapshot'] if p['id']==3)['allergies']==['견과류','새우']
    assert any(p['value']=='버섯' for p in result['profile_snapshot'][0]['preferences'])
    pref=db.execute("SELECT id FROM inferred_preferences WHERE member_id=3 AND feature_value='버섯'").fetchone()[0]
    db.execute("UPDATE inferred_preferences SET status='dismissed' WHERE id=?",(pref,))
    aggregate(db,1)
    assert db.execute('SELECT status FROM inferred_preferences WHERE id=?',(pref,)).fetchone()[0]=='dismissed'


def test_personal_overrides_household_and_nonparticipant_is_ignored(db):
    for _ in range(3):
        add_evidence(db,1,None,'conversation','ingredient','당근','avoid')
        add_evidence(db,1,1,'conversation','ingredient','당근','prefer')
    aggregate(db,1)
    one=candidate(recommend(db,req([1])),1)
    two=candidate(recommend(db,req([2])),1)
    assert any(s['member_id']==1 for s in one['applied_preference_signals'])
    assert any(s.get('ignored_reason') for s in one['ignored_preference_signals'])
    assert not any(s['member_id']==1 for s in two['applied_preference_signals'] if 'member_id' in s)
    assert one['personalization_score'] > two['personalization_score']


def test_old_evidence_decays(db):
    old=(datetime.now(timezone.utc)-timedelta(days=500)).isoformat()
    for _ in range(3): add_evidence(db,1,1,'conversation','ingredient','당근','prefer',observed_at=old)
    aggregate(db,1)
    row=db.execute('SELECT * FROM inferred_preferences').fetchone()
    assert row['status']=='expired' and row['confidence'] < .65


def test_feedback_conversation_and_dismiss_api(monkeypatch,tmp_path):
    monkeypatch.setenv('KINI_DB_PATH',str(tmp_path/'api.sqlite3'))
    import app.main as main
    monkeypatch.setattr(main.recommendation_api, 'name', main.recommendation_api.name, raising=False)
    import app.services.recommendation_api as api
    monkeypatch.setattr(api,'explain_recommendation',lambda _: '템플릿')
    client=main.app.test_client()
    result=client.post('/api/recommendations',json=req([1,3])).json
    before=candidate(result,2)['personalization_score']
    for _ in range(3):
        response=client.post(f"/api/recommendations/{result['request_id']}/feedback",json={'candidate_recipe_id':2,'member_id':3,'action':'rejected','reason_code':'ingredient:버섯'})
        assert response.status_code==201
    preferences=client.get('/api/preferences?household_id=1&member_id=3').json['preferences']
    mushroom=next(p for p in preferences if p['feature_value']=='버섯')
    assert mushroom['inference']=='avoid' and mushroom['evidence_count']==3
    assert candidate(client.post('/api/recommendations',json=req([1,3])).json,2)['personalization_score'] < before
    assert client.post(f"/api/preferences/{mushroom['id']}/dismiss").status_code==200
    assert not client.get('/api/preferences?household_id=1&member_id=3').json['preferences']
    dismissed=candidate(client.post('/api/recommendations',json=req([1,3])).json,2)
    assert not any(s.get('id')==mushroom['id'] for s in dismissed['applied_preference_signals'])
    assert dismissed['personalization_score']==before
    session=client.post('/api/conversations',json={'household_id':1,'purpose':'general'}).json['session_id']
    clear=client.post(f'/api/conversations/{session}/messages',json={'role':'user','content':'버섯은 싫어','member_id':3}).json
    assert clear['evidence'][0]['feature_value']=='버섯'
    for text in ('새우 알레르기가 있어', '당뇨라 버섯은 싫어', '복용 약 때문에 당근은 싫어'):
        assert client.post(f'/api/conversations/{session}/messages',json={'role':'user','content':text}).json['evidence']==[]
    monkeypatch.setattr(api,'extract_preference_signal',lambda *_: (_ for _ in ()).throw(RuntimeError('offline')))
    assert client.post(f'/api/conversations/{session}/messages',json={'role':'user','content':'버섯은 싫어'}).json['evidence']==[]
    with open_db() as check:
        assert all(row[0]=='[원문 미보관]' for row in check.execute('SELECT content FROM conversation_messages'))


def test_existing_database_migration(tmp_path):
    path=str(tmp_path/'old.sqlite3')
    db=open_db(path)
    db.execute('DROP TABLE conversation_messages')
    db.commit(); db.close()
    db=open_db(path)
    assert db.execute("SELECT name FROM sqlite_master WHERE name='conversation_messages'").fetchone()
    assert len(members(db,1))==4
    db.close()


def test_repeated_selection_increases_preference_and_stable(monkeypatch,tmp_path):
    monkeypatch.setenv('KINI_DB_PATH',str(tmp_path/'api.sqlite3'))
    import app.main as main
    import app.services.recommendation_api as api
    monkeypatch.setattr(api,'explain_recommendation',lambda _: '템플릿')
    client=main.app.test_client()
    initial=client.post('/api/recommendations',json=req([1])).json
    first=candidate(initial,1)['personalization_score']
    for _ in range(3):
        assert client.post(f"/api/recommendations/{initial['request_id']}/feedback",json={'candidate_recipe_id':1,'member_id':1,'action':'selected'}).status_code==201
    after=client.post('/api/recommendations',json=req([1])).json
    assert candidate(after,1)['personalization_score']>first
    again=client.post('/api/recommendations',json=req([1])).json
    assert [r['recipe_id'] for r in after['recommendations']]==[r['recipe_id'] for r in again['recommendations']]
