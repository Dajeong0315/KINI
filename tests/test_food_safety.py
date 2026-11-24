from app.services.food_safety import _recipe_from_row, nutrition_for_food


def test_recipe_mapping_keeps_source_and_steps():
    recipe = _recipe_from_row(
        {
            "RCP_SEQ": "1",
            "RCP_NM": "테스트 떡볶이",
            "RCP_PARTS_DTLS": "떡, 고추장",
            "INFO_ENG": "120",
            "MANUAL01": "떡을 준비한다.",
        },
        ["떡", "고추장"],
        ["떡", "고추장"],
    )
    assert recipe["name"] == "테스트 떡볶이"
    assert recipe["nutrition"]["energy_kcal"] == 120.0
    assert recipe["steps"] == ["떡을 준비한다."]
    assert recipe["matched_items"] == ["떡", "고추장"]


def test_recipe_mapping_marks_missing_core_ingredient_from_title():
    recipe = _recipe_from_row(
        {"RCP_SEQ": "2", "RCP_NM": "꼬막떡꼬치구이", "RCP_PARTS_DTLS": "꼬막, 떡, 고추장"},
        ["떡", "고추장"],
        ["떡", "고추장"],
    )
    assert recipe["additional_core_ingredients"] == ["꼬막"]


def test_nutrition_returns_none_for_empty_api_items(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"response": {"body": {"items": {}}}}

    monkeypatch.setenv("DATA_GO_KR_SERVICE_KEY", "test-key")
    monkeypatch.setattr("app.services.food_safety.requests.get", lambda *args, **kwargs: FakeResponse())
    assert nutrition_for_food("떡") is None
