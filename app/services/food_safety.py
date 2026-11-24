from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

import requests

from .errors import ServiceError


RECIPE_URL = "https://openapi.foodsafetykorea.go.kr/api"
NUTRITION_URL = "https://apis.data.go.kr/1471000/FoodNtrCpntDbInfo03/getFoodNtrCpntDbInq03"
_recipe_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
CACHE_SECONDS = 600
COOKING_WORDS = (
    "볶음밥", "덮밥", "떡볶이", "볶음", "꼬치", "구이", "조림", "튀김", "무침",
    "찌개", "샐러드", "김밥", "파스타", "스프", "볶이", "국", "탕", "찜",
    "전", "죽", "밥", "면", "피자", "버거", "구",
)
DESCRIPTIVE_WORDS = ("매콤한", "매콤", "저염", "건강", "간편", "영양", "특별", "맛있는")


def _environment_value(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ServiceError(f"{name} 환경 변수가 설정되지 않았습니다.")
    return value


def _as_number(value: Any) -> float | None:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _missing_core_ingredients(title: str, ingredients: str, item_names: list[str]) -> list[str]:
    """비정형 레시피명에서 재고에 없는 핵심 식재료 후보를 보수적으로 찾는다.

    COOKRCP01 재료 문자열은 구조화된 목록이 아니므로, 이 값은 '전체 재료 충족' 판정이
    아니라 사용자가 반드시 확인해야 할 추가 핵심 재료 안내에만 사용한다.
    """
    remainder = title.replace(" ", "")
    for name in sorted(item_names, key=len, reverse=True):
        remainder = remainder.replace(name, "")
    for word in sorted((*COOKING_WORDS, *DESCRIPTIVE_WORDS), key=len, reverse=True):
        remainder = remainder.replace(word, "")
    if len(remainder) >= 2 and remainder in ingredients:
        return [remainder]
    return []


def _recipe_from_row(row: dict[str, Any], matched_items: list[str], item_names: list[str]) -> dict[str, Any]:
    steps = [row.get(f"MANUAL{index:02d}", "").strip() for index in range(1, 21)]
    ingredients = row.get("RCP_PARTS_DTLS", "")
    title = row.get("RCP_NM", "이름 없는 레시피")
    additional_core_ingredients = _missing_core_ingredients(title, ingredients, item_names)
    return {
        "id": row.get("RCP_SEQ", ""),
        "name": title,
        "method": row.get("RCP_WAY2", ""),
        "category": row.get("RCP_PAT2", ""),
        "ingredients": ingredients,
        "matched_items": matched_items,
        "additional_core_ingredients": additional_core_ingredients,
        "image_url": row.get("ATT_FILE_NO_MAIN", ""),
        "steps": [step for step in steps if step],
        "nutrition": {
            "serving": row.get("INFO_WGT", "미기재"),
            "energy_kcal": _as_number(row.get("INFO_ENG")),
            "carbohydrate_g": _as_number(row.get("INFO_CAR")),
            "protein_g": _as_number(row.get("INFO_PRO")),
            "fat_g": _as_number(row.get("INFO_FAT")),
            "sodium_mg": _as_number(row.get("INFO_NA")),
        },
        "source": "식품안전나라 조리식품의 레시피 DB (COOKRCP01)",
    }


def _recipes_for_ingredient(ingredient: str) -> list[dict[str, Any]]:
    cached = _recipe_cache.get(ingredient)
    if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]

    key = _environment_value("FOODSAFETY_KOREA_API_KEY")
    # COOKRCP01은 인증키와 필터를 URL 경로의 일부로 받는다.
    url = (
        f"{RECIPE_URL}/{quote(key, safe='')}/COOKRCP01/json/1/12/"
        f"RCP_PARTS_DTLS={quote(ingredient, safe='')}"
    )
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise ServiceError("식품안전나라 레시피 데이터를 불러오지 못했습니다.") from exc

    result = data.get("COOKRCP01", {})
    if not isinstance(result, dict):
        raise ServiceError("식품안전나라 레시피 응답 형식이 예상과 다릅니다.")
    rows = result.get("row", [])
    if not isinstance(rows, list):
        rows = []
    _recipe_cache[ingredient] = (time.monotonic(), rows)
    return rows


def recipes_for_inventory(items: list[dict[str, Any]], limit: int = 6) -> list[dict[str, Any]]:
    """재고명별 검색 결과를 합쳐, 실제 재고와 겹치는 수가 많은 레시피를 앞에 둔다."""
    item_names = [item["name"] for item in items if item.get("name")]
    collected: dict[str, tuple[dict[str, Any], set[str]]] = {}
    for ingredient in item_names:
        for row in _recipes_for_ingredient(ingredient):
            recipe_id = str(row.get("RCP_SEQ", ""))
            if not recipe_id:
                continue
            ingredient_text = row.get("RCP_PARTS_DTLS", "")
            matched = {name for name in item_names if name in ingredient_text}
            if recipe_id not in collected:
                collected[recipe_id] = (row, matched)
            else:
                collected[recipe_id][1].update(matched)

    mapped = [_recipe_from_row(row, sorted(matched), item_names) for row, matched in collected.values()]
    return sorted(
        mapped,
        key=lambda recipe: (
            bool(recipe["additional_core_ingredients"]),
            -len(recipe["matched_items"]),
            recipe["name"],
        ),
    )[:limit]


def nutrition_for_food(food_name: str) -> dict[str, Any] | None:
    key = unquote(_environment_value("DATA_GO_KR_SERVICE_KEY"))
    try:
        response = requests.get(
            NUTRITION_URL,
            params={
                "serviceKey": key,
                "pageNo": 1,
                "numOfRows": 5,
                "type": "json",
                "FOOD_NM_KR": food_name,
            },
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise ServiceError("식품영양성분 DB를 불러오지 못했습니다.") from exc

    # 제공 포맷은 계정·응답 형식에 따라 response.body 또는 최상위 body를 쓴다.
    body = data.get("response", {}).get("body") or data.get("body", {})
    items = body.get("items", {})
    if isinstance(items, list):
        rows = items
    else:
        rows = items.get("item", []) if isinstance(items, dict) else []
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list) or not rows:
        return None
    row = next((candidate for candidate in rows if candidate.get("FOOD_NM_KR") == food_name), rows[0])
    return {
        "food_name": row.get("FOOD_NM_KR", food_name),
        "food_code": row.get("FOOD_CD", ""),
        "serving_size": row.get("SERVING_SIZE", "미기재"),
        "category": row.get("FOOD_CAT1_NM", ""),
        "energy_kcal": _as_number(row.get("AMT_NUM1")),
        "protein_g": _as_number(row.get("AMT_NUM3")),
        "fat_g": _as_number(row.get("AMT_NUM4")),
        "carbohydrate_g": _as_number(row.get("AMT_NUM6")),
        "sugar_g": _as_number(row.get("AMT_NUM7")),
        "sodium_mg": _as_number(row.get("AMT_NUM13")),
        "source": "식품의약품안전처 식품영양성분DB정보",
        "match_note": "식품명 검색의 첫 일치 결과입니다. 제품·조리 상태에 따라 값이 다를 수 있습니다.",
    }
