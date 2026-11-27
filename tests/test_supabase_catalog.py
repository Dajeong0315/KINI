"""Synthetic public/private schemas only; never connects to a real project."""
from decimal import Decimal

import pytest

from app.services.recommendation_repository import open_db
from app.services.public_catalog import PublicRecordMissing
from app.services.integrations.cache import DisabledCache, MemoryCache
from app.services.supabase_catalog import SupabasePublicCatalog, _number
from app.services.recommendation_provider import RecommendationNutritionProvider, per_serving_nutrition

RELEASE = '10000000-0000-4000-8000-000000000001'


@pytest.fixture
def catalog_db(tmp_path, monkeypatch):
    monkeypatch.setenv('KINI_DB_BACKEND', 'sqlite')
    db = open_db(str(tmp_path / 'synthetic.sqlite3'))
    db.execute("ATTACH DATABASE ':memory:' AS public")
    db.executescript('''
        CREATE TABLE public.data_sources(code TEXT PRIMARY KEY,rights_status TEXT);
        CREATE TABLE public.data_releases(id TEXT PRIMARY KEY,source_code TEXT,status TEXT);
        CREATE TABLE public.active_data_releases(source_code TEXT,release_id TEXT,cache_version INTEGER);
        CREATE TABLE public.catalog_foods(release_id TEXT,external_id TEXT,name TEXT,basis_amount REAL,
            basis_unit TEXT,energy_kcal REAL,protein_g REAL,carbohydrate_g REAL,fat_g REAL,sodium_mg REAL,sugar_g REAL);
        CREATE TABLE public.catalog_recipes(release_id TEXT,external_id TEXT,name TEXT,servings REAL,
            cook_time_minutes INTEGER,ingredients_text TEXT);
        CREATE TABLE public.catalog_recipe_steps(release_id TEXT,recipe_id TEXT,step_no INTEGER,instruction TEXT);
        CREATE TABLE public.catalog_recipe_nutrients(release_id TEXT,recipe_id TEXT,nutrient TEXT,
            amount REAL,basis_amount REAL,basis_unit TEXT);
        CREATE TABLE public_catalog_ingredient_links(ingredient_id INTEGER PRIMARY KEY,release_id TEXT,
            food_id TEXT,expected_name TEXT,reviewed_at TEXT,review_evidence TEXT);
    ''')
    db.execute("INSERT INTO public.data_sources VALUES ('mfds_nutrition','approved')")
    db.execute("INSERT INTO public.data_releases VALUES (?,'mfds_nutrition','published')", (RELEASE,))
    db.execute("INSERT INTO public.active_data_releases VALUES ('mfds_nutrition',?,1)", (RELEASE,))
    db.execute("INSERT INTO public.catalog_foods VALUES (?,'SYNTHETIC-FOOD','synthetic raw vegetable',100,'g',40,2,8,0,20,NULL)", (RELEASE,))
    db.execute("INSERT INTO public_catalog_ingredient_links VALUES (3,?,'SYNTHETIC-FOOD','synthetic raw vegetable','2026-11-27','synthetic review')", (RELEASE,))
    db.commit()
    yield db, SupabasePublicCatalog(DisabledCache())
    db.close()


def test_reads_compact_values_without_private_catalog_copy(catalog_db):
    db, catalog = catalog_db
    statements = []
    db.set_trace_callback(statements.append)
    result = RecommendationNutritionProvider(catalog).get(db, 3, None)
    assert result.source == 'catalog_published' and result.release_id == RELEASE
    assert per_serving_nutrition([(result.values, 25)]) == dict(
        energy_kcal=10, protein_g=.5, carbohydrate_g=2, fat_g=0, sodium_mg=5)
    assert all(s.lstrip().upper().startswith('SELECT') for s in statements)
    assert db.execute('SELECT count(*) FROM catalog_foods').fetchone()[0] == 0
    assert _number(Decimal('0.78')) == .78
    assert _number(None) is None


@pytest.mark.parametrize('mutation', [
    "UPDATE public.data_sources SET rights_status='review_required'",
    "UPDATE public.data_releases SET status='staging'",
    "UPDATE public_catalog_ingredient_links SET reviewed_at=''",
    "UPDATE public_catalog_ingredient_links SET review_evidence=''",
    "DELETE FROM public_catalog_ingredient_links",
    "UPDATE public_catalog_ingredient_links SET expected_name='different product'",
    "UPDATE public_catalog_ingredient_links SET release_id='stale-release'",
    "UPDATE public.catalog_foods SET basis_unit='ml'",
    "UPDATE public.catalog_foods SET basis_amount=0",
    "UPDATE public.catalog_foods SET sodium_mg=NULL",
    "UPDATE public.catalog_foods SET protein_g=-1",
])
def test_cloud_rejects_invalid_or_missing_data_without_demo_success(catalog_db, mutation):
    db, catalog = catalog_db
    db.execute(mutation)
    db.commit()
    legacy = dict(energy_kcal=999, protein_g=10, carbohydrate_g=10, fat_g=10,
                  sodium_mg=10, basis_amount_g=100, nutrition_source='demo')
    assert RecommendationNutritionProvider(catalog).get(db, 3, legacy) is None


