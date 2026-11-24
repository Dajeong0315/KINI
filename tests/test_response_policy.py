from app.services.response_policy import FALLBACK_REPLY, SAFETY_NOTICE, enforce_response_policy


def test_rejects_han_characters_from_model_output():
    answer = enforce_response_policy("매콤한 감자烹飯을 드셔보세요.", ["떡", "고추장"], "메뉴 추천해줘")
    assert answer == FALLBACK_REPLY


def test_rejects_answer_without_inventory_item():
    answer = enforce_response_policy("감자로 볶음밥을 만들어 보세요.", ["떡", "고추장"], "메뉴 추천해줘")
    assert answer == FALLBACK_REPLY


def test_adds_safety_notice_when_safety_answer_is_uncertain():
    answer = enforce_response_policy("떡의 상태는 기록만으로 판단할 수 없어요.", ["떡"], "떡 먹어도 안전해?")
    assert SAFETY_NOTICE in answer
    assert answer.endswith(SAFETY_NOTICE)


def test_limits_answer_to_two_sentences():
    answer = enforce_response_policy("떡과 고추장을 쓰세요. 버섯도 넣으세요. 달걀도 넣으세요.", ["떡", "고추장", "버섯", "달걀"], "메뉴 추천해줘")
    assert answer.count(".") == 2


def test_rejects_rephrased_answer_that_omits_required_fact():
    answer = enforce_response_policy(
        "떡은 충분히 남아 있어요.",
        ["떡"],
        "떡은 몇 개 남았어?",
        required_phrases=["떡", "300g"],
    )
    assert answer == FALLBACK_REPLY
