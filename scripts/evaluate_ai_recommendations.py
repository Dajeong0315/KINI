"""Repeatable offline contract evaluation; it does not assess real-model quality or cost."""
from __future__ import annotations

import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.ai.openai_client import AIExplanation, OpenAIMalformedResponseError
from app.services.ai.recommendation_chain import enrich_with_ai


class ScriptedClient:
    """Produces deterministic valid, malformed, and fact-mismatched responses."""
    def __init__(self, outcome: str):
        self.outcome = outcome

    def explain(self, payload: dict) -> AIExplanation:
        if self.outcome == "malformed_json":
            raise OpenAIMalformedResponseError("scripted malformed JSON")
        codes = [item["candidate_code"] for item in payload["validated_candidates"]]
        if self.outcome == "candidate_mismatch":
            codes = codes + ["candidate_999"]
        return AIExplanation("scripted text is intentionally not rendered", [{"candidate_code": code, "reason": "scripted"} for code in codes])


def evaluate(cases: list[dict]) -> dict:
    """Measure contract enforcement over all supplied fixture cases.

    JSON compliance denominator: cases that simulate a model response. A malformed
    case passes the safety check only when it is rejected as ``api_error``.
    Rule-fact mismatch denominator: cases explicitly marked ``candidate_mismatch``;
    they pass only when rejected before a recommendation is returned.
    Model latency, token count, and cost are intentionally ``null`` because this
    evaluator makes no real model request.
    """
    total = len(cases)
    response_cases = 0
    json_format_valid = 0
    json_format_invalid_cases = 0
    json_format_rejected = 0
    fact_mismatch_cases = 0
    fact_mismatch_detected = 0
    expected_outcomes = 0
    for case in cases:
        outcome = case["simulated_model_outcome"]
        result = enrich_with_ai(case["request"], case["rule_result"], client=ScriptedClient(outcome))
        if outcome == "malformed_json":
            response_cases += 1
            json_format_invalid_cases += 1
            json_format_rejected += int(result["status"] == "api_error")
            expected_outcomes += int(result["status"] == "api_error")
        elif outcome == "candidate_mismatch":
            response_cases += 1
            json_format_valid += 1
            fact_mismatch_cases += 1
            fact_mismatch_detected += int(result["status"] == "api_error")
            expected_outcomes += int(result["status"] == "api_error")
        else:
            response_cases += 1
            json_format_valid += 1
            expected_outcomes += int(result["status"] == "recommendations_available")
    return {
        "cases_total": total,
        "expected_outcome_pass_count": expected_outcomes,
        "expected_outcome_pass_rate": expected_outcomes / total if total else None,
        "json_format_check_denominator": response_cases,
        "json_format_valid_response_count": json_format_valid,
        "json_format_invalid_case_count": json_format_invalid_cases,
        "json_format_error_rejected_count": json_format_rejected,
        "rule_fact_mismatch_cases": fact_mismatch_cases,
        "rule_fact_mismatch_detected_count": fact_mismatch_detected,
        "rule_fact_mismatch_undetected_count": fact_mismatch_cases - fact_mismatch_detected,
        "model_latency_ms": None,
        "token_usage": None,
        "estimated_cost": None,
        "scope": "offline deterministic contract enforcement only; not real-model quality, latency, token, or cost evaluation",
    }


if __name__ == "__main__":
    fixture = Path(__file__).with_name("ai_evaluation_cases.json")
    print(json.dumps(evaluate(json.loads(fixture.read_text(encoding="utf-8"))), ensure_ascii=False, indent=2))
