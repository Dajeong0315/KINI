-- MCP migration: kini_nutrition_core_compact_v1.
-- Six common nutrients use nullable core columns; sparse legacy values stay readable.
ALTER TABLE public.catalog_foods
 ADD COLUMN energy_kcal numeric CHECK(energy_kcal>=0 AND energy_kcal<=1000000000000),
 ADD COLUMN protein_g numeric CHECK(protein_g>=0 AND protein_g<=1000000000000),
 ADD COLUMN carbohydrate_g numeric CHECK(carbohydrate_g>=0 AND carbohydrate_g<=1000000000000),
 ADD COLUMN fat_g numeric CHECK(fat_g>=0 AND fat_g<=1000000000000),
 ADD COLUMN sodium_mg numeric CHECK(sodium_mg>=0 AND sodium_mg<=1000000000000),
 ADD COLUMN sugar_g numeric CHECK(sugar_g>=0 AND sugar_g<=1000000000000);
CREATE VIEW public.catalog_food_nutrients_resolved WITH(security_invoker=true) AS
 SELECT f.release_id,f.external_id AS food_id,n.nutrient,n.amount
 FROM public.catalog_foods f CROSS JOIN LATERAL
 (VALUES ('energy_kcal'::text,f.energy_kcal),('protein_g',f.protein_g),
 ('carbohydrate_g',f.carbohydrate_g),('fat_g',f.fat_g),('sodium_mg',f.sodium_mg),('sugar_g',f.sugar_g)) n(nutrient,amount)
 WHERE n.amount IS NOT NULL
 UNION ALL
 SELECT e.release_id,e.food_id,e.nutrient,e.amount
 FROM public.catalog_food_nutrients e JOIN public.catalog_foods f
 ON f.release_id=e.release_id AND f.external_id=e.food_id
 WHERE CASE e.nutrient WHEN 'energy_kcal' THEN f.energy_kcal WHEN 'protein_g' THEN f.protein_g
 WHEN 'carbohydrate_g' THEN f.carbohydrate_g WHEN 'fat_g' THEN f.fat_g
 WHEN 'sodium_mg' THEN f.sodium_mg WHEN 'sugar_g' THEN f.sugar_g END IS NULL;
REVOKE ALL ON public.catalog_food_nutrients_resolved FROM PUBLIC,anon,authenticated;
GRANT SELECT ON public.catalog_food_nutrients_resolved TO service_role;
COMMENT ON VIEW public.catalog_food_nutrients_resolved IS 'Read nutrients without assuming sparse physical rows: compact six-core columns plus legacy sparse fallback. Null means unknown.';
COMMENT ON COLUMN public.catalog_foods.energy_kcal IS 'Core nutrient per basis_amount/basis_unit. Null is unknown, never automatically zero.';
