ALTER TABLE members
    ADD COLUMN IF NOT EXISTS height_cm DOUBLE PRECISION CHECK (height_cm BETWEEN 0.1 AND 300),
    ADD COLUMN IF NOT EXISTS weight_kg DOUBLE PRECISION CHECK (weight_kg BETWEEN 0.1 AND 700),
    ADD COLUMN IF NOT EXISTS activity_level TEXT
        CHECK (activity_level IN ('sedentary', 'low_active', 'active', 'very_active'));
