from __future__ import annotations

from typing import Any

from .openai_client import OpenAIClientError, OpenAIRecommendationClient, configured_recommendation_client
from .privacy_gateway import PrivacyGateway

API_ERROR_MESSAGE = "AI 연결 오류로 추천을 만들지 못했습니다. 잠시 후 다시 시도해 주세요."


def enrich_with_ai(request: dict[str, Any], rule_result: dict[str, Any], *, gateway: PrivacyGateway | None = None, client: OpenAIRecommendationClient | None = None) -> dict[str, Any]:
    if rule_result.get("status") != "recommendations_available":
        return rule_result
    decision = (gateway or PrivacyGateway()).prepare(request, rule_result)
    if decision.status != "allowed":
        return _terminal("privacy_blocked", "개인정보 보호를 위해 AI 추천을 처리할 수 없습니다.", rule_result, decision.reason)
    try:
        response = (client or configured_recommendation_client()).explain(decision.payload or {})
        validated = _validated_explanations(rule_result["recommendations"], response.candidate_explanations)
    except OpenAIClientError as exc:
        return _terminal("api_error", API_ERROR_MESSAGE, rule_result, type(exc).__name__)
    except ValueError:
        return _terminal("api_error", API_ERROR_MESSAGE, rule_result, "unsafe_ai_structure")
    result = dict(rule_result)
    # Model prose is never user-visible.  It could contain unsafe claims even
    # when JSON shape is valid, so this deterministic local template is the
    # only explanation returned to the API/UI.
    summary = "로컬 안전 규칙으로 검증된 후보를 안내합니다."
    result["ai_explanation"] = {"summary": summary, "candidate_explanations": validated, "latency_ms": response.latency_ms, "usage": response.usage, "source": "local_template"}
    result["explanation"] = summary
    return result


def _validated_explanations(candidates: list[dict[str, Any]], explanations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    expected = {f"candidate_{index}": candidate["recipe_id"] for index, candidate in enumerate(candidates, start=1)}
    received = [item.get("candidate_code") for item in explanations if isinstance(item, dict)]
    if len(received) != len(expected) or len(set(received)) != len(received) or set(received) != set(expected):
        raise ValueError("AI output candidate codes do not match local facts")
    # Model prose is deliberately discarded; it cannot introduce ingredients,
    # safety guarantees, nutrition values, or medical suggestions.
    return [{"recipe_id": recipe_id, "reason": "로컬 규칙 엔진이 검증한 후보입니다."} for recipe_id in expected.values()]


def _terminal(status: str, message: str, original: dict[str, Any], reason: str | None) -> dict[str, Any]:
    return {
        "request_id": original.get("request_id"), "rules_version": original.get("rules_version"),
        "prompt_version": original.get("prompt_version"), "status": status, "message": message,
        "recommendations": [], "excluded": [], "excluded_candidates": [],
        "data_quality": original.get("data_quality", "confirmation_required"),
        "warnings": list(original.get("warnings", [])), "requires_confirmation": original.get("requires_confirmation", True),
        "ai_error_code": reason,
    }
