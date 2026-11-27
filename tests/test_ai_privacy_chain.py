from io import BytesIO
from urllib import error

import pytest

from app.services.ai import openai_client
from app.services.ai.openai_client import AIExplanation, HttpOpenAIRecommendationClient, OpenAIClientError
from app.services.ai.privacy_gateway import PrivacyGateway
from app.services.ai.recommendation_chain import enrich_with_ai
from app.services.recommendation_engine import recommend
from app.services.recommendation_models import RecommendationRequest
from app.services.recommendation_repository import open_db


def _request(**extra):
    return {"household_id": 1, "participant_ids": [1, 3], "meal_type": "dinner", "purchase_allowed": False, **extra}


def _rules():
    return {"request_id": 7, "rules_version": "x", "prompt_version": "y", "status": "recommendations_available", "data_quality": "demo_data", "requires_confirmation": True, "warnings": [], "recommendations": [{"recipe_id": 2, "name": "두부볶음", "cook_time_minutes": 20, "nutrition_per_serving": {"sodium_mg": 300}, "warnings": ["raw warning"], "recommendation_reason_codes": ["PERSONALIZED", "NOT_ALLOWED"]}], "excluded": [{"reason_codes": ["allergen_conflict", "ARBITRARY_CODE"]}], "validated_constraint_codes": ["exclude_ingredient:ingredient_12", "nutrition:locally_validated", "exclude_ingredient:evil"]}


def _valid_explanation(payload):
    return AIExplanation("untrusted", [{"candidate_code": item["candidate_code"], "reason": "untrusted"} for item in payload["validated_candidates"]])


def test_gateway_uses_explicit_allowlists_and_request_scoped_codes():
    decision = PrivacyGateway().prepare(_request(excluded_ingredients=["새우"]), _rules())
    assert decision.status == "allowed"
    assert decision.payload["constraint_codes"] == ["constraint_1"]
    assert decision.payload["excluded_reason_codes"] == ["allergen_conflict"]
    candidate = decision.payload["validated_candidates"][0]
    assert candidate == {"candidate_code": "candidate_1", "cook_time_minutes": 20, "nutrition_per_serving": {"sodium_mg": 300}, "recommendation_reason_codes": ["PERSONALIZED"]}
    assert "두부볶음" not in str(decision.payload) and "raw warning" not in str(decision.payload)
    assert "ingredient_12" not in str(decision.payload) and "recipe_id" not in str(decision.payload)


def test_every_forwarded_reason_code_is_from_the_engine_allowlist():
    codes = ["allergen_conflict", "label_confirmation_required", "nutrition_unknown", "ingredient_unknown", "unit_confirmation_required", "food_safety_confirmation_required", "user_exclusion", "dietary_constraint", "health_constraint", "cook_time_limit", "inventory_insufficient"]
    rules = _rules() | {"excluded": [{"reason_codes": codes + ["NEW_UNREVIEWED_CODE"]}]}
    assert PrivacyGateway().prepare(_request(), rules).payload["excluded_reason_codes"] == sorted(codes)


def test_sensitive_input_blocks_and_never_reaches_client():
    sensitive_values = ["김민지", "010-1234-5678", "서울시 강남구 테헤란로 1", "sk-secret-token-12345", "당뇨 진단"]
    for sensitive in sensitive_values:
        called = False
        class Client:
            def explain(self, _payload):
                nonlocal called
                called = True
                return _valid_explanation(_payload)
        result = enrich_with_ai(_request(excluded_ingredients=[sensitive]), _rules(), client=Client())
        assert result["status"] == "privacy_blocked"
        assert not called


def test_unknown_constraint_is_omitted_and_broken_local_inspector_cannot_bypass_block():
    class BrokenInspector:
        def inspect(self, _): raise RuntimeError("offline")
    allowed = PrivacyGateway(BrokenInspector()).prepare(_request(excluded_ingredients=["unverified-input"]), _rules())
    assert allowed.status == "allowed" and "unverified-input" not in str(allowed.payload)
    assert PrivacyGateway(BrokenInspector()).prepare(_request(name="김민지"), _rules()).status == "privacy_blocked"


def test_normalized_ingredient_is_preserved_as_opaque_external_constraint(tmp_path):
    db = open_db(str(tmp_path / "recommendation.sqlite3"))
    try:
        request = _request(excluded_ingredients=["당근"])
        rules = recommend(db, request)
    finally:
        db.close()
    captured = {}
    class Client:
        def explain(self, payload):
            captured.update(payload)
            return _valid_explanation(payload)
    assert enrich_with_ai(request, rules, client=Client())["status"] == "recommendations_available"
    assert captured["constraint_codes"] == ["constraint_1"]
    assert "당근" not in str(captured) and "ingredient_3" not in str(captured)


