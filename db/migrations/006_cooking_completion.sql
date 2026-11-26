-- Confirmed A outflows can belong to one cooking completion only.
CREATE TABLE cooking_inventory_event_links (
    event_id BIGINT PRIMARY KEY REFERENCES inventory_events(id),
    cooking_session_id BIGINT NOT NULL REFERENCES cooking_sessions(id)
);
CREATE INDEX cooking_link_session ON cooking_inventory_event_links(cooking_session_id);
CREATE UNIQUE INDEX cooking_record_once ON cooking_sessions(meal_record_id);
CREATE UNIQUE INDEX meal_member_once ON meal_record_items(record_id,member_id);
CREATE UNIQUE INDEX intake_nutrient_once ON nutrition_intakes(record_item_id,nutrient);
REVOKE ALL ON cooking_inventory_event_links FROM PUBLIC;
