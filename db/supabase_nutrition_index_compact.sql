-- MCP migration: kini_nutrition_index_compact_v1. Preserve rows/FKs, remove redundant indexes.
ALTER TABLE public.catalog_foods DROP CONSTRAINT catalog_foods_release_id_external_id_kind_fkey;
ALTER TABLE public.catalog_recipes DROP CONSTRAINT catalog_recipes_release_id_external_id_kind_fkey;
ALTER TABLE public.catalog_import_quarantine DROP CONSTRAINT catalog_import_quarantine_release_id_external_id_kind_fkey;
ALTER TABLE public.source_records DROP CONSTRAINT source_records_release_id_external_id_kind_key;
ALTER TABLE public.source_records DROP CONSTRAINT source_records_pkey;
ALTER TABLE public.source_records ADD PRIMARY KEY(release_id,external_id,kind);
ALTER TABLE public.catalog_foods ADD FOREIGN KEY(release_id,external_id,kind) REFERENCES public.source_records(release_id,external_id,kind);
ALTER TABLE public.catalog_recipes ADD FOREIGN KEY(release_id,external_id,kind) REFERENCES public.source_records(release_id,external_id,kind);
ALTER TABLE public.catalog_import_quarantine ADD FOREIGN KEY(release_id,external_id,kind) REFERENCES public.source_records(release_id,external_id,kind);
DROP INDEX public.catalog_foods_name_idx;
DROP INDEX public.catalog_foods_source_record_idx;
COMMENT ON TABLE public.catalog_foods IS 'Core nutrients use compact columns; lookup by PK(release_id,external_id). kind is constrained constant food. Name-search indexing deferred until measured need.';
