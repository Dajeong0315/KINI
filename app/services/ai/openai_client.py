from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib import error, request


class OpenAIClientError(RuntimeError): pass
class OpenAITimeoutError(OpenAIClientError): pass
class OpenAIMalformedResponseError(OpenAIClientError): pass


@dataclass(frozen=True)
class AIExplanation:
    summary: str
    candidate_explanations: list[dict[str, Any]]
    latency_ms: float | None = None
    usage: dict[str, Any] | None = None


class OpenAIRecommendationClient(Protocol):
    def explain(self, payload: dict[str, Any]) -> AIExplanation: ...


class FakeOpenAIRecommendationClient:
    """Offline deterministic client used by default and by tests; makes no network call."""
    def explain(self, payload: dict[str, Any]) -> AIExplanation:
        started = time.perf_counter()
        items = [{"candidate_code": item["candidate_code"], "reason": "ignored by the local display policy"} for item in payload["validated_candidates"]]
        return AIExplanation("로컬 안전 규칙으로 검증된 후보를 안내합니다.", items, (time.perf_counter() - started) * 1000)


class HttpOpenAIRecommendationClient:
    """Minimal HTTP adapter. The key is read only from environment at call time."""
    def __init__(self, timeout_seconds: float = 10.0):
        self.timeout_seconds = timeout_seconds

    def explain(self, payload: dict[str, Any]) -> AIExplanation:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise OpenAIClientError("OPENAI_API_KEY is not configured")
        model = os.getenv("OPENAI_RECOMMENDATION_MODEL")
        if not model:
            raise OpenAIClientError("OPENAI_RECOMMENDATION_MODEL is not configured")
        instructions = (Path(__file__).resolve().parents[2] / "prompts" / "openai_recommendation.md").read_text(encoding="utf-8")
        body = {"model": model, "instructions": instructions, "input": [{"role": "user", "content": [{"type": "input_text", "text": json.dumps(payload, ensure_ascii=False)}]}], "text": {"format": {"type": "json_object"}}}
        wire = json.dumps(body).encode("utf-8")
        req = request.Request("https://api.openai.com/v1/responses", data=wire, headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, method="POST")
        started = time.perf_counter()
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except TimeoutError as exc:
            raise OpenAITimeoutError("OpenAI request timed out") from exc
        except error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise OpenAITimeoutError("OpenAI request timed out") from exc
            raise OpenAIClientError("OpenAI request failed") from exc
        except (error.HTTPError, ValueError) as exc:
            raise OpenAIClientError("OpenAI request failed") from exc
        try:
            text = raw["output"][0]["content"][0]["text"]
            parsed = json.loads(text)
            return _parse_response(parsed, (time.perf_counter() - started) * 1000, raw.get("usage"))
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise OpenAIMalformedResponseError("OpenAI response is malformed") from exc


def _parse_response(value: Any, latency_ms: float | None = None, usage: dict[str, Any] | None = None) -> AIExplanation:
    if not isinstance(value, dict) or not isinstance(value.get("summary"), str) or not isinstance(value.get("candidate_explanations"), list):
        raise OpenAIMalformedResponseError("OpenAI response is malformed")
    explanations = value["candidate_explanations"]
    if any(not isinstance(item, dict) or not isinstance(item.get("candidate_code"), str) or not isinstance(item.get("reason"), str) for item in explanations):
        raise OpenAIMalformedResponseError("OpenAI response is malformed")
    return AIExplanation(value["summary"], explanations, latency_ms, usage)


def configured_recommendation_client() -> OpenAIRecommendationClient:
    # Opt-in external execution keeps tests and local demos offline by default.
    return HttpOpenAIRecommendationClient() if os.getenv("KINI_USE_OPENAI") == "1" else FakeOpenAIRecommendationClient()
