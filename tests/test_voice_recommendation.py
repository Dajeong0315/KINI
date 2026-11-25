from io import BytesIO
from app.services.recommendation_models import RecommendationRequest

def test_voice_recommendation_uses_explicit_participants(monkeypatch,tmp_path):
    monkeypatch.setenv('KINI_DB_PATH',str(tmp_path/'voice.sqlite3'))
    import app.services.recommendation_api as api
    from app.main import app
    monkeypatch.setattr(api,'transcribe_audio',lambda _: '저녁 추천')
    monkeypatch.setattr(api,'parse_recommendation_request',lambda _: RecommendationRequest(household_id=1,participant_ids=[1],meal_type='breakfast'))
    monkeypatch.setattr(api,'explain_recommendation',lambda _: '추천')
    client=app.test_client()
    def data(): return {'audio':(BytesIO(b'audio'),'input.wav'),'household_id':'1','participant_ids':'[3,4]','meal_type':'dinner'}
    result=client.post('/api/voice/recommendations',data=data(),content_type='multipart/form-data')
    assert result.status_code==201 and result.json['participant_ids']==[3,4]
    assert not {3,4,5}.intersection(x['recipe_id'] for x in result.json['recommendations'])
    monkeypatch.setattr(api,'parse_recommendation_request',lambda _: (_ for _ in ()).throw(RuntimeError('offline')))
    failed=client.post('/api/voice/recommendations',data=data(),content_type='multipart/form-data')
    assert failed.status_code==422 and 'recommendations' not in failed.json