def test_release_changes_invalidate_cache_and_mapping(catalog_db):
    db, catalog = catalog_db
    catalog = SupabasePublicCatalog(MemoryCache())
    assert RecommendationNutritionProvider(catalog).get(db, 3, None)
    new = '10000000-0000-4000-8000-000000000002'
    db.execute("INSERT INTO public.data_releases VALUES (?,'mfds_nutrition','published')", (new,))
    db.execute('UPDATE public.active_data_releases SET release_id=?,cache_version=2', (new,))
    db.commit()
    assert RecommendationNutritionProvider(catalog).get(db, 3, None) is None
    assert catalog.context(db)[0:2] == [new, 2]


def test_recipe_unknown_servings_are_preserved(catalog_db):
    db, catalog = catalog_db
    db.execute("INSERT INTO public.data_sources VALUES ('mfds_recipe','approved')")
    recipe_release = '20000000-0000-4000-8000-000000000001'
    db.execute("INSERT INTO public.data_releases VALUES (?,'mfds_recipe','published')", (recipe_release,))
    db.execute("INSERT INTO public.active_data_releases VALUES ('mfds_recipe',?,1)", (recipe_release,))
    db.execute("INSERT INTO public.catalog_recipes VALUES (?,'SYNTHETIC-RECIPE','synthetic recipe',NULL,NULL,'synthetic vegetable 10g')", (recipe_release,))
    db.execute("INSERT INTO public.catalog_recipe_steps VALUES (?,'SYNTHETIC-RECIPE',1,'synthetic instruction')", (recipe_release,))
    db.execute("INSERT INTO public.catalog_recipe_nutrients VALUES (?,'SYNTHETIC-RECIPE','energy_kcal',25,100,'g')", (recipe_release,))
    page = catalog.recipe_page(db, 0)
    assert page['items'] == [{'external_id': 'SYNTHETIC-RECIPE', 'name': 'synthetic recipe', 'image_url': None}]
    result = catalog.read(db, 'mfds_recipe', 'recipe', 'SYNTHETIC-RECIPE').item
    assert result['servings'] is None and result['cook_time_minutes'] is None
    assert result['nutrition']['energy_kcal'] == dict(amount=25, basis_amount=100, basis_unit='g')
    with pytest.raises(PublicRecordMissing):
        catalog.read(db, 'mfds_recipe', 'recipe', 'MISSING')


def test_recommendation_path_uses_public_nutrition_and_keeps_safety(catalog_db, monkeypatch):
    db, catalog = catalog_db
    from app.services import recommendation_engine, recommendation_provider
    monkeypatch.setattr(recommendation_engine, 'RecommendationNutritionProvider', lambda: RecommendationNutritionProvider(catalog))
    monkeypatch.setattr(recommendation_provider, 'service', lambda: catalog)
    # A single-ingredient synthetic menu has a known serving count, unlike the
    # reference recipe with missing servings. Do not relabel it as that recipe.
    db.execute('DELETE FROM recipe_ingredients')
    db.execute('DELETE FROM recipes')
    db.execute("INSERT INTO recipes VALUES (90,'synthetic menu',1,5,0)")
    db.execute('INSERT INTO recipe_ingredients VALUES (90,3,25)')
    db.commit()
    request = dict(household_id=1, participant_ids=[1], meal_type='dinner', purchase_allowed=False)
    result = recommendation_engine.recommend(db, request, persist=False)
    candidate = result['recommendations'][0]
    assert candidate['nutrition_per_serving']['energy_kcal'] == 10
    assert candidate['inventory_usage'][0]['nutrition_source'] == 'catalog_published'
    assert result['_catalog_context'][0][0] == RELEASE
    assert candidate['inventory_usage'][0]['nutrition_food_id'] == 'SYNTHETIC-FOOD'
    assert candidate['inventory_usage'][0]['nutrition_release_id'] == RELEASE
    # Successful source reads never bypass the existing allergy exclusions.
    db.execute("INSERT INTO member_allergies VALUES (1,'당근')")
    result = recommendation_engine.recommend(db, request, persist=False)
    assert not result['recommendations']
    assert 'allergen_conflict' in result['excluded'][0]['reason_codes']


def test_mapping_change_invalidates_recommendation_context(catalog_db, monkeypatch):
    db, catalog = catalog_db
    from app.services import recommendation_provider
    monkeypatch.setattr(recommendation_provider, 'service', lambda: catalog)
    before = recommendation_provider.catalog_context(db)
    db.execute("UPDATE public_catalog_ingredient_links SET food_id='OTHER-FOOD'")
    assert recommendation_provider.catalog_context(db) != before


def test_read_permission_or_database_failure_is_not_fake_success(catalog_db):
    db, catalog = catalog_db
    import sqlite3
    db.execute('DROP TABLE public.data_sources')
    with pytest.raises(sqlite3.OperationalError):
        RecommendationNutritionProvider(catalog).get(db, 3, dict(nutrition_source='demo'))


def test_smoke_reports_only_ingredient_contribution(catalog_db):
    from scripts.check_public_catalog import inspect
    db, _ = catalog_db
    result = inspect(db, 'SYNTHETIC-FOOD', 25)
    assert result['ingredient_contribution']['energy_kcal'] == 10
    assert result['food']['release_id'] == RELEASE
    for grams in (0, -1, float('nan'), float('inf')):
        with pytest.raises(ValueError):
            inspect(db, 'SYNTHETIC-FOOD', grams)
