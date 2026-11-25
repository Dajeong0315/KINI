import json
from pathlib import Path

import pytest

from app.services.data_import.allergen_reference import import_allergen_reference
from app.services.data_import.mfds_nutrition import import_mfds
from app.services.data_import.publish import publish_allergen_version, publish_nutrition_release
from app.services.recommendation_engine import recommend
from app.services.recommendation_repository import open_db

FIXTURES=Path(__file__).parent/'fixtures'

@pytest.fixture
def db(tmp_path):
    connection=open_db(str(tmp_path/'import.sqlite3'))
    yield connection
    connection.close()

def raw(name): return (FIXTURES/name).read_bytes()
def request(): return {'household_id':1,'participant_ids':[1],'meal_type':'dinner','purchase_allowed':False}

def test_dry_run_has_no_release_and_validate_does_not_publish(db):
    dry=import_mfds(db,raw('mfds_nutrition_sample.json'),mode='dry_run')
    assert dry['accepted']==2 and dry['quarantined']==0
    assert db.execute('SELECT COUNT(*) FROM dataset_releases').fetchone()[0]==0
    staged=import_mfds(db,raw('mfds_nutrition_sample.json'),mode='import')
    assert db.execute('SELECT status FROM dataset_releases WHERE id=?',(staged['release_id'],)).fetchone()[0]=='validated'
    assert db.execute('SELECT nutrition_release_id FROM data_release_pointers WHERE id=1').fetchone()[0] is None

def test_publish_mapping_and_stale_result(db):
    staged=import_mfds(db,raw('mfds_nutrition_sample.json'),mode='import')
    item=db.execute("SELECT id FROM official_food_items WHERE release_id=? AND source_food_code='MFDS-001'",(staged['release_id'],)).fetchone()[0]
    db.execute("INSERT INTO ingredient_official_food_mappings(ingredient_id,official_food_item_id,mapping_method,mapping_confidence) VALUES (1,?,'manual',1)",(item,))
    before=recommend(db,request())
    assert before['nutrition_data_release_id'] is None
    publish_nutrition_release(db,staged['release_id'])
    after=recommend(db,request())
    assert after['nutrition_data_release_id']==staged['release_id']
    assert any(c['nutrition_confidence']=='official imported (explicit mapping)' for c in after['recommendations'])
    another=import_mfds(db,raw('mfds_nutrition_sample.json'),mode='import')
    publish_nutrition_release(db,another['release_id'])
    assert db.execute('SELECT status FROM dataset_releases WHERE id=?',(staged['release_id'],)).fetchone()[0]=='retired'
    publish_nutrition_release(db,staged['release_id'])
    assert db.execute('SELECT nutrition_release_id FROM data_release_pointers WHERE id=1').fetchone()[0]==staged['release_id']

def test_invalid_records_are_quarantined_without_pointer_change(db):
    bad={'records':[{'food_code':'BAD','food_name':'bad','basis_amount_g':100,'nutrients':{'energy_kcal':1,'carbohydrate_g':1,'protein_g':1,'fat_g':1,'sodium_mg':1},'nutrient_units':{'sodium_mg':'g'}},{'food_code':'','food_name':'missing','basis_amount_g':100,'nutrients':{}}]}
    result=import_mfds(db,json.dumps(bad).encode(),mode='import')
    assert result['accepted']==0 and result['quarantined']==2
    assert db.execute('SELECT nutrition_release_id FROM data_release_pointers WHERE id=1').fetchone()[0] is None
    assert db.execute('SELECT COUNT(*) FROM import_quarantine WHERE release_id=?',(result['release_id'],)).fetchone()[0]==2

def test_allergen_reference_requires_explicit_publish(db):
    result=import_allergen_reference(db,raw('allergen_reference_sample.json'))
    assert db.execute('SELECT allergen_reference_version_id FROM data_release_pointers WHERE id=1').fetchone()[0] is None
    publish_allergen_version(db,result['version_id'])
    assert db.execute('SELECT allergen_reference_version_id FROM data_release_pointers WHERE id=1').fetchone()[0]==result['version_id']

def test_allergen_fixture_rejects_missing_metadata(db):
    with pytest.raises(ValueError): import_allergen_reference(db,b'{"items": []}')
