"""N1: actual PostgreSQL public reads; no private demo seeding or AI calls."""
import json
import os
from contextlib import closing
from uuid import uuid4

import pytest

from app.services.catalog_repository import stage, validate, publish, rollback
from app.services.integrations.cache import MemoryCache, CacheUnavailable
from app.services.public_catalog import PublicCatalog, PublicRecordMissing, cache_key
from app.services.recommendation_provider import RecommendationNutritionProvider, catalog_context, per_serving_nutrition
from app.services.recommendation_repository import open_db, recipe_candidates, recipe_ingredient_rows, ingredient_nutrition_row

pytestmark = pytest.mark.skipif(os.getenv('KINI_DB_BACKEND') != 'postgres', reason='requires disposable PostgreSQL')


def food(energy=100):
    return dict(external_id='F1', kind='food', name='synthetic public food', basis_amount=100, basis_unit='g',
                nutrients=dict(energy_kcal=energy, protein_g=10, carbohydrate_g=5, fat_g=0, sodium_mg=0))


def seed(db, release=None):
    db.execute("UPDATE catalog_data_sources SET rights_status='approved'")
    db.execute("INSERT INTO ingredients(id,name,canonical_name,category,source_type,data_confidence) VALUES (901,'synthetic','synthetic','protein','demo',1)")
    db.execute("INSERT INTO ingredient_nutrition(ingredient_id,energy_kcal,protein_g,carbohydrate_g,fat_g,sodium_mg,basis_amount_g,source_type,confidence) VALUES (901,50,5,2,0,0,100,'demo',1)")
    db.execute("INSERT INTO recipes(id,name,servings,cook_time_minutes,spicy) VALUES (501,'synthetic recipe',2,15,0)")
    db.execute('INSERT INTO recipe_ingredients VALUES (501,901,200)')
    db.commit()
    rid = stage(db, 'mfds_nutrition', [food()], source_revision='synthetic', release_id=release)
    assert validate(db, rid) == []
    publish(db, rid)
    db.execute('INSERT INTO catalog_ingredient_links VALUES (901,?,?,?)', (rid, 'F1', '2026-11-26'))
    db.commit()
    return rid


@pytest.fixture
def db():
    with closing(open_db()) as db:
        db.execute('TRUNCATE active_data_releases, meal_data_context, catalog_data_sources, ingredients, recipes CASCADE')
        db.executemany('INSERT INTO catalog_data_sources VALUES (?,?,?,?)', [
            (s, s, 'https://example.invalid', 'review_required') for s in ('mfds_nutrition', 'mfds_recipe', '10000recipe')])
        db.commit()
        yield db


def test_public_nutrition_recipe_units_and_sqlite_parity(db, tmp_path, monkeypatch):
    release = seed(db)
    provider = RecommendationNutritionProvider(PublicCatalog())
    row = ingredient_nutrition_row(db, 901)
    result = provider.get(db, 901, row)
    assert result.source == 'catalog_published' and result.release_id == release
    assert result.values['fat_g'] == 0 and result.values['basis_amount_g'] == 100
    assert per_serving_nutrition([(result.values, 100)])['energy_kcal'] == 100
    expected = (recipe_candidates(db), recipe_ingredient_rows(db, 501), result.values, catalog_context(db))
    assert expected[0][0]['servings'] == 2 and expected[1][0]['quantity_g'] == 200
    monkeypatch.setenv('KINI_DB_BACKEND', 'sqlite')
    with closing(open_db(str(tmp_path / 'parity.sqlite3'), seed_demo=False)) as sqlite:
        seed(sqlite, release)
        actual = (recipe_candidates(sqlite), recipe_ingredient_rows(sqlite, 501),
                  provider.get(sqlite, 901, ingredient_nutrition_row(sqlite, 901)).values, catalog_context(sqlite))
        assert actual == expected


