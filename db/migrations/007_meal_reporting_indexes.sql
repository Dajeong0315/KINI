-- Read paths only; no historical nutrition recomputation or record backfill.
CREATE INDEX IF NOT EXISTS meal_history_household ON meal_sessions(household_id,id);
CREATE INDEX IF NOT EXISTS meal_history_session ON meal_records(session_id,id);
CREATE INDEX IF NOT EXISTS meal_history_member ON meal_record_items(member_id,record_id);
