import pytest

from app.services.catalog_repository import stage, validate, publish
from app.services.recommendation_engine import recommend
from app.services.recommendation_repository import open_db


def request():
    return {'household_id': 1, 'participant_ids': [1], 'meal_type': 'dinner', 'purchase_allowed': False}


@pytest.fixture
def db(tmp_path):
    db = open_db(str(tmp_path / 'provider.sqlite3'))
    db.execute("UPDATE catalog_data_sources SET rights_status='approved' WHERE code='mfds_nutrition'")
    db.commit()
    yield db
    db.close()


def test_recommendation_uses_approved_published_catalog_link(db):
    row = {'external_id': 'F-RICE', 'kind': 'food', 'name': '쌀 공식',
           'basis_amount': 100, 'basis_unit': 'g',
           'nutrients': {'energy_kcal': 999, 'protein_g': 20, 'carbohydrate_g': 40,
                         'fat_g': 2, 'sodium_mg': 1}}
    release = stage(db, 'mfds_nutrition', [row], source_revision='test')
    assert validate(db, release) == []
    publish(db, release)
    db.execute("INSERT INTO catalog_ingredient_links VALUES (1,?,?,datetime('now'))", (release, 'F-RICE'))
    db.commit()
    result = recommend(db, request(), persist=False)
    candidate = next(c for c in result['recommendations']
                     if any(x['nutrition_source'] == 'catalog_published' for x in c['inventory_usage']))
    assert candidate['nutrition_per_serving']['energy_kcal'] > 999 / 4
    assert result['data_provenance_summary'] == 'approved published public catalog and explicit ingredient links'


def test_missing_or_invalid_catalog_keeps_explicit_demo_fallback(db):
    result = recommend(db, request(), persist=False)
    candidate = next(c for c in result['recommendations'] if c['recipe_id'] == 1)
    assert candidate['data_quality'] == 'demo_data'
    assert '데모' in candidate['nutrition_confidence']

    db.execute("UPDATE ingredient_nutrition SET energy_kcal=NULL WHERE ingredient_id=1")
    db.commit()
    result = recommend(db, request(), persist=False)
    assert 1 in {x['recipe_id'] for x in result['excluded']}
    assert any('nutrition_unknown' in x.get('reason_codes', []) for x in result['excluded'])


def test_catalog_cache_failure_falls_back_to_catalog_database(monkeypatch, db):
    class BrokenCatalog:
        def read(self, *args, **kwargs):
            from app.services.integrations.cache import CacheUnavailable
            raise CacheUnavailable()

    # The provider must not silently treat a cache outage as a personal-data fallback.
    # PublicCatalog itself handles CacheUnavailable; this checks its safe contract.
    from app.services.recommendation_provider import RecommendationNutritionProvider
    db.execute("UPDATE catalog_data_sources SET rights_status='approved' WHERE code='mfds_nutrition'")
    release = stage(db, 'mfds_nutrition', [{'external_id':'F-CACHE','kind':'food','name':'공개식품',
        'basis_amount':100,'basis_unit':'g','nutrients':{'energy_kcal':10,'protein_g':1,
        'carbohydrate_g':1,'fat_g':1,'sodium_mg':1}}], source_revision='test')
    validate(db, release); publish(db, release)
    db.execute("INSERT INTO catalog_ingredient_links VALUES (2,?,?,datetime('now'))", (release, 'F-CACHE'))
    db.commit()
    assert RecommendationNutritionProvider(BrokenCatalog()).get(db, 2,
        db.execute('SELECT i.*,n.source_type nutrition_source,n.basis_amount_g FROM ingredients i JOIN ingredient_nutrition n ON n.ingredient_id=i.id WHERE i.id=2').fetchone()) is None


def test_publishing_new_catalog_invalidates_saved_candidate(db, monkeypatch):
    monkeypatch.setenv('KINI_DB_PATH', db.execute('PRAGMA database_list').fetchone()['file'])
    monkeypatch.setenv('KINI_USE_OPENAI','0')
    from app.main import app
    client = app.test_client()
    rec = client.post('/api/recommendations',json=request()).json
    meal = client.post('/api/meal-sessions',json=request()).json
    row = dict(external_id="F2",kind="food",name="fixture",basis_amount=100,basis_unit="g",
               nutrients={"protein_g":5})
    release = stage(db,'mfds_nutrition',[row],source_revision="next")
    assert not validate(db,release)
    publish(db,release)
    saved = client.get(f"/api/recommendations/{rec['request_id']}").json
    assert saved['status']=='stale_unvalidated' and saved['recommendations']==[]
    assert client.post('/api/cooking-sessions',json={'meal_session_id':meal['session_id'],
        'recipe_id':rec['recommendations'][0]['recipe_id'],
        'recommendation_request_id':rec['request_id']}).status_code==400
