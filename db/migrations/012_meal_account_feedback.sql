-- Account-specific evaluations recorded in the local nutrition report.
CREATE TABLE IF NOT EXISTS meal_account_feedback (
    record_id BIGINT NOT NULL REFERENCES meal_records(id) ON DELETE CASCADE,
    account_id UUID NOT NULL REFERENCES local_accounts(id) ON DELETE CASCADE,
    taste TEXT CHECK (taste IN ('bad', 'okay', 'good')),
    salt TEXT CHECK (salt IN ('bland', 'balanced', 'salty')),
    difficulty TEXT CHECK (difficulty IN ('easy', 'normal', 'hard')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (record_id, account_id),
    CHECK (taste IS NOT NULL OR salt IS NOT NULL OR difficulty IS NOT NULL)
);
REVOKE ALL ON meal_account_feedback FROM PUBLIC;

-- Provision the runtime role on existing private deployments as well.
DO $access$ BEGIN
    IF current_schema() = 'kini_private' AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname='kini_app') THEN
        ALTER TABLE meal_account_feedback ENABLE ROW LEVEL SECURITY;
        CREATE POLICY kini_app_backend_access ON meal_account_feedback TO kini_app USING (true) WITH CHECK (true);
        GRANT SELECT, INSERT, UPDATE ON meal_account_feedback TO kini_app;
    END IF;
END $access$;
