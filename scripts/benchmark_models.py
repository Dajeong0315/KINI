"""동일 질의 세트로 설치된 Ollama 모델을 비교한다.

예: py .\\scripts\\benchmark_models.py --models qwen2.5:3b-instruct-q4_K_M qwen3:4b
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

import requests


QUESTIONS = [
    "냉장고 재료는 떡 300g, 버섯 200g, 고추장 1통, 달걀 4개야. 매콤한 저녁 메뉴를 추천해줘.",
    "냉장고에 감자가 있니?",
    "떡의 소비기한 정보가 없을 때 안전하게 어떻게 안내해야 해?",
]


def run_question(model: str, question: str) -> dict[str, object]:
    started = time.perf_counter()
    response = requests.post(
        "http://localhost:11434/api/chat",
        json={
            "model": model,
            "stream": False,
            "think": False,
            "messages": [
                {"role": "system", "content": ("/no_think\n" if model.startswith("qwen3") else "") + "한국어로만, 두 문장 이내로 답하세요. 재료를 추측하지 마세요."},
                {"role": "user", "content": question},
            ],
            "options": {"temperature": 0.1, "num_predict": 120, "seed": 7},
        },
        timeout=120,
    )
    response.raise_for_status()
    data = response.json()
    elapsed = time.perf_counter() - started
    eval_count = data.get("eval_count", 0)
    eval_duration = data.get("eval_duration", 0)
    tokens_per_second = (eval_count / (eval_duration / 1_000_000_000)) if eval_duration else None
    return {
        "question": question,
        "answer": data["message"]["content"].strip(),
        "elapsed_seconds": round(elapsed, 2),
        "eval_tokens": eval_count,
        "tokens_per_second": round(tokens_per_second, 2) if tokens_per_second else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="+", required=True, help="비교할 Ollama 모델명")
    args = parser.parse_args()
    result = {"recorded_at": datetime.now().astimezone().isoformat(), "models": {}}
    for model in args.models:
        print(f"측정 중: {model}")
        result["models"][model] = [run_question(model, question) for question in QUESTIONS]

    output_dir = Path("docs") / "benchmarks"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"benchmark-{datetime.now():%Y%m%d-%H%M%S}.json"
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"저장 완료: {output_path}")


if __name__ == "__main__":
    main()
