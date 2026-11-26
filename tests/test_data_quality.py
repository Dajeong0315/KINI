import json
import pytest
from app.services.recommendation_repository import open_db
from app.services.ingredient_normalization import normalize_ingredient
from app.services.unit_conversion import convert_quantity
from app.services.recommendation_engine import recommend

@pytest.fixture
def db(tmp_path):
    db=open_db(str(tmp_path/'test.sqlite3'))
    yield db
    db.close()

def req(ids): return {'household_id':1,'participant_ids':ids,'meal_type':'dinner','purchase_allowed':False}

def test_alias_ambiguity_and_product(db):
    db.execute("INSERT INTO ingredients(id,name,canonical_name,category,source_type,data_confidence) VALUES (10,'양파','양파','vegetable','demo',.5)")
    db.execute("INSERT INTO ingredient_aliases(ingredient_id,alias,normalized_alias,source_type,confidence) VALUES (10,'깐 양파','깐 양파','demo',.95)")
    assert normalize_ingredient(db,' 양파 1개 ')['ingredient_id']==10
    assert normalize_ingredient(db,'양파(국산) 1개')['ingredient_id']==10
    assert normalize_ingredient(db,'깐 양파')['match_status']=='matched'
    db.execute("INSERT INTO ingredients(id,name,canonical_name,category,source_type) VALUES (11,'깐 양파','깐 양파','vegetable','demo')")
    assert normalize_ingredient(db,'깐 양파')['match_status']=='ambiguous'
    assert normalize_ingredient(db,'브랜드 양파 소스')['match_status']=='needs_label_check'
    assert normalize_ingredient(db,'간장')['match_status']=='needs_label_check'

@pytest.mark.parametrize('unit',['개','컵','스푼','반 통','한 봉지'])
def test_unknown_weight_not_converted(db,unit):
    assert convert_quantity(db,1,unit,1)['quantity_g'] is None

@pytest.mark.parametrize('unit,qty,target,amount',[('g',1,'g',1),('kg',2,'g',2000),('ml',10,'ml',10),('L',2,'ml',2000)])
def test_si(db,unit,qty,target,amount):
    result=convert_quantity(db,qty,unit)
    assert (result['unit'],result['quantity'])==(target,amount)
    if target=='ml': assert result['quantity_g'] is None

def test_allergen_tag_label_and_unknown_nutrition(db):
    db.execute("INSERT INTO ingredient_allergen_tags VALUES (4,'새우','user_entered',1,0)")
    result=recommend(db,req([3]))
    assert 'allergen_conflict' in next(c for c in result['excluded'] if c['recipe_id']==2)['reason_codes']
    db.execute("DELETE FROM ingredient_allergen_tags WHERE ingredient_id=4")
    db.execute('UPDATE ingredients SET is_composite=1 WHERE id=4')
    result=recommend(db,req([3]))
    assert 'label_confirmation_required' in next(c for c in result['excluded'] if c['recipe_id']==2)['reason_codes']
    db.execute('UPDATE ingredient_nutrition SET protein_g=NULL')
    result=recommend(db,req([1]))
    assert result['status']=='no_safe_recommendation' and result['recommendations']==[]
    assert all('nutrition_unknown' in c['reason_codes'] for c in result['excluded'])

def test_alias_allergen_and_missing_quantity(db):
    db.execute("UPDATE member_allergies SET ingredient='새우별칭' WHERE member_id=3 AND ingredient='새우'")
    db.execute("INSERT INTO allergen_aliases VALUES ('새우별칭','새우','official_imported',1)")
    db.execute('UPDATE recipe_ingredients SET quantity_g=NULL WHERE recipe_id=1 AND ingredient_id=1')
    result=recommend(db,req([3]))
    assert 'allergen_conflict' in next(x for x in result['excluded'] if x['recipe_id']==3)['reason_codes']
    assert 'unit_confirmation_required' in next(x for x in result['excluded'] if x['recipe_id']==1)['reason_codes']

def test_ingredient_specific_conversion_requires_evidence(db):
    db.execute("INSERT INTO unit_conversions(ingredient_id,from_unit,to_unit,factor,confidence,source_type) VALUES (1,'컵','g',200,.99,'unknown')")
    assert convert_quantity(db,1,'컵',1)['quantity_g'] is None
    db.execute("UPDATE unit_conversions SET source_type='verified_product'")
    assert convert_quantity(db,1,'컵',1)['quantity_g']==200
    assert convert_quantity(db,1,'컵',2)['quantity_g'] is None

def test_inventory_api(monkeypatch,tmp_path):
    monkeypatch.setenv('KINI_DB_PATH',str(tmp_path/'api.sqlite3'))
    from app.main import app
    client=app.test_client()
    result=client.post('/api/inventory/normalize',json={'name':'당근 1개','quantity':1,'unit':'개'}).json
    assert result['ingredient']['canonical_name']=='당근' and result['requires_confirmation']
    assert client.post('/api/inventory/items',json={'household_id':1,'ingredient_id':3,'name':'당근','quantity':1,'unit':'개'}).status_code==401
    from app.services.local_identity import create_account, login
    from app.services.recommendation_repository import open_db
    with open_db() as db:
        create_account(db, 'inventory-test-owner', 'inventory-test-password', 1)
        token=login(db, 'inventory-test-owner', 'inventory-test-password', 1)
    added=client.post('/api/inventory/items',headers={'Authorization':f'Bearer {token}'},json={'household_id':1,'ingredient_id':3,'name':'당근','quantity':1,'unit':'kg'})
    assert added.status_code==201 and added.json['conversion']['quantity_g']==1000
    assert client.get('/api/ingredients/search?q=당근').json['ingredients'][0]['id']==3

def test_old_nutrition_basis_is_not_invented(tmp_path):
    import sqlite3
    path=str(tmp_path/'legacy.sqlite3')
    old=sqlite3.connect(path)
    old.executescript("CREATE TABLE households(id INTEGER PRIMARY KEY,name TEXT); INSERT INTO households VALUES (1,'old'); CREATE TABLE ingredients(id INTEGER PRIMARY KEY,name TEXT UNIQUE,category TEXT); INSERT INTO ingredients VALUES (1,'old','other'); CREATE TABLE ingredient_nutrition(ingredient_id INTEGER PRIMARY KEY,energy_kcal REAL,protein_g REAL,carbohydrate_g REAL,fat_g REAL,sodium_mg REAL); INSERT INTO ingredient_nutrition VALUES (1,100,1,2,3,4);")
    old.close()
    db=open_db(path)
    assert db.execute('SELECT basis_amount_g FROM ingredient_nutrition WHERE ingredient_id=1').fetchone()[0] is None
    db.close()
