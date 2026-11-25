from app.services.data_privacy import SENSITIVE_TABLES, redact_sensitive

def test_privacy_capabilities_and_redaction():
    from app.main import app
    response=app.test_client().get('/api/privacy/capabilities')
    assert response.json['delete_available'] is False
    assert response.json['export_available'] is False
    assert 'member_allergies' in SENSITIVE_TABLES
    assert '민감' in redact_sensitive('private')

def test_validation_errors_do_not_echo_input(monkeypatch,tmp_path):
    monkeypatch.setenv('KINI_DB_PATH',str(tmp_path/'db.sqlite3'))
    from app.main import app
    response=app.test_client().post('/api/conversations',json={'household_id':'sensitive-phrase','purpose':'general'})
    assert response.status_code==400 and 'sensitive-phrase' not in response.get_data(as_text=True)
