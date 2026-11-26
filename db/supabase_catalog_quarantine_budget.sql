-- Include rejected source rows in the shared public catalog budget.
CREATE OR REPLACE FUNCTION public.assert_catalog_capacity(incoming_payload_bytes bigint DEFAULT 0) RETURNS void
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE limits public.catalog_storage_policy%ROWTYPE; current_bytes bigint; catalog_bytes bigint;
BEGIN
 PERFORM pg_advisory_xact_lock(1199094,1);
 SELECT * INTO STRICT limits FROM public.catalog_storage_policy WHERE singleton=true;
 IF incoming_payload_bytes<0 OR incoming_payload_bytes>limits.max_batch_payload_bytes THEN
   RAISE EXCEPTION 'Catalog batch payload exceeds policy' USING ERRCODE='54000';
 END IF;
 current_bytes:=pg_database_size(current_database());
 SELECT coalesce(sum(pg_total_relation_size(c.oid)),0) INTO catalog_bytes
 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' AND c.relkind='r' AND c.relname IN
 ('data_sources','nutrient_definitions','data_releases','source_records','catalog_foods',
  'catalog_food_nutrients','catalog_recipes','catalog_recipe_steps','catalog_recipe_ingredients',
  'catalog_recipe_nutrients','active_data_releases','catalog_import_quarantine');
 IF current_bytes+limits.reserve_bytes+incoming_payload_bytes*limits.expansion_factor>=limits.stop_bytes THEN
   RAISE EXCEPTION 'Catalog database capacity threshold reached' USING ERRCODE='54000';
 END IF;
 IF catalog_bytes+limits.reserve_bytes+incoming_payload_bytes*limits.expansion_factor>=limits.catalog_pool_bytes THEN
   RAISE EXCEPTION 'Public catalog pool exhausted; fridge capacity reserved' USING ERRCODE='54000';
 END IF;
END $$;
