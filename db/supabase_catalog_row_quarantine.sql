-- MCP migration: kini_catalog_row_quarantine_v1.
CREATE TABLE public.catalog_import_quarantine (
 release_id uuid NOT NULL, external_id text NOT NULL, kind text NOT NULL CHECK(kind IN ('food','recipe')),
 reason_code text NOT NULL CHECK(length(reason_code)<=80),safe_detail text NOT NULL CHECK(length(safe_detail)<=300),
 PRIMARY KEY(release_id,external_id),
 FOREIGN KEY(release_id,external_id,kind) REFERENCES public.source_records(release_id,external_id,kind)
);
ALTER TABLE public.catalog_import_quarantine ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.catalog_import_quarantine FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT,INSERT,UPDATE ON public.catalog_import_quarantine TO service_role;
CREATE POLICY backend_quarantine_access ON public.catalog_import_quarantine TO service_role USING(true) WITH CHECK(true);
CREATE TRIGGER catalog_capacity_check BEFORE INSERT OR UPDATE ON public.catalog_import_quarantine
 FOR EACH STATEMENT EXECUTE FUNCTION public.guard_catalog_capacity();
COMMENT ON TABLE public.catalog_import_quarantine IS 'Invalid public source rows remain represented by ID/hash and reason; full raw content stays in local compressed snapshots. Not recommendation candidates.';
