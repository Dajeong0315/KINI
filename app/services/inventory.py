from __future__ import annotations

import json
from pathlib import Path
from typing import Any


INVENTORY_PATH = Path(__file__).resolve().parents[1] / "data" / "inventory.json"


def load_inventory() -> dict[str, Any]:
    try:
        data = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError("재고 파일을 찾을 수 없습니다.") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("재고 JSON 형식이 올바르지 않습니다.") from exc

    if not isinstance(data.get("items"), list):
        raise ValueError("재고 JSON에는 items 배열이 필요합니다.")
    return data


def matching_items(question: str, inventory: dict[str, Any]) -> list[dict[str, Any]]:
    """질문에 품목명이 있으면 해당 품목만, 없으면 전체 재고를 돌려준다."""
    items = inventory["items"]
    matches = [item for item in items if item.get("name", "") in question]
    return matches or items


def inventory_context(items: list[dict[str, Any]]) -> str:
    if not items:
        return "확인 가능한 재고가 없습니다."
    lines = []
    for item in items:
        use_by = item.get("use_by") or "미입력"
        lines.append(
            f"- {item['name']}: {item['quantity']}{item['unit']}, "
            f"보관 위치 {item['location']}, 소비기한 {use_by}"
        )
    return "\n".join(lines)
