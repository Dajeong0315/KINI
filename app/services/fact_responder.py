from __future__ import annotations

from typing import Any

from .food_safety import recipes_for_inventory
from .inventory import matching_items
from .response_policy import SAFETY_NOTICE


SAFETY_WORDS = ("상했", "상한", "먹어도", "안전", "유통기한", "소비기한")
QUANTITY_WORDS = ("몇", "얼마", "수량", "양")
LOCATION_WORDS = ("어디", "보관", "냉장", "냉동")
EXPIRY_WORDS = ("유통기한", "소비기한", "기한")
RECIPE_WORDS = ("추천", "메뉴", "요리", "만들", "해먹")
LIST_WORDS = ("뭐", "무엇", "목록", "재료", "전체")
AVAILABILITY_WORDS = ("있", "남았", "없")


def _item_names(items: list[dict[str, Any]]) -> str:
    return ", ".join(item["name"] for item in items)


# 재고에 없어도 조리 시 보유한 것으로 가정하는 최소 기본 양념이다.
# 고기·해산물·채소·주식 재료는 여기에 넣지 않는다.
PANTRY_STAPLES = {
    "소금", "설탕", "간장", "식용유", "참기름", "후추", "마늘", "대파", "파", "물", "깨",
}


def _missing_non_pantry_ingredients(recipe: dict[str, Any]) -> list[str]:
    """합의한 기본 양념 이외에는 재고에 있어야 한다."""
    return [
        name
        for name in recipe["additional_core_ingredients"]
        if name.replace(" ", "") not in PANTRY_STAPLES
    ]


def _pantry_recipe_answer(mentioned: list[dict[str, Any]]) -> dict[str, Any] | None:
    """DB 후보가 없을 때도 확인된 재고 조합만으로 낼 수 있는 제한된 제안이다."""
    names = {item["name"] for item in mentioned}
    if not {"떡", "고추장"}.issubset(names):
        return None

    optional = [name for name in ("버섯", "계란") if name in names]
    optional_text = f" {', '.join(optional)}도 함께 넣을 수 있습니다." if optional else ""
    return {
        "answer": (
            "현재 재고의 떡과 고추장으로 매콤한 고추장 떡볶이 스타일 볶음을 추천합니다. "
            f"소금·설탕·간장·식용유 등 기본 양념은 있다고 가정했습니다.{optional_text}"
        ),
        "selected_items": mentioned,
        "answer_source": "inventory",
        "llm_rephrase": False,
    }


def _inventory_list_answer(items: list[dict[str, Any]]) -> str:
    details = ", ".join(f"{item['name']} {item['quantity']}{item['unit']}" for item in items)
    return f"현재 등록된 재고는 {details}입니다."


def _rephraseable(
    answer: str,
    selected_items: list[dict[str, Any]],
    *,
    required_phrases: list[str],
    facts: dict[str, Any],
    answer_source: str = "inventory",
) -> dict[str, Any]:
    """코드가 확정한 사실은 유지하고, 말투만 LLM이 다듬게 한다."""
    return {
        "answer": answer,
        "selected_items": selected_items,
        "answer_source": answer_source,
        "llm_rephrase": True,
        "required_phrases": required_phrases,
        "decision_facts": facts,
    }


def _item_answer(question: str, item: dict[str, Any]) -> str:
    name = item["name"]
    if any(word in question for word in SAFETY_WORDS):
        if item.get("use_by"):
            first = f"{name}의 기록된 소비기한은 {item['use_by']}입니다."
        else:
            first = f"{name}의 소비기한 정보는 현재 재고에 기록되어 있지 않습니다."
        return f"{first} {SAFETY_NOTICE}"
    if any(word in question for word in EXPIRY_WORDS):
        value = item.get("use_by") or "미입력"
        return f"{name}의 소비기한은 {value}로 기록되어 있습니다."
    if any(word in question for word in LOCATION_WORDS):
        return f"{name}은(는) {item['location']}에 보관 중입니다."
    if any(word in question for word in QUANTITY_WORDS):
        return f"{name}은(는) {item['quantity']}{item['unit']} 남아 있습니다."
    return f"{name}은(는) {item['quantity']}{item['unit']} 있으며 {item['location']}에 보관 중입니다."


