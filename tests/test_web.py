import app.main as main


def test_inventory_endpoint_returns_items():
    client = main.app.test_client()
    response = client.get("/api/inventory")
    assert response.status_code == 200
    assert response.json["items"]


def test_chat_endpoint_uses_policy_guard(monkeypatch):
    monkeypatch.setattr(main, "ask_ollama", lambda *_: ("감자烹飯을 드셔보세요.", {}))
    client = main.app.test_client()
    response = client.post("/api/chat", json={"question": "떡으로 메뉴 추천해줘"})
    assert response.status_code == 200
    assert "감자" not in response.json["answer"]


def test_inventory_question_uses_ollama_only_to_rephrase_confirmed_facts(monkeypatch):
    captured = {}

    def fake_ollama(question, _context, decision_facts):
        captured["decision_facts"] = decision_facts
        assert question == "떡은 몇 개 남았어?"
        return "떡은 300g 남아 있어요.", {}

    monkeypatch.setattr(main, "ask_ollama", fake_ollama)
    client = main.app.test_client()
    response = client.post("/api/chat", json={"question": "떡은 몇 개 남았어?"})
    assert response.status_code == 200
    assert response.json["answer_source"] == "inventory"
    assert response.json["model"] == main.configured_model()
    assert response.json["answer"] == "떡은 300g 남아 있어요."
    assert captured["decision_facts"] == {"item": "떡", "quantity": 300, "unit": "g"}


def test_invalid_rephrased_fact_falls_back_to_code_answer(monkeypatch):
    monkeypatch.setattr(main, "ask_ollama", lambda *_: ("떡은 많이 남아 있어요.", {}))
    client = main.app.test_client()
    response = client.post("/api/chat", json={"question": "떡은 몇 개 남았어?"})
    assert response.status_code == 200
    assert response.json["answer"] == "떡은(는) 300g 남아 있습니다."


def test_safety_question_uses_fixed_notice_without_ollama(monkeypatch):
    monkeypatch.setattr(main, "ask_ollama", lambda *_: (_ for _ in ()).throw(AssertionError("Ollama should not run")))
    client = main.app.test_client()
    response = client.post("/api/chat", json={"question": "떡 먹어도 안전해?"})
    assert response.status_code == 200
    assert "냄새·색·점액을 확인하고 의심되면 버리세요" in response.json["answer"]


def test_location_question_is_not_misclassified_as_safety(monkeypatch):
    monkeypatch.setattr(main, "ask_ollama", lambda question, _context, _facts: ("버섯은 냉장실에 보관 중입니다.", {}))
    client = main.app.test_client()
    response = client.post("/api/chat", json={"question": "버섯은 어디에 보관돼 있어?"})
    assert response.status_code == 200
    assert response.json["answer_source"] == "inventory"
    assert "냉장실" in response.json["answer"]
    assert "냄새·색·점액" not in response.json["answer"]


def test_recipe_query_does_not_select_partial_ingredient_match(monkeypatch):
    monkeypatch.setattr(
        "app.services.fact_responder.recipes_for_inventory",
        lambda *_args, **_kwargs: [
            {"name": "고추장 라따뚜이", "matched_items": ["고추장"], "additional_core_ingredients": [], "nutrition": {"energy_kcal": 62.6}},
        ],
    )
    client = main.app.test_client()
    response = client.post("/api/chat", json={"question": "떡과 고추장으로 매콤한 메뉴를 추천해줘"})
    assert response.status_code == 200
    assert response.json["answer_source"] == "inventory"
    assert "고추장 라따뚜이" not in response.json["answer"]


def test_recipe_query_does_not_select_recipe_needing_extra_core_ingredient(monkeypatch):
    monkeypatch.setattr(
        "app.services.fact_responder.recipes_for_inventory",
        lambda *_args, **_kwargs: [
            {
                "name": "꼬막떡꼬치구이",
                "matched_items": ["떡", "고추장"],
                "additional_core_ingredients": ["꼬막"],
                "nutrition": {"energy_kcal": 220.8},
            },
        ],
    )
    client = main.app.test_client()
    response = client.post("/api/chat", json={"question": "떡과 고추장으로 매콤한 메뉴를 추천해줘"})
    assert response.status_code == 200
    assert response.json["answer_source"] == "inventory"
    assert "꼬막" not in response.json["answer"]


def test_nutrition_endpoint_requires_food_name():
    client = main.app.test_client()
    response = client.get("/api/nutrition")
    assert response.status_code == 400


def test_recipe_query_can_assume_only_basic_pantry_ingredients(monkeypatch):
    monkeypatch.setattr(
        "app.services.fact_responder.recipes_for_inventory",
        lambda *_args, **_kwargs: [
            {
                "name": "떡고추장볶음",
                "matched_items": ["떡", "고추장"],
                "additional_core_ingredients": ["간장"],
                "nutrition": {"energy_kcal": 180},
            },
        ],
    )
    client = main.app.test_client()
    response = client.post("/api/chat", json={"question": "떡과 고추장으로 매콤한 메뉴를 추천해줘."})
    assert response.status_code == 200
    assert response.json["answer_source"] == "recipe_db"
    assert "떡고추장볶음" in response.json["answer"]


def test_recipe_query_uses_local_pantry_template_when_no_db_candidate(monkeypatch):
    monkeypatch.setattr("app.services.fact_responder.recipes_for_inventory", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(main, "ask_ollama", lambda *_: (_ for _ in ()).throw(AssertionError("Ollama should not run")))
    client = main.app.test_client()
    response = client.post("/api/chat", json={"question": "떡과 고추장으로 매콤한 메뉴를 추천해줘."})
    assert response.status_code == 200
    assert response.json["answer_source"] == "inventory"
    assert "고추장 떡볶이 스타일" in response.json["answer"]
