-- MCP migration: kini_food_catalog_v1. Public reference data only; personal data stays in SQLite.
CREATE TABLE public.data_sources (
  code text PRIMARY KEY CHECK (length(code) BETWEEN 1 AND 80),
  name text NOT NULL CHECK (length(trim(name))>0),
  documentation_url text NOT NULL,
  rights_status text NOT NULL DEFAULT 'review_required' CHECK (rights_status IN ('review_required','approved')),
  rights_reference text, reviewed_at timestamptz,
  CHECK (rights_status <> 'approved' OR (rights_reference IS NOT NULL AND reviewed_at IS NOT NULL))
);
CREATE TABLE public.nutrient_definitions (
  code text PRIMARY KEY, name text NOT NULL, unit text NOT NULL CHECK(unit IN ('kcal','g','mg','ug'))
);
CREATE TABLE public.data_releases (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_code text NOT NULL REFERENCES public.data_sources(code),
  source_revision text NOT NULL CHECK(length(trim(source_revision))>0),
  status text NOT NULL DEFAULT 'staging' CHECK(status IN ('staging','validated','published','quarantined')),
  created_at timestamptz NOT NULL DEFAULT now(), published_at timestamptz,
  validation_report jsonb NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(validation_report)='object'),
  UNIQUE(id,source_code,status),
  CHECK(status <> 'published' OR published_at IS NOT NULL)
);
CREATE INDEX data_releases_source_created_idx ON public.data_releases(source_code,created_at DESC);
CREATE TABLE public.source_records (
  release_id uuid NOT NULL REFERENCES public.data_releases(id),
  external_id text NOT NULL CHECK(length(trim(external_id))>0),
  kind text NOT NULL CHECK(kind IN ('food','recipe')),
  payload_json jsonb NOT NULL CHECK(jsonb_typeof(payload_json)='object'),
  payload_sha256 text NOT NULL CHECK(payload_sha256 ~ '^[0-9a-f]{64}$'),
  PRIMARY KEY(release_id,external_id), UNIQUE(release_id,external_id,kind)
);
CREATE TABLE public.catalog_foods (
  release_id uuid NOT NULL, external_id text NOT NULL,
  kind text NOT NULL DEFAULT 'food' CHECK(kind='food'),
  name text NOT NULL CHECK(length(trim(name))>0), category text,
  basis_amount numeric NOT NULL CHECK(basis_amount>0 AND basis_amount<=1000000000),
  basis_unit text NOT NULL CHECK(basis_unit IN ('g','ml')),
  PRIMARY KEY(release_id,external_id),
  FOREIGN KEY(release_id,external_id,kind) REFERENCES public.source_records(release_id,external_id,kind)
);
CREATE INDEX catalog_foods_name_idx ON public.catalog_foods(name);
CREATE TABLE public.catalog_food_nutrients (
  release_id uuid NOT NULL, food_id text NOT NULL,
  nutrient text NOT NULL REFERENCES public.nutrient_definitions(code),
  amount numeric NOT NULL CHECK(amount>=0 AND amount<=1000000000000),
  PRIMARY KEY(release_id,food_id,nutrient),
  FOREIGN KEY(release_id,food_id) REFERENCES public.catalog_foods(release_id,external_id)
);
CREATE INDEX catalog_food_nutrients_code_idx ON public.catalog_food_nutrients(nutrient);
CREATE TABLE public.catalog_recipes (
  release_id uuid NOT NULL, external_id text NOT NULL,
  kind text NOT NULL DEFAULT 'recipe' CHECK(kind='recipe'),
  name text NOT NULL CHECK(length(trim(name))>0),
  servings numeric CHECK(servings>0 AND servings<=1000000),
  cook_time_minutes integer CHECK(cook_time_minutes>0),
  ingredients_text text NOT NULL CHECK(length(trim(ingredients_text))>0),
  method text, category text, image_url text, source_url text,
  PRIMARY KEY(release_id,external_id),
  FOREIGN KEY(release_id,external_id,kind) REFERENCES public.source_records(release_id,external_id,kind)
);
CREATE INDEX catalog_recipes_name_idx ON public.catalog_recipes(name);
CREATE TABLE public.catalog_recipe_steps (
  release_id uuid NOT NULL, recipe_id text NOT NULL,
  step_no integer NOT NULL CHECK(step_no>0), instruction text NOT NULL CHECK(length(trim(instruction))>0),
  image_url text, PRIMARY KEY(release_id,recipe_id,step_no),
  FOREIGN KEY(release_id,recipe_id) REFERENCES public.catalog_recipes(release_id,external_id)
);
CREATE TABLE public.catalog_recipe_ingredients (
  release_id uuid NOT NULL, recipe_id text NOT NULL,
  line_no integer NOT NULL CHECK(line_no>0), ingredient_name text NOT NULL CHECK(length(trim(ingredient_name))>0),
  original_text text NOT NULL, quantity numeric CHECK(quantity>0 AND quantity<=1000000000), unit text,
  mapping_status text NOT NULL DEFAULT 'unreviewed' CHECK(mapping_status IN ('unreviewed','reviewed')),
  PRIMARY KEY(release_id,recipe_id,line_no),
  FOREIGN KEY(release_id,recipe_id) REFERENCES public.catalog_recipes(release_id,external_id),
  CHECK((quantity IS NULL AND unit IS NULL) OR (quantity IS NOT NULL AND unit IS NOT NULL AND length(trim(unit))>0))
);
CREATE TABLE public.catalog_recipe_nutrients (
  release_id uuid NOT NULL, recipe_id text NOT NULL,
  nutrient text NOT NULL REFERENCES public.nutrient_definitions(code),
  amount numeric NOT NULL CHECK(amount>=0 AND amount<=1000000000000),
  basis_amount numeric NOT NULL CHECK(basis_amount>0 AND basis_amount<=1000000000),
  basis_unit text NOT NULL CHECK(basis_unit IN ('g','ml','serving')),
  PRIMARY KEY(release_id,recipe_id,nutrient),
  FOREIGN KEY(release_id,recipe_id) REFERENCES public.catalog_recipes(release_id,external_id)
);
CREATE INDEX catalog_recipe_nutrients_code_idx ON public.catalog_recipe_nutrients(nutrient);
CREATE TABLE public.active_data_releases (
  source_code text PRIMARY KEY REFERENCES public.data_sources(code),
  release_id uuid NOT NULL,
  release_status text NOT NULL DEFAULT 'published' CHECK(release_status='published'),
  cache_version bigint NOT NULL CHECK(cache_version>0),
  updated_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY(release_id,source_code,release_status) REFERENCES public.data_releases(id,source_code,status)
);
CREATE INDEX active_data_releases_release_idx ON public.active_data_releases(release_id,source_code,release_status);

