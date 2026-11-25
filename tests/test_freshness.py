from datetime import date
from app.services.freshness import assess_inventory
from app.services.recommendation_repository import open_db

def test_expiry_and_missing_information():
    item={'quantity_g':100,'freshness_state':'good','received_at':'2026-01-01','use_by':'2026-01-11'}
    assert assess_inventory(item,date(2026,1,10))['priority']==1
    assert not assess_inventory(item,date(2026,1,12))['usable']
    item['use_by']=None
    assert '소비기한 미입력' in assess_inventory(item,date(2026,1,10))['flags']
    item['freshness_state']='unsafe'
    assert not assess_inventory(item)['usable']

def test_existing_inventory_columns_are_migrated(tmp_path):
    db=open_db(str(tmp_path/'migrate.db'))
    columns={r['name'] for r in db.execute('PRAGMA table_info(inventory_items)')}
    assert {'received_at','opened_at','storage_location','package_state'} <= columns
    db.close()

def test_guideline_and_product_earliest():
    item={'quantity_g':100,'freshness_state':'good','opened_at':'2026-01-01','use_by':'2026-01-20','storage_location':'refrigerator'}
    guideline={'opened_days':5,'recommended_storage':'refrigerator'}
    result=assess_inventory(item,date(2026,1,7),guideline)
    assert not result['usable'] and result['recommended_by']=='2026-01-06'
