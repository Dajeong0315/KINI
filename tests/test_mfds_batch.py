import gzip
import json
import pytest
from app.services.data_import.mfds_batch import collect, fetch_page, normalize_food
from app.services.data_import.cloud_plan import prepare, literal
from app.services.data_import.storage_budget import admission, batches, policy


class Response:
    status_code=200
    def __init__(self,payload): self.payload=payload
    def json(self): return self.payload


class Session:
    def __init__(self,payloads): self.payloads=iter(payloads); self.calls=0
    def get(self,*args,**kwargs):
        self.calls+=1
        return Response(next(self.payloads))


def food(**updates):
    return {'FOOD_CD':'F1','FOOD_NM_KR':'두부','SERVING_SIZE':'100g',
            'AMT_NUM1':'76','AMT_NUM3':'8','AMT_NUM13':'',**updates}


def page(rows,total=2):
    return {'COOKRCP01':{'RESULT':{'CODE':'INFO-000'},'row':rows,'total_count':str(total)}}


def test_full_collection_checkpoint_and_resume_without_more_calls(tmp_path,monkeypatch):
    monkeypatch.setattr('app.services.data_import.mfds_batch.time.sleep',lambda value:None)
    session=Session([page([{'RCP_SEQ':'1'}]),page([{'RCP_SEQ':'2'}])])
    report=collect('mfds_recipe','fake',tmp_path,session=session,page_size=1)
    assert report['complete'] and report['rows']==2 and session.calls==2
    resumed=collect('mfds_recipe','fake',tmp_path,session=Session([]),page_size=1)
    assert resumed==report
    for path in (tmp_path/'mfds_recipe').glob('*.gz'):
        with gzip.open(path,'rt',encoding='utf-8') as handle:
            assert 'fake' not in json.dumps(json.load(handle))


def test_incomplete_collection_never_marks_complete(tmp_path,monkeypatch):
    monkeypatch.setattr('app.services.data_import.mfds_batch.time.sleep',lambda value:None)
    with pytest.raises(ValueError):
        collect('mfds_recipe','fake',tmp_path,session=Session([page([{'RCP_SEQ':'1'}])]),page_size=1,max_pages=1)
    assert not json.loads((tmp_path/'mfds_recipe/manifest.json').read_text())['complete']


def test_api_auth_errors_do_not_expose_key():
    session=Session([{'COOKRCP01':{'RESULT':{'CODE':'INFO-100'}}}])
    with pytest.raises(ValueError) as error:
        fetch_page(session,'mfds_recipe','sensitive-key',1,100)
    assert 'sensitive-key' not in str(error.value)


def test_nutrition_mapping_preserves_missing_and_basis():
    row=normalize_food(food())
    assert row['basis_amount']==100 and row['basis_unit']=='g'
    assert row['nutrients']['sodium_mg'] is None
    assert row['nutrients']['protein_g']==8
    assert normalize_food(food(SERVING_SIZE='250mL'))['basis_unit']=='ml'


@pytest.mark.parametrize('updates',[{'SERVING_SIZE':'100'},{'AMT_NUM3':'-1'},{'AMT_NUM3':'NaN'},{'FOOD_CD':''}])
def test_unknown_units_and_invalid_numbers_quarantined(updates):
    with pytest.raises(ValueError): normalize_food(food(**updates))


def test_capacity_admission_blocks_before_stop():
    assert admission(12000000,10000)['allow']
    assert not admission(399000000,1)['allow']
    assert admission(349000000,1)['status']=='warning'
    with pytest.raises(ValueError): admission(0,policy()['max_batch_payload_bytes']+1)
    assert [len(group) for group in batches([{'id':i} for i in range(201)])]==[100,100,1]
    assert not admission(250000000,1,catalog_bytes=199000000)['allow']
    assert sum(policy()['allocation_bytes'].values())==500000000
    assert policy()['allocation_bytes']['fridge_change_reserve']==150000000


def test_plan_only_compact_provenance_and_bounded_insert_batches(tmp_path):
    record=normalize_food(food(FOOD_NM_KR="두부'; DROP TABLE x; --"))
    plan=prepare([record],'mfds_nutrition','fixture',tmp_path)
    directory=tmp_path/plan['release_id']
    sql='\n'.join((directory/name).read_text(encoding='utf-8') for name in plan['batch_files'])
    assert "두부''; DROP TABLE x; --" in sql
    assert 'AMT_NUM1' not in sql and 'FOOD_NM_KR' not in sql
    assert plan['counts']['catalog_food_nutrients']==2
    assert plan['full_raw_in_cloud'] is False
    assert 'assert_catalog_capacity' in sql
    assert 'rights_status' in (directory/'publish.sql').read_text(encoding='utf-8')
    assert 'Incomplete catalog release' in (directory/'validate.sql').read_text(encoding='utf-8')


def test_sql_nulls_and_nonfinite_rejection():
    assert literal(None)=='NULL'
    assert literal("a'b")=="'a''b'"
    with pytest.raises(ValueError): literal(float('inf'))


def test_repeated_page_is_not_success(tmp_path,monkeypatch):
    monkeypatch.setattr('app.services.data_import.mfds_batch.time.sleep',lambda value:None)
    same=page([{'RCP_SEQ':'1'}])
    with pytest.raises(ValueError):
        collect('mfds_recipe','fake',tmp_path,session=Session([same,same]),page_size=1)