COMMENT ON TABLE public.source_records IS 'Normalized snapshots of public/licensed food data only. No credentials, household, member, device, session or health data.';
COMMENT ON TABLE public.catalog_foods IS 'Food nutrition basis is explicit g/ml; missing values are absent nutrient rows, never assumed zero.';
COMMENT ON TABLE public.catalog_recipes IS 'Reference recipes; nullable serving/time means unknown, never inferred.';
COMMENT ON TABLE public.catalog_recipe_ingredients IS 'Public ingredient lines; reviewed does not certify allergen safety or map to a local household.';
COMMENT ON TABLE public.active_data_releases IS 'Per-source published release pointer. Writer increments cache_version on publish and rollback.';
DO $$
DECLARE table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'data_sources','nutrient_definitions','data_releases','source_records',
    'catalog_foods','catalog_food_nutrients','catalog_recipes','catalog_recipe_steps',
    'catalog_recipe_ingredients','catalog_recipe_nutrients','active_data_releases'
  ] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',table_name);
    EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC,anon,authenticated,service_role',table_name);
    EXECUTE format('GRANT SELECT,INSERT,UPDATE ON public.%I TO service_role',table_name);
    EXECUTE format('CREATE POLICY backend_catalog_access ON public.%I TO service_role USING (true) WITH CHECK (true)',table_name);
  END LOOP;
END $$;

INSERT INTO public.data_sources(code,name,documentation_url) VALUES
 ('mfds_recipe','식약처 COOKRCP01','https://www.foodsafetykorea.go.kr/api/newDatasetDetail.do?svc_no=COOKRCP01'),
 ('mfds_nutrition','식약처 식품영양성분DB','https://www.data.go.kr/data/15127578/openapi.do'),
 ('10000recipe','만개의레시피 제휴 데이터','https://www.10000recipe.com/company/intro.html');
INSERT INTO public.nutrient_definitions(code,name,unit) VALUES
 ('energy_kcal','열량','kcal'),('protein_g','단백질','g'),('carbohydrate_g','탄수화물','g'),
 ('fat_g','지방','g'),('sodium_mg','나트륨','mg'),('sugar_g','당류','g');
