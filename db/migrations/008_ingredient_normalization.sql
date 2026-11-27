-- Evidence fields only. Existing aliases/weights remain unverified by default.
ALTER TABLE ingredient_aliases ADD COLUMN IF NOT EXISTS alias_type TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE ingredient_aliases ADD COLUMN IF NOT EXISTS confidence DOUBLE PRECISION;
ALTER TABLE ingredient_aliases ADD COLUMN IF NOT EXISTS source_type TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE unit_conversions ADD COLUMN IF NOT EXISTS from_unit TEXT;
ALTER TABLE unit_conversions ADD COLUMN IF NOT EXISTS to_unit TEXT;
ALTER TABLE unit_conversions ADD COLUMN IF NOT EXISTS factor DOUBLE PRECISION;
ALTER TABLE unit_conversions ADD COLUMN IF NOT EXISTS confidence DOUBLE PRECISION;
ALTER TABLE unit_conversions ADD COLUMN IF NOT EXISTS note TEXT;
ALTER TABLE unit_conversions ADD COLUMN IF NOT EXISTS source_type TEXT NOT NULL DEFAULT 'unknown';
