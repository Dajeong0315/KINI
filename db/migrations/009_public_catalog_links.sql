-- App-private mapping only. No writes, copies or DDL in public.
-- Explicit external ID/release/review contract mirrors catalog_ingredient_links.
-- No public FK: application migrations do not require public REFERENCES rights.
CREATE TABLE public_catalog_ingredient_links (
    ingredient_id BIGINT PRIMARY KEY REFERENCES ingredients(id),
    release_id TEXT NOT NULL CHECK (length(trim(release_id)) > 0),
    food_id TEXT NOT NULL CHECK (length(trim(food_id)) > 0),
    expected_name TEXT NOT NULL CHECK (length(trim(expected_name)) > 0),
    reviewed_at TEXT NOT NULL CHECK (length(trim(reviewed_at)) > 0),
    review_evidence TEXT NOT NULL CHECK (length(trim(review_evidence)) > 0)
);
REVOKE ALL ON public_catalog_ingredient_links FROM PUBLIC;