@pytest.mark.parametrize('mode', ['unapproved', 'staging', 'quarantined', 'unmapped', 'unreviewed'])
def test_unapproved_or_unreviewed_catalog_is_not_used(db, mode):
    release = seed(db)
    if mode == 'unapproved': db.execute("UPDATE catalog_data_sources SET rights_status='review_required'")
    elif mode in ('staging', 'quarantined'): db.execute('UPDATE data_releases SET status=?', (mode,))
    elif mode == 'unmapped': db.execute('DELETE FROM catalog_ingredient_links')
    else: db.execute("UPDATE catalog_ingredient_links SET reviewed_at=''")
    db.commit()
    provider = RecommendationNutritionProvider(PublicCatalog())
    assert provider.get(db, 901, None) is None
    assert provider.get(db, 901, ingredient_nutrition_row(db, 901)).source == 'demo'


@pytest.mark.parametrize('field,bad', [('energy_kcal', x) for x in (None, -1, float('nan'), float('inf'), True)] +
                         [('basis_amount_g', x) for x in (None, 0, -1, float('nan'), float('inf'), True)])
def test_invalid_legacy_nutrition_is_excluded(db, field, bad):
    seed(db)
    db.execute('DELETE FROM catalog_ingredient_links'); db.commit()
    row = dict(ingredient_nutrition_row(db, 901)); row[field] = bad
    assert RecommendationNutritionProvider().get(db, 901, row) is None


def test_cache_failure_release_swap_rollback_and_approval_revocation(db):
    old = seed(db)
    catalog = PublicCatalog(jitter=lambda: 1)
    first = catalog.read(db, 'mfds_nutrition', 'food', 'F1')
    assert first.cache_status == 'miss'
    assert catalog.read(db, 'mfds_nutrition', 'food', 'F1').cache_status == 'hit'
    context = catalog_context(db)
    assert context == [old, 1, 'approved', 'published']
    new = stage(db, 'mfds_nutrition', [food(200)], source_revision='next')
    assert not validate(db, new); publish(db, new)
    assert catalog_context(db) != context
    assert catalog.read(db, 'mfds_nutrition', 'food', 'F1').item['nutrients']['energy_kcal'] == 200
    rollback(db, 'mfds_nutrition', old)
    restored = catalog.read(db, 'mfds_nutrition', 'food', 'F1')
    assert restored.cache_status == 'miss' and restored.item['cache_version'] == 3
    assert restored.item['nutrients']['energy_kcal'] == 100
    class Broken:
        backend = 'redis'
        def get(self, key): raise CacheUnavailable()
    assert PublicCatalog(Broken()).read(db, 'mfds_nutrition', 'food', 'F1').cache_status == 'unavailable'
    db.execute("UPDATE catalog_data_sources SET rights_status='review_required'"); db.commit()
    with pytest.raises(PublicRecordMissing): catalog.read(db, 'mfds_nutrition', 'food', 'F1')


def test_public_recipe_steps_and_poisoned_cache_private_fields(db):
    seed(db)
    recipe = dict(kind='recipe', external_id='R1', name='public recipe', servings=2,
                  cook_time_minutes=15, ingredients_text='synthetic 200g', steps=['one', 'two'])
    release = stage(db, 'mfds_recipe', [recipe], source_revision='fixture')
    assert not validate(db, release); publish(db, release)
    memory = MemoryCache(); catalog = PublicCatalog(memory)
    item = catalog.read(db, 'mfds_recipe', 'recipe', 'R1').item
    assert item['steps'] == ['one', 'two'] and item['servings'] == 2
    key = cache_key('mfds_recipe', release, 1, 'recipe', 'R1')
    memory.set(key, json.dumps({**item, 'member_id': 987, 'allergies': ['private']}).encode(), 100)
    restored = catalog.read(db, 'mfds_recipe', 'recipe', 'R1').item
    assert restored == item and 'member_id' not in json.loads(memory.get(key))
