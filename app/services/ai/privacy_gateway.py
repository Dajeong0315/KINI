from __future__ import annotations

"""Deterministic, allowlist-only boundary for external AI requests.

This module deliberately does not attempt to anonymize arbitrary user data.  It
constructs a new payload from local rule-engine facts and rejects unsafe source
fields before anything is eligible to leave the device.
"""

from dataclasses import dataclass
import re
import math
from typing import Any, Protocol


FORBIDDEN_FIELDS = {
    "name", "user_id", "member_id", "household_id", "device", "audio",
    "voice", "transcript", "conversation", "message", "address", "phone",
    "location", "token", "api_key", "password", "medical", "diagnosis",
    "disease", "test_result", "meal_history", "inventory_snapshot",
    "profile_snapshot",
}
FREE_TEXT_FIELDS = {"text", "desired_menu", "question", "content", "transcript"}
DETAILED_HEALTH_PATTERN = re.compile(
    r"(\ub2f9\ub1e8|\uace0\ud608\uc555|\uc554|\uc2e0\uc7a5|\uac04\uc9c8\ud658|\uc2ec\uc7a5|\uac11\uc0c1\uc120|\uc6b0\uc6b8|\uc9c4\ub2e8|\uac80\uc0ac|\uc9c8\ud658|disease|diagnosis)", re.I
)
ALLOWED_MEAL_TYPES = {"breakfast", "lunch", "dinner", "snack"}
CONSTRAINT_CODE_PATTERN = re.compile(r"^exclude_ingredient:ingredient_[1-9][0-9]*$")
ALLOWED_EXCLUDED_REASON_CODES = frozenset({
    "allergen_conflict", "label_confirmation_required", "nutrition_unknown",
    "ingredient_unknown", "unit_confirmation_required",
    "food_safety_confirmation_required", "user_exclusion", "dietary_constraint",
    "health_constraint", "cook_time_limit", "inventory_insufficient",
})
ALLOWED_RECOMMENDATION_REASON_CODES = frozenset({"PERSONALIZED"})
PHONE_PATTERN = re.compile(r"(?:\+?\d{1,3}[- ]?)?\d{2,4}[- ]?\d{3,4}[- ]?\d{4}$")
ADDRESS_PATTERN = re.compile(r"(?:\b(?:si|gu|dong|ro|gil)\b|(?:시|구|동|로|길)\s*\d)", re.I)
API_KEY_PATTERN = re.compile(r"(?:sk-|api[_-]?key|bearer\s+)[A-Za-z0-9_-]{8,}", re.I)
KOREAN_NAME_PATTERN = re.compile(r"^(?:김|이|박|최|정|강|조|윤|장|임|한|오|서|신|권)[가-힣]{2}$")


@dataclass(frozen=True)
class PrivacyDecision:
    status: str
    payload: dict[str, Any] | None = None
    reason: str | None = None


class LocalPrivacyInspector(Protocol):
    """Optional local (for example Ollama) second opinion; never authoritative."""

    def inspect(self, payload: dict[str, Any]) -> bool: ...


def _contains_forbidden(value: Any, key: str = "") -> str | None:
    lowered = key.lower()
    if lowered in FORBIDDEN_FIELDS or (lowered.endswith("_id") and lowered not in {"recipe_id"}):
        return key
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            found = _contains_forbidden(child_value, str(child_key))
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _contains_forbidden(child, key)
            if found:
                return found
    elif isinstance(value, str):
        if key in FREE_TEXT_FIELDS or DETAILED_HEALTH_PATTERN.search(value):
            return key or "free_text"
        if PHONE_PATTERN.search(value) or ADDRESS_PATTERN.search(value) or API_KEY_PATTERN.search(value) or KOREAN_NAME_PATTERN.fullmatch(value):
            return key or "sensitive_value"
    return None


def _unsafe_source_field(value: Any, key: str = "") -> str | None:
    """Local request IDs are consumed locally and removed, not sent or rejected."""
    if key in {"household_id", "participant_ids", "excluded_recipe_ids"}:
        return None
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            found = _unsafe_source_field(child_value, str(child_key))
            if found:
                return found
        return None
    if isinstance(value, list):
        for child in value:
            found = _unsafe_source_field(child, key)
            if found:
                return found
        return None
    return _contains_forbidden(value, key)


