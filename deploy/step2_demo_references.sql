-- Minimal app-authored demonstration reference data. No public catalog writes.
BEGIN;
SET LOCAL lock_timeout='5s';
DO $guard$ BEGIN
 IF EXISTS (SELECT 1 FROM kini_private.ingredients) OR EXISTS (SELECT 1 FROM kini_private.recipes) THEN
  RAISE EXCEPTION 'Reference tables are not empty; inspect instead of overwriting';
 END IF;
 IF (SELECT count(*) FROM public.catalog_foods f JOIN public.active_data_releases a
      ON a.release_id=f.release_id JOIN public.data_releases r ON r.id=a.release_id
      JOIN public.data_sources s ON s.code=a.source_code
      WHERE a.source_code='mfds_nutrition' AND s.rights_status='approved' AND r.status='published'
      AND f.release_id='1d211108-0849-479d-9205-ac5060529431'
      AND f.basis_amount=100 AND f.basis_unit='g'
      AND (f.external_id,f.name) IN (('R101-008000301-0003','멥쌀, 백미, 생것'),
          ('R109-008000401-0000','닭고기, 가슴, 생것'),('R106-041007601-0000','당근, 뿌리, 생것'))) <> 3 THEN
  RAISE EXCEPTION 'Reviewed food references changed; re-review required';
 END IF;
END $guard$;
SET LOCAL ROLE kini_migrator;
SET LOCAL search_path=kini_private,pg_catalog;
INSERT INTO ingredients(id,name,canonical_name,category,source_type,data_confidence) VALUES
 (900001,'멥쌀, 백미, 생것','멥쌀, 백미, 생것','곡류','demo',1),
 (900002,'닭고기, 가슴, 생것','닭고기, 가슴, 생것','닭고기','demo',1),
 (900003,'당근, 뿌리, 생것','당근, 뿌리, 생것','채소류','demo',1);
-- Nutrition numbers are deliberately NOT copied; the provider reads public.
INSERT INTO ingredient_nutrition(ingredient_id,source_type,confidence) VALUES
 (900001,'demo',1),(900002,'demo',1),(900003,'demo',1);
INSERT INTO public_catalog_ingredient_links(ingredient_id,release_id,food_id,expected_name,reviewed_at,review_evidence) VALUES
 (900001,'1d211108-0849-479d-9205-ac5060529431','R101-008000301-0003','멥쌀, 백미, 생것','2026-11-27','Reviewed exact raw white-rice identity and 100g basis against live public catalog; synthetic demo input only'),
 (900002,'1d211108-0849-479d-9205-ac5060529431','R109-008000401-0000','닭고기, 가슴, 생것','2026-11-27','Reviewed exact raw chicken-breast part/state and 100g basis against live public catalog; synthetic demo input only'),
 (900003,'1d211108-0849-479d-9205-ac5060529431','R106-041007601-0000','당근, 뿌리, 생것','2026-11-27','Reviewed exact raw carrot-root identity and 100g basis against live public catalog; synthetic demo input only');
INSERT INTO recipes(id,name,servings,cook_time_minutes,spicy) VALUES
 (900001,'[시연] 닭가슴살 당근밥 · 원재료 기준',4,25,0);
INSERT INTO recipe_ingredients(recipe_id,ingredient_id,quantity_g) VALUES
 (900001,900001,300),(900001,900002,400),(900001,900003,200);
INSERT INTO metadata(key,value) VALUES ('step2_demo_reference_version','2026-11-27-v1'),
 ('step2_demo_recipe_scope','App-authored synthetic 4-serving recipe; raw-ingredient nutrient estimate; cooking retention and actual cook time not measured. Not an imported MFDS recipe.');
SELECT setval(pg_get_serial_sequence('kini_private.ingredients','id'),900003,true);
SELECT setval(pg_get_serial_sequence('kini_private.recipes','id'),900001,true);
COMMIT;
SELECT 'step2_demo_references_ready' AS status;
