import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
import pytest
from app.services.recommendation_repository import open_db,connect
from app.services.catalog_repository import stage,validate,publish,rollback
from app.services.public_catalog import PublicCatalog,PublicRecordMissing,cache_key,service,_service
from app.services.integrations.cache import MemoryCache,CacheUnavailable


def food(energy=76):
    return {'external_id':'F1','kind':'food','name':'두부','basis_amount':100,'basis_unit':'g',
            'nutrients':{'energy_kcal':energy,'sodium_mg':None}}


@pytest.fixture
def db(tmp_path):
    db=open_db(str(tmp_path/'cache.sqlite3'),seed_demo=False)
    db.execute("UPDATE catalog_data_sources SET rights_status='approved' WHERE code='mfds_nutrition'");db.commit()
    release=stage(db,'mfds_nutrition',[food()],source_revision='fixture')
    validate(db,release);publish(db,release)
    yield db
    db.close()


def test_second_read_avoids_catalog_select_but_still_checks_live_pointer(db):
    current=PublicCatalog(jitter=lambda:1)
    first=current.read(db,'mfds_nutrition','food','F1')
    statements=[];db.set_trace_callback(statements.append)
    second=current.read(db,'mfds_nutrition','food','F1')
    assert first.cache_status=='miss' and second.cache_status=='hit'
    assert first.item==second.item
    assert any('active_data_releases' in s for s in statements)
    assert not any('SELECT * FROM catalog_foods' in s for s in statements)
    assert second.item['nutrients']=={'energy_kcal':76}


def test_ttl_expiry_and_memory_eviction():
    now=[0]
    memory=MemoryCache(max_bytes=20,max_entries=1,clock=lambda:now[0])
    memory.set('a',b'1',2);assert memory.get('a')==b'1'
    now[0]=2;assert memory.get('a') is None
    memory.set('a',b'1',2);memory.set('b',b'2',2)
    assert memory.get('a') is None and memory.get('b')==b'2'
    memory.set('c',b'x'*30,2);assert memory.get('c') is None


def test_publish_and_rollback_use_new_cache_namespace(db):
    current=PublicCatalog(jitter=lambda:1)
    original=current.read(db,'mfds_nutrition','food','F1')
    release=stage(db,'mfds_nutrition',[food(100)],source_revision='next')
    validate(db,release);publish(db,release)
    newer=current.read(db,'mfds_nutrition','food','F1')
    assert newer.cache_status=='miss' and newer.item['nutrients']['energy_kcal']==100
    rollback(db,'mfds_nutrition',original.item['release_id'])
    restored=current.read(db,'mfds_nutrition','food','F1')
    assert restored.cache_status=='miss' and restored.item['cache_version']==3
    assert restored.item['nutrients']['energy_kcal']==76


def test_rights_revocation_blocks_cached_value(db):
    current=PublicCatalog();current.read(db,'mfds_nutrition','food','F1')
    db.execute("UPDATE catalog_data_sources SET rights_status='review_required'");db.commit()
    with pytest.raises(PublicRecordMissing):current.read(db,'mfds_nutrition','food','F1')


def test_failed_cache_reads_and_writes_fall_back_to_db(db):
    class Broken:
        backend='redis'
        def get(self,key):raise CacheUnavailable()
    result=PublicCatalog(Broken()).read(db,'mfds_nutrition','food','F1')
    assert result.cache_status=='unavailable' and result.item['name']=='두부'
    class BrokenWrite:
        backend='redis'
        def get(self,key):return None
        def set(self,*args):raise CacheUnavailable()
    assert PublicCatalog(BrokenWrite()).read(db,'mfds_nutrition','food','F1').cache_status=='unavailable'


def test_cache_cannot_hide_source_database_error(db):
    current=PublicCatalog();current.read(db,'mfds_nutrition','food','F1')
    db.close()
    with pytest.raises(sqlite3.ProgrammingError):current.read(db,'mfds_nutrition','food','F1')


