-- Account-only sessions and profiles for the existing family HTTP contract.
-- Keep 001 unchanged; upgrades retain existing accounts, sessions and inventory.
CREATE TABLE local_account_sessions (
    token_hash TEXT PRIMARY KEY,
    account_id UUID NOT NULL REFERENCES local_accounts(id) ON DELETE CASCADE,
    created_at BIGINT NOT NULL,
    expires_at BIGINT NOT NULL,
    revoked_at BIGINT,
    CHECK (expires_at > created_at)
);
CREATE INDEX local_account_session_account ON local_account_sessions(account_id);
CREATE INDEX local_account_session_expiry ON local_account_sessions(expires_at);

CREATE TABLE member_preferences (
    member_id BIGINT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (member_id, kind, value)
);
CREATE TABLE member_allergies (
    member_id BIGINT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    ingredient TEXT NOT NULL,
    PRIMARY KEY (member_id, ingredient)
);
CREATE TABLE member_health_constraints (
    member_id BIGINT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (member_id, kind, value)
);
CREATE TABLE member_nutrition_targets (
    member_id BIGINT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    nutrient TEXT NOT NULL,
    amount DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (member_id, nutrient)
);
REVOKE ALL ON local_account_sessions, member_preferences, member_allergies,
    member_health_constraints, member_nutrition_targets FROM PUBLIC;