def _item_decision(question: str, item: dict[str, Any]) -> dict[str, Any]:
    """안전 판정은 템플릿으로 고정하고, 일반 재고 사실은 자연어 다듬기를 허용한다."""
    name = item["name"]
    answer = _item_answer(question, item)
    if any(word in question for word in SAFETY_WORDS):
        return {
            "answer": answer,
            "selected_items": [item],
            "answer_source": "inventory",
            "llm_rephrase": False,
        }

    if any(word in question for word in EXPIRY_WORDS):
        required_phrases = [name, item.get("use_by") or "미입력"]
        facts = {"item": name, "use_by": item.get("use_by")}
    elif any(word in question for word in LOCATION_WORDS):
        required_phrases = [name, item["location"]]
        facts = {"item": name, "location": item["location"]}
    elif any(word in question for word in QUANTITY_WORDS):
        required_phrases = [name, f"{item['quantity']}{item['unit']}"]
        facts = {"item": name, "quantity": item["quantity"], "unit": item["unit"]}
    else:
        required_phrases = [name, f"{item['quantity']}{item['unit']}", item["location"]]
        facts = {
            "item": name,
            "quantity": item["quantity"],
            "unit": item["unit"],
            "location": item["location"],
        }
    return _rephraseable(answer, [item], required_phrases=required_phrases, facts=facts)


def fact_answer(question: str, inventory: dict[str, Any]) -> dict[str, Any] | None:
    """확정 가능한 재고·레시피 사실만으로 답할 수 있으면 LLM 호출 없이 반환한다."""
    items = inventory["items"]
    mentioned = [item for item in items if item["name"] in question]
    lower_question = question.replace(" ", "")

    if any(word in question for word in LIST_WORDS) and any(word in question for word in AVAILABILITY_WORDS):
        return _rephraseable(
            _inventory_list_answer(items),
            items,
            required_phrases=[item["name"] for item in items],
            facts={"items": [{"name": item["name"], "quantity": item["quantity"], "unit": item["unit"]} for item in items]},
        )

    if mentioned and any(word in question for word in RECIPE_WORDS):
        try:
            # 질문에 직접 언급한 재료는 모두 레시피 재료에 포함돼야 한다.
            # 하나만 겹치는 '고추장 라따뚜이' 같은 후보는 추천하지 않는다.
            required_names = {item["name"] for item in mentioned}
            recipes = [
                recipe
                for recipe in recipes_for_inventory(mentioned, limit=24)
                if required_names.issubset(set(recipe["matched_items"]))
            ]
        except Exception:
            recipes = []
        directly_usable = [
            recipe
            for recipe in recipes
            if not _missing_non_pantry_ingredients(recipe)
        ]
        if directly_usable:
            recipe = directly_usable[0]
            matched = ", ".join(recipe["matched_items"])
            energy = recipe["nutrition"]["energy_kcal"]
            calorie = f", 1인분 기준 {energy:g}kcal입니다" if energy is not None else ""
            answer = f"식약처 레시피 중 {recipe['name']}을 참고할 수 있습니다. 현재 재고에서는 {matched}을 활용할 수 있습니다{calorie}."
            return _rephraseable(
                answer,
                mentioned,
                required_phrases=[recipe["name"], *recipe["matched_items"]],
                facts={
                    "recipe_name": recipe["name"],
                    "matched_items": recipe["matched_items"],
                    "energy_kcal": energy,
                    "additional_core_ingredients": [],
                },
                answer_source="recipe_db",
            )
        pantry_answer = _pantry_recipe_answer(mentioned)
        if pantry_answer:
            return pantry_answer
        return {
            "answer": f"식약처 레시피에서 {_item_names(mentioned)}을 모두 포함하면서 추가 핵심 재료가 확인되지 않은 후보를 찾지 못했습니다. 현재 재고만으로 만들 수 있는 메뉴는 확정하지 않습니다.",
            "selected_items": mentioned,
            "answer_source": "inventory",
        }

    if mentioned:
        return _item_decision(question, mentioned[0])

    if any(word in question for word in AVAILABILITY_WORDS) and not any(word in question for word in LIST_WORDS):
        return {
            "answer": f"현재 등록된 재고는 {_item_names(items)}입니다. 질문한 품목은 등록된 재고에서 확인되지 않습니다.",
            "selected_items": [],
            "answer_source": "inventory",
        }

    return None
