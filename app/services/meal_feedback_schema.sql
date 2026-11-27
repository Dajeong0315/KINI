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
    PRIMARY KEY (record_id, member_id),
    CHECK (
        (status = 'skipped' AND taste IS NULL AND seasoning IS NULL AND difficulty IS NULL)
        OR (status = 'submitted' AND (taste IS NOT NULL OR seasoning IS NOT NULL OR difficulty IS NOT NULL))
    )
);
