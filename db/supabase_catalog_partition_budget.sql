-- MCP migration: kini_catalog_partition_budget_v1. Logical budgets; no disk is preallocated.
ALTER TABLE public.catalog_storage_policy
 ADD COLUMN catalog_pool_bytes bigint NOT NULL DEFAULT 200000000 CHECK(catalog_pool_bytes>0),
 ADD COLUMN allocation_bytes jsonb NOT NULL DEFAULT '{"system":30000000,"food_nutrition":70000000,"recipes":100000000,"catalog_shared_and_search":30000000,"fridge_change_reserve":150000000,"operational_metrics":20000000,"import_and_rollback_buffer":50000000,"safety_margin":50000000}'::jsonb CHECK(jsonb_typeof(allocation_bytes)='object');
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
  'catalog_recipe_nutrients','active_data_releases');
 IF current_bytes+limits.reserve_bytes+incoming_payload_bytes*limits.expansion_factor>=limits.stop_bytes THEN
   RAISE EXCEPTION 'Catalog database capacity threshold reached' USING ERRCODE='54000';
 END IF;
 IF catalog_bytes+limits.reserve_bytes+incoming_payload_bytes*limits.expansion_factor>=limits.catalog_pool_bytes THEN
   RAISE EXCEPTION 'Public catalog pool exhausted; fridge capacity reserved' USING ERRCODE='54000';
 END IF;
END $$;
CREATE OR REPLACE FUNCTION public.catalog_capacity_status() RETURNS jsonb
LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
 SELECT jsonb_build_object('database_bytes',pg_database_size(current_database()),
 'database_quota_bytes',database_quota_bytes,'target_bytes',target_bytes,'warning_bytes',warning_bytes,
 'stop_bytes',stop_bytes,'reserve_bytes',reserve_bytes,'catalog_pool_bytes',catalog_pool_bytes,
 'allocation_bytes',allocation_bytes,'scope','database_only_not_wal_or_object_storage')
 FROM public.catalog_storage_policy WHERE singleton=true
$$;
