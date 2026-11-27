-- Confirmed meal-scoped feedback policy. Apply once as project SQL administrator.
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL ROLE kini_migrator;
SET LOCAL search_path=kini_private,pg_catalog;
-- One current evaluation per saved meal; edits replace it.
-- Feature-owned schema candidate. Explicit setup only, never request-time DDL.
-- Requires existing A meal/member tables. Does not alter them.
CREATE TABLE meal_feedback (
    record_id BIGINT NOT NULL REFERENCES meal_records(id),
    member_id BIGINT NOT NULL REFERENCES members(id),
    status TEXT NOT NULL CHECK (status IN ('submitted', 'skipped')),
    taste TEXT,
    seasoning TEXT,
    difficulty TEXT,
    revision BIGINT NOT NULL CHECK (revision > 0),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (record_id),
    CHECK (
        (status = 'skipped' AND taste IS NULL AND seasoning IS NULL AND difficulty IS NULL)
        OR (status = 'submitted' AND (taste IS NOT NULL OR seasoning IS NOT NULL OR difficulty IS NOT NULL))
    )
);

ALTER TABLE meal_feedback ENABLE ROW LEVEL SECURITY;
CREATE POLICY kini_app_backend_access ON meal_feedback TO kini_app USING (true) WITH CHECK (true);
REVOKE ALL ON meal_feedback FROM PUBLIC,anon,authenticated,service_role;
GRANT SELECT,INSERT,UPDATE ON meal_feedback TO kini_app;
INSERT INTO schema_migrations(version) VALUES ('010_meal_feedback');
COMMIT;
SELECT (SELECT count(*) FROM kini_private.schema_migrations) AS migrations,
 has_table_privilege('kini_app','kini_private.meal_feedback','INSERT') AS app_can_save,
 has_table_privilege('anon','kini_private.meal_feedback','SELECT') AS anon_can_read;