def test_model_prose_is_never_rendered_and_extra_candidate_is_api_error():
    unsafe_text = "새우를 추가하면 안전합니다. 나트륨은 10mg이고 치료에 도움이 됩니다."
    class UnsafeButStructuredClient:
        def explain(self, payload):
            return AIExplanation(unsafe_text, [{"candidate_code": "candidate_1", "reason": unsafe_text}])
    safe = enrich_with_ai(_request(), _rules(), client=UnsafeButStructuredClient())
    assert safe["status"] == "recommendations_available"
    assert unsafe_text not in str(safe)
    assert safe["explanation"] == "로컬 안전 규칙으로 검증된 후보를 안내합니다."
    assert safe["ai_explanation"]["candidate_explanations"] == [{"recipe_id": 2, "reason": "로컬 규칙 엔진이 검증한 후보입니다."}]

    class ExtraCandidateClient:
        def explain(self, _): return AIExplanation("ok", [{"candidate_code": "candidate_1", "reason": "ok"}, {"candidate_code": "candidate_99", "reason": "new menu"}])
    blocked = enrich_with_ai(_request(), _rules(), client=ExtraCandidateClient())
    assert blocked["status"] == "api_error" and blocked["recommendations"] == []


@pytest.mark.parametrize("failure", [TimeoutError(), error.URLError("offline"), error.HTTPError("https://api.openai.com", 500, "error", {}, None)])
def test_http_adapter_transport_failures_raise_openai_client_error(monkeypatch, failure):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_RECOMMENDATION_MODEL", "test-model")
    monkeypatch.setattr(openai_client.request, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(failure))
    with pytest.raises(OpenAIClientError):
        HttpOpenAIRecommendationClient().explain(PrivacyGateway().prepare(_request(), _rules()).payload)


