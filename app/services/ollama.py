from __future__ import annotations

import os
import json
from pathlib import Path
from typing import Any

import requests

from .errors import ServiceError


PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "system.md"
DEFAULT_MODEL = "qwen2.5:3b-instruct-q4_K_M"


def configured_model() -> str:
    return (os.getenv("OLLAMA_MODEL") or "").strip() or DEFAULT_MODEL


def ask_ollama(
    question: str,
    inventory_context: str,
    decision_facts: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    try:
        system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ServiceError("시스템 프롬프트 파일을 찾을 수 없습니다.") from exc

    model = configured_model()
    if model.split(":", 1)[0] == "qwen3":
        # 구형 Ollama에서도 Qwen3의 장문 사고 출력을 피하기 위한 모델 고유 제어 토큰.
        system_prompt = "/no_think\n" + system_prompt
    system_prompt += (
        "\n\n중요 규칙: 반드시 한국어 한글만 사용하고 한자·중국어를 사용하지 마세요. "
        "제공된 재고 목록의 품목명 외의 식재료를 제안하지 마세요. "
        "메뉴 추천은 목록에 있는 품목명을 최소 하나 이상 그대로 포함하세요."
    )
    user_content = f"확정 재고 목록:\n{inventory_context}\n\n사용자 질문: {question}"
    if decision_facts is not None:
        system_prompt += (
            "\n\n아래 결정 사실은 코드가 확정한 값입니다. 이 값만 사용해 답변의 말투를 자연스럽게 다듬으세요. "
            "새 식재료·수량·날짜·건강 판단을 추가하지 마세요. 출력은 reply 하나만 가진 JSON 객체여야 합니다."
        )
        user_content += f"\n\n코드가 확정한 결정 사실:\n{json.dumps(decision_facts, ensure_ascii=False)}"

    payload: dict[str, Any] = {
        "model": model,
        "stream": False,
        # Qwen3.5에서도 API의 사고 제어를 사용해 최종 답변만 받는다.
        "think": False,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "options": {"temperature": 0.1, "num_predict": 120, "num_ctx": 4096, "seed": 7},
    }
    if decision_facts is not None:
        payload["format"] = {
            "type": "object",
            "properties": {"reply": {"type": "string"}},
            "required": ["reply"],
            "additionalProperties": False,
        }
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    try:
        response = requests.post(f"{base_url}/api/chat", json=payload, timeout=90)
    except requests.RequestException as exc:
        raise ServiceError("Ollama에 연결할 수 없습니다. Ollama가 실행 중인지 확인하세요.") from exc

    if response.status_code == 404:
        raise ServiceError(f"Ollama 모델 '{configured_model()}'을 찾을 수 없습니다. ollama list로 모델명을 확인하세요.")
    if not response.ok:
        raise ServiceError("Ollama 응답을 생성하지 못했습니다.")

    data = response.json()
    try:
        answer = data["message"]["content"].strip()
    except (KeyError, TypeError) as exc:
        raise ServiceError("Ollama 응답 형식이 예상과 다릅니다.") from exc
    if decision_facts is not None:
        try:
            answer = json.loads(answer)["reply"].strip()
        except (json.JSONDecodeError, KeyError, AttributeError, TypeError) as exc:
            raise ServiceError("Ollama가 규칙에 맞는 답변 형식을 만들지 못했습니다.") from exc
    return answer, data
