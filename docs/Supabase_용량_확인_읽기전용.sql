-- KINI owner: run in the existing project's SQL Editor.
-- Read-only aggregates only; no rows, keys, identities or health data returned.
-- Send the three result sets and dashboard Free plan/egress usage to the team.
BEGIN TRANSACTION READ ONLY;

SELECT
  current_timestamp AS checked_at,
  pg_database_size(current_database()) AS database_bytes,
  round(pg_database_size(current_database()) / 1000000.0, 2) AS database_mb_decimal,
  round(pg_database_size(current_database()) / 1048576.0, 2) AS database_mib;

SELECT
  count(*) AS public_table_count,
  coalesce(sum(pg_total_relation_size(c.oid)), 0) AS public_tables_and_indexes_bytes
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'm');

SELECT
  count(*) AS storage_object_count,
  coalesce(sum(CASE
    WHEN metadata->>'size' ~ '^[0-9]+$'
    THEN (metadata->>'size')::numeric ELSE 0 END), 0) AS storage_metadata_bytes,
  count(*) FILTER (WHERE metadata->>'size' IS NULL
                    OR metadata->>'size' !~ '^[0-9]+$') AS missing_size_count
FROM storage.objects;

COMMIT;

-- Storage metadata totals are an estimate, not billing's authoritative meter.
-- Also check Organization Usage: database, Storage, egress, plan and pausing.
-- Do not send a service_role key, DB password, access token or a DB export.