class PrivacyGateway:
    """Builds the only payload accepted by ``OpenAIRecommendationClient``."""

    def __init__(self, local_inspector: LocalPrivacyInspector | None = None):
        self._local_inspector = local_inspector

    def prepare(self, request: dict[str, Any], rule_result: dict[str, Any]) -> PrivacyDecision:
        unsafe = _unsafe_source_field(request)
        if unsafe:
            return PrivacyDecision("privacy_blocked", reason=f"forbidden_source:{unsafe}")
        meal_type = request.get("meal_type")
        if meal_type not in ALLOWED_MEAL_TYPES:
            return PrivacyDecision("privacy_blocked", reason="unsupported_meal_type")
        candidates = rule_result.get("recommendations", [])
        facts = [self._candidate_fact(candidate, index) for index, candidate in enumerate(candidates, start=1)]
        if any(fact is None for fact in facts):
            return PrivacyDecision("privacy_blocked", reason="unvalidated_candidate_fact")
        constraint_codes = self._constraint_codes(rule_result)
        payload = {
            "schema_version": "ai-recommendation-v1",
            "participant_count": len(request.get("participant_ids", [])),
            "meal_type": meal_type,
            "cook_time_limit_minutes": request.get("cook_time_limit_minutes"),
            "purchase_allowed": bool(request.get("purchase_allowed", False)),
            # Ingredients and restriction facts below originate from completed local rules.
            "constraint_codes": self._external_constraint_codes(constraint_codes),
            "validated_candidates": facts,
            "excluded_reason_codes": self._excluded_reason_codes(rule_result),
        }
        # The allowlist is checked again after construction, so a future refactor
        # cannot accidentally add raw source fields to this boundary.
        if _contains_forbidden(payload):
            return PrivacyDecision("privacy_blocked", reason="generated_payload_not_safe")
        if self._local_inspector is not None:
            try:
                if not self._local_inspector.inspect(payload):
                    return PrivacyDecision("privacy_blocked", reason="local_inspector_rejected")
            except Exception:
                # A failing optional inspector cannot widen deterministic policy.
                pass
        return PrivacyDecision("allowed", payload=payload)

    @staticmethod
    def _constraint_codes(rule_result: dict[str, Any]) -> list[str]:
        """Accept only codes emitted by the local rule engine, never request text."""
        return sorted({code for code in rule_result.get("validated_constraint_codes", []) if isinstance(code, str) and CONSTRAINT_CODE_PATTERN.fullmatch(code)})

    @staticmethod
    def _external_constraint_codes(codes: list[str]) -> list[str]:
        """Replace stable local ingredient IDs with request-scoped opaque tokens."""
        return [f"constraint_{index}" for index, _code in enumerate(codes, start=1)]

    @staticmethod
    def _excluded_reason_codes(rule_result: dict[str, Any]) -> list[str]:
        return sorted({code for item in rule_result.get("excluded", []) if isinstance(item, dict) for code in item.get("reason_codes", []) if isinstance(code, str) and code in ALLOWED_EXCLUDED_REASON_CODES})

    @staticmethod
    def _candidate_fact(candidate: dict[str, Any], index: int) -> dict[str, Any] | None:
        if not isinstance(candidate, dict) or not isinstance(candidate.get("recipe_id"), int) or candidate["recipe_id"] <= 0:
            return None
        if not isinstance(candidate.get("cook_time_minutes"), int) or not 0 < candidate["cook_time_minutes"] <= 600:
            return None
        nutrition = candidate.get("nutrition_per_serving")
        if not isinstance(nutrition, dict):
            return None
        allowed_nutrition = {}
        for key in ("energy_kcal", "protein_g", "carbohydrate_g", "fat_g", "sodium_mg"):
            value = nutrition.get(key)
            if value is not None:
                if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or not 0 <= value <= 100000:
                    return None
                allowed_nutrition[key] = value
        if not allowed_nutrition:
            return None
        return {
            # Position is request-scoped; stable local recipe IDs never leave the device.
            "candidate_code": f"candidate_{index}",
            "cook_time_minutes": candidate["cook_time_minutes"],
            "nutrition_per_serving": allowed_nutrition,
            "recommendation_reason_codes": sorted({code for code in candidate.get("recommendation_reason_codes", []) if isinstance(code, str) and code in ALLOWED_RECOMMENDATION_REASON_CODES}),
        }
