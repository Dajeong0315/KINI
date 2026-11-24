from app.services.inventory import inventory_context, load_inventory, matching_items


def test_inventory_has_demo_items():
    inventory = load_inventory()
    assert {item["name"] for item in inventory["items"]} >= {"떡", "버섯", "고추장"}


def test_matching_items_limits_context_when_name_is_in_question():
    inventory = load_inventory()
    selected = matching_items("버섯은 얼마나 남았어?", inventory)
    assert [item["name"] for item in selected] == ["버섯"]
    assert "버섯" in inventory_context(selected)
    assert "고추장" not in inventory_context(selected)
