-- MCP migration: kini_food_catalog_fk_indexes
CREATE INDEX catalog_foods_source_record_idx ON public.catalog_foods(release_id,external_id,kind);
CREATE INDEX catalog_recipes_source_record_idx ON public.catalog_recipes(release_id,external_id,kind);
