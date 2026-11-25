-- MCP migration: kini_catalog_capacity_v1. All byte limits use decimal MB.
CREATE TABLE public.catalog_storage_policy (
  singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
  database_quota_bytes bigint NOT NULL DEFAULT 500000000,
  target_bytes bigint NOT NULL DEFAULT 300000000,
  warning_bytes bigint NOT NULL DEFAULT 350000000,
  stop_bytes bigint NOT NULL DEFAULT 400000000,
  reserve_bytes bigint NOT NULL DEFAULT 8000000,
  max_batch_payload_bytes bigint NOT NULL DEFAULT 256000,
  expansion_factor integer NOT NULL DEFAULT 4,
  CHECK(0<target_bytes AND target_bytes<warning_bytes AND warning_bytes<stop_bytes
        AND stop_bytes<database_quota_bytes AND reserve_bytes>0
        AND max_batch_payload_bytes>0 AND expansion_factor>=1)
);
INSERT INTO public.catalog_storage_policy(singleton) VALUES(true);
ALTER TABLE public.catalog_storage_policy ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.catalog_storage_policy FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT ON public.catalog_storage_policy TO service_role;
CREATE POLICY backend_capacity_read ON public.catalog_storage_policy FOR SELECT TO service_role USING(true);

CREATE FUNCTION public.catalog_capacity_status() RETURNS jsonb
LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog,public AS $$
  SELECT jsonb_build_object('database_bytes',pg_database_size(current_database()),
    'database_quota_bytes',database_quota_bytes,'target_bytes',target_bytes,
    'warning_bytes',warning_bytes,'stop_bytes',stop_bytes,'reserve_bytes',reserve_bytes,
    'scope','database_only_not_wal_or_object_storage')
  FROM public.catalog_storage_policy WHERE singleton=true
$$;
CREATE FUNCTION public.assert_catalog_capacity(incoming_payload_bytes bigint DEFAULT 0) RETURNS void
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
DECLARE limits public.catalog_storage_policy%ROWTYPE; current_bytes bigint;
BEGIN
  PERFORM pg_advisory_xact_lock(1199094,1);
  SELECT * INTO STRICT limits FROM public.catalog_storage_policy WHERE singleton=true;
  IF incoming_payload_bytes<0 OR incoming_payload_bytes>limits.max_batch_payload_bytes THEN
    RAISE EXCEPTION 'Catalog batch payload exceeds policy' USING ERRCODE='54000';
  END IF;
  current_bytes:=pg_database_size(current_database());
  IF current_bytes+limits.reserve_bytes+incoming_payload_bytes*limits.expansion_factor>=limits.stop_bytes THEN
    RAISE EXCEPTION 'Catalog database capacity threshold reached' USING ERRCODE='54000';
  END IF;
END $$;
CREATE FUNCTION public.guard_catalog_capacity() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog,public AS $$
BEGIN
  PERFORM public.assert_catalog_capacity(0);
  RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION public.catalog_capacity_status(),public.assert_catalog_capacity(bigint),public.guard_catalog_capacity() FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.catalog_capacity_status(),public.assert_catalog_capacity(bigint),public.guard_catalog_capacity() TO service_role;
DO $$
DECLARE table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY['source_records','catalog_foods','catalog_food_nutrients',
    'catalog_recipes','catalog_recipe_steps','catalog_recipe_ingredients','catalog_recipe_nutrients']
  LOOP
    EXECUTE format('CREATE TRIGGER catalog_capacity_check BEFORE INSERT OR UPDATE ON public.%I FOR EACH STATEMENT EXECUTE FUNCTION public.guard_catalog_capacity()',table_name);
  END LOOP;
END $$;
ALTER TABLE public.source_records ADD CONSTRAINT compact_provenance_payload CHECK(octet_length(payload_json::text)<=2048);
ALTER TABLE public.catalog_recipes ADD CONSTRAINT bounded_recipe_text CHECK(length(ingredients_text)<=20000);
ALTER TABLE public.catalog_recipe_steps ADD CONSTRAINT bounded_step_text CHECK(length(instruction)<=8000);
ALTER TABLE public.catalog_recipe_ingredients ADD CONSTRAINT bounded_ingredient_text CHECK(length(original_text)<=2000);
COMMENT ON TABLE public.source_records IS 'Compact public provenance metadata plus SHA256 only. Full source pages are compressed locally; no full API payloads, credentials or personal data.';
COMMENT ON TABLE public.catalog_storage_policy IS 'KINI conservative admission thresholds, not a live billing/physical disk quota. Writer must use bounded batches and check projected payload.';
