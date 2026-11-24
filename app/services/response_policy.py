from __future__ import annotations

import re

SAFETY_NOTICE = "냄새·색·점액을 확인하고 의심되면 버리세요."
FALLBACK_REPLY = "재고에 있는 품목만 기준으로 다시 답해 주세요. " + SAFETY_NOTICE
HAN_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
SAFETY_TERMS = ("상했", "상한", "먹어도", "안전", "유통기한", "소비기한")


def limit_to_two_sentences(text: str) -> str:
    normalized = " ".join(text.strip().split())
    pieces = [piece.strip() for piece in re.split(r"(?<=[.!?])\s+", normalized) if piece.strip()]
    return " ".join(pieces[:2])


def _first_sentence(text: str) -> str:
    normalized = " ".join(text.strip().split())
    pieces = [piece.strip() for piece in re.split(r"(?<=[.!?])\s+", normalized) if piece.strip()]
    return pieces[0] if pieces else ""


def enforce_response_policy(
    answer: str,
    item_names: list[str],
    question: str,
    *,
    required_phrases: list[str] | None = None,
) -> str:
    """형식 오류와 명백한 품목 이탈을 안전한 응답으로 바꾼다."""
    answer = limit_to_two_sentences(answer)
    if not answer or HAN_PATTERN.search(answer):
        return FALLBACK_REPLY

    # 메뉴·재고 질문의 답은 최소 하나의 제공 재고를 직접 언급해야 한다.
    if item_names and not any(name in answer for name in item_names):
        return FALLBACK_REPLY

    if required_phrases and not all(phrase in answer for phrase in required_phrases):
        return FALLBACK_REPLY

    if any(term in question for term in SAFETY_TERMS) and SAFETY_NOTICE not in answer:
        # 불확실한 안전 안내는 첫 문장 뒤에 안전 문구를 붙여 정확히 두 문장으로 고정한다.
        answer = f"{_first_sentence(answer)} {SAFETY_NOTICE}"
    return answer
