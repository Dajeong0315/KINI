-- Operator maintenance after large immutable source ingestion; not a schema migration.
-- Run during an ingestion window with no concurrent catalog writer: REINDEX takes a lock.
-- Keeps all rows and FK constraints. Never VACUUM FULL or delete existing releases automatically.
REINDEX INDEX public.source_records_pkey;
-- After food insertion completes, rebuild its primary index if measured fragmentation warrants it.
REINDEX INDEX public.catalog_foods_pkey;
