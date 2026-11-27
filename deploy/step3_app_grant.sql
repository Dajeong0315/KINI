-- Required by authenticated meal-session creation. No public write grant.
BEGIN;
GRANT INSERT ON kini_private.meal_data_context TO kini_app;
COMMIT;
SELECT has_table_privilege('kini_app','kini_private.meal_data_context','INSERT') AS meal_context_insert,
       has_table_privilege('kini_app','public.catalog_foods','UPDATE') AS public_food_update;