def test_http_adapter_malformed_response_raises_openai_client_error(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def read(self): return b'{"output":[{"content":[{"text":"not-json"}]}]}'
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_RECOMMENDATION_MODEL", "test-model")
    monkeypatch.setattr(openai_client.request, "urlopen", lambda *_args, **_kwargs: Response())
    with pytest.raises(OpenAIClientError):
        HttpOpenAIRecommendationClient().explain(PrivacyGateway().prepare(_request(), _rules()).payload)


def test_recommendation_and_voice_api_send_opaque_payloads(monkeypatch, tmp_path):
    monkeypatch.setenv("KINI_DB_PATH", str(tmp_path / "api.sqlite3"))
    import app.services.ai.recommendation_chain as chain
    import app.services.recommendation_api as api
    from app.main import app
    payloads = []
    class Client:
        def explain(self, payload):
            payloads.append(payload)
            return _valid_explanation(payload)
    monkeypatch.setattr(chain, "configured_recommendation_client", lambda: Client())
    client = app.test_client()
    recommendation = client.post("/api/recommendations", json=_request(excluded_ingredients=["당근"]))
    assert recommendation.status_code == 201 and recommendation.json["status"] == "recommendations_available"
    monkeypatch.setattr(api, "transcribe_audio", lambda _: "저녁 추천")
    monkeypatch.setattr(api, "parse_recommendation_request", lambda _: RecommendationRequest(household_id=1, participant_ids=[1], meal_type="dinner"))
    voice = client.post("/api/voice/recommendations", data={"audio": (BytesIO(b"audio"), "input.wav"), "household_id": "1", "participant_ids": "[1,3]", "meal_type": "dinner"}, content_type="multipart/form-data")
    assert voice.status_code == 201 and voice.json["status"] == "recommendations_available"
    assert len(payloads) == 2
    for payload in payloads:
        assert "household_id" not in str(payload) and "participant_ids" not in str(payload)
        assert "recipe_id" not in str(payload) and "ingredient_" not in str(payload)


def test_api_error_and_no_safe_remain_distinct():
    class FailingClient:
        def explain(self, _): raise OpenAIClientError("offline")
    failed = enrich_with_ai(_request(), _rules(), client=FailingClient())
    assert failed["status"] == "api_error" and failed["recommendations"] == []
    no_safe = {**_rules(), "status": "no_safe_recommendation", "recommendations": []}
    assert enrich_with_ai(_request(), no_safe, client=FailingClient())["status"] == "no_safe_recommendation"


@pytest.mark.parametrize("failure", [TimeoutError(), error.URLError("offline"), error.HTTPError("https://api.openai.com", 500, "error", {}, None)])
def test_recommendation_api_converts_http_adapter_errors_to_api_error(monkeypatch, tmp_path, failure):
    monkeypatch.setenv("KINI_DB_PATH", str(tmp_path / "api-error.sqlite3"))
    monkeypatch.setenv("KINI_USE_OPENAI", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_RECOMMENDATION_MODEL", "test-model")
    monkeypatch.setattr(openai_client.request, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(failure))
    from app.main import app
    response = app.test_client().post("/api/recommendations", json=_request())
    assert response.status_code == 201
    assert response.json["status"] == "api_error" and response.json["recommendations"] == []


def test_recommendation_api_converts_malformed_http_response_to_api_error(monkeypatch, tmp_path):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def read(self): return b'{"output":[{"content":[{"text":"not-json"}]}]}'
    monkeypatch.setenv("KINI_DB_PATH", str(tmp_path / "malformed.sqlite3"))
    monkeypatch.setenv("KINI_USE_OPENAI", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_RECOMMENDATION_MODEL", "test-model")
    monkeypatch.setattr(openai_client.request, "urlopen", lambda *_args, **_kwargs: Response())
    from app.main import app
    response = app.test_client().post("/api/recommendations", json=_request())
    assert response.status_code == 201
    assert response.json["status"] == "api_error" and response.json["recommendations"] == []

@pytest.mark.parametrize('failure_status', ['api_error', 'privacy_blocked'])
def test_failed_recommendation_is_persisted_without_candidates_or_shown_feedback(monkeypatch, tmp_path, failure_status):
    monkeypatch.setenv('KINI_DB_PATH', str(tmp_path / 'terminal.sqlite3'))
    import app.services.ai.recommendation_chain as chain
    from app.main import app
    class FailingClient:
        def explain(self, payload): raise OpenAIClientError('offline')
    monkeypatch.setattr(chain, 'configured_recommendation_client', lambda: FailingClient())
    request = _request(excluded_ingredients=['김민지']) if failure_status == 'privacy_blocked' else _request()
    client = app.test_client()
    response = client.post('/api/recommendations', json=request).json
    assert response['status'] == failure_status and response['recommendations'] == []
    saved = client.get(f"/api/recommendations/{response['request_id']}").json
    assert saved['status'] == failure_status and saved['recommendations'] == []
    with open_db() as db:
        assert db.execute('SELECT COUNT(*) FROM recommendation_candidates WHERE request_id=?', (response['request_id'],)).fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM recommendation_feedback WHERE recommendation_request_id=?', (response['request_id'],)).fetchone()[0] == 0
    assert client.post(f"/api/recommendations/{response['request_id']}/feedback", json={'candidate_recipe_id': 1, 'action': 'selected'}).status_code == 400


def test_successful_recommendation_retrieval_preserves_final_explanation(monkeypatch, tmp_path):
    monkeypatch.setenv('KINI_DB_PATH', str(tmp_path / 'success.sqlite3'))
    monkeypatch.delenv('KINI_USE_OPENAI', raising=False)
    from app.main import app
    client = app.test_client()
    created = client.post('/api/recommendations', json=_request()).json
    saved = client.get(f"/api/recommendations/{created['request_id']}").json
    assert created['status'] == saved['status'] == 'recommendations_available'
    assert saved['recommendations'] == created['recommendations']
    assert saved['ai_explanation'] == created['ai_explanation']


def test_legacy_unvalidated_result_is_not_republished(monkeypatch, tmp_path):
    monkeypatch.setenv('KINI_DB_PATH', str(tmp_path / 'legacy.sqlite3'))
    with open_db() as db:
        legacy = recommend(db, _request())
    from app.main import app
    client = app.test_client()
    saved = client.get(f"/api/recommendations/{legacy['request_id']}").json
    assert saved['status'] == 'stale_unvalidated' and saved['recommendations'] == []
    assert client.post(f"/api/recommendations/{legacy['request_id']}/feedback", json={'candidate_recipe_id': legacy['recommendations'][0]['recipe_id'], 'action': 'selected'}).status_code == 400


def test_voice_api_error_cannot_be_retrieved_as_recommendation(monkeypatch, tmp_path):
    monkeypatch.setenv('KINI_DB_PATH', str(tmp_path / 'voice-error.sqlite3'))
    import app.services.ai.recommendation_chain as chain
    import app.services.recommendation_api as api
    from app.main import app
    class FailingClient:
        def explain(self, payload): raise OpenAIClientError('offline')
    monkeypatch.setattr(chain, 'configured_recommendation_client', lambda: FailingClient())
    monkeypatch.setattr(api, 'transcribe_audio', lambda _: '저녁 추천')
    monkeypatch.setattr(api, 'parse_recommendation_request', lambda _: RecommendationRequest(household_id=1, participant_ids=[1], meal_type='dinner'))
    client = app.test_client()
    response = client.post('/api/voice/recommendations', data={'audio': (BytesIO(b'audio'), 'input.wav'), 'household_id': '1', 'participant_ids': '[1,3]', 'meal_type': 'dinner'}, content_type='multipart/form-data').json
    assert response['status'] == 'api_error' and response['recommendations'] == []
    saved = client.get(f"/api/recommendations/{response['request_id']}").json
    assert saved['status'] == 'api_error' and saved['recommendations'] == []

@pytest.mark.parametrize("field", ["name","device_id","warning","session_token","transcript","health_details"])
def test_http_adapter_blocks_nested_fields_before_transport(monkeypatch, field):
    payload = PrivacyGateway().prepare(_request(), _rules()).payload
    payload["validated_candidates"][0][field] = "synthetic"
    monkeypatch.setattr(openai_client.request, "urlopen", lambda *_a, **_kw: pytest.fail("unexpected transport"))
    with pytest.raises(OpenAIClientError, match="External payload rejected"):
        HttpOpenAIRecommendationClient().explain(payload)