@pytest.mark.parametrize('private_change',[{'member_id':3},{'allergies':['새우']},{'raw_query':'개인 질문'}])
def test_poisoned_cache_private_fields_never_returned(db,private_change):
    memory=MemoryCache();current=PublicCatalog(memory)
    value=current.read(db,'mfds_nutrition','food','F1').item
    key=cache_key('mfds_nutrition',value['release_id'],1,'food','F1')
    memory.set(key,json.dumps({**value,**private_change},ensure_ascii=False).encode(),100)
    result=current.read(db,'mfds_nutrition','food','F1')
    assert not set(private_change)&set(result.item)
    assert current.read(db,'mfds_nutrition','food','F1').cache_status=='hit'


def test_same_key_concurrent_reads_are_coalesced(db):
    path=db.execute('PRAGMA database_list').fetchone()['file']
    class SlowCatalog(PublicCatalog):
        calls=0
        def _load(self,*args):
            self.calls+=1;time.sleep(.03)
            return super()._load(*args)
    current=SlowCatalog()
    def read(_):
        connection=connect(path)
        try:return current.read(connection,'mfds_nutrition','food','F1').cache_status
        finally:connection.close()
    with ThreadPoolExecutor(max_workers=5) as pool:results=list(pool.map(read,range(5)))
    assert current.calls==1 and results.count('hit')==4


def test_new_routes_reject_personal_filters_and_return_public_records(db,monkeypatch):
    monkeypatch.setenv('KINI_DB_PATH',db.execute('PRAGMA database_list').fetchone()['file'])
    monkeypatch.setenv('KINI_CACHE_BACKEND','memory');_service.cache_clear()
    from app.main import app
    client=app.test_client()
    assert client.get('/api/data/catalog/foods/F1?member_id=3').status_code==400
    first=client.get('/api/data/catalog/foods/F1');second=client.get('/api/data/catalog/foods/F1')
    assert first.status_code==200 and first.json['cache']['status']=='miss'
    assert second.json['cache']['status']=='hit'
    assert client.get('/api/data/catalog/foods/unknown').status_code==404
    assert client.get('/api/data/cache/status').status_code==401
    summary=service().stats.summary()
    assert 'raw_queries_recorded' in summary and 'keys' not in summary


def test_recipe_uses_public_typed_fields_only(db):
    db.execute("UPDATE catalog_data_sources SET rights_status='approved' WHERE code='mfds_recipe'");db.commit()
    release=stage(db,'mfds_recipe',[{'external_id':'R1','kind':'recipe','name':'두부찜',
        'ingredients_text':'두부 100g','steps':['찐다.']}],source_revision='fixture')
    validate(db,release);publish(db,release)
    current=PublicCatalog()
    result=current.read(db,'mfds_recipe','recipe','R1')
    assert result.item['servings'] is None and result.item['steps']==['찐다.']
    assert current.read(db,'mfds_recipe','recipe','R1').cache_status=='hit'


def test_legacy_a_database_renames_sources_without_losing_reference(tmp_path):
    db=connect(str(tmp_path/'legacy.sqlite3'))
    db.execute('CREATE TABLE data_sources(code TEXT PRIMARY KEY,name TEXT NOT NULL,documentation_url TEXT NOT NULL,rights_status TEXT NOT NULL)')
    db.execute("INSERT INTO data_sources VALUES('mfds_nutrition','legacy','https://example.test','approved')")
    db.execute('CREATE TABLE legacy_source_reference(code TEXT REFERENCES data_sources(code))')
    db.execute("INSERT INTO legacy_source_reference VALUES('mfds_nutrition')")
    db.commit();db.close()
    db=open_db(str(tmp_path/'legacy.sqlite3'),seed_demo=False)
    try:
        assert 'source_key' in {r['name'] for r in db.execute('PRAGMA table_info(data_sources)')}
        assert db.execute("SELECT name FROM catalog_data_sources WHERE code='mfds_nutrition'").fetchone()[0]=='legacy'
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
    finally:db.close()
