"""Ingredient selection helpers on actual PostgreSQL and loopback HTTP."""
import os
from contextlib import closing
from pathlib import Path
import subprocess
import sys
from uuid import uuid4
import pytest

from test_recommendation_rules_postgres import rule_db
from test_recommendation_http_postgres import http
from test_cooking_postgres_http import stock_state

pytestmark=pytest.mark.skipif(os.getenv('KINI_DB_BACKEND')!='postgres',reason='requires disposable PostgreSQL')


@pytest.fixture
def catalog(rule_db):
    rule_db.execute("UPDATE ingredients SET name='합성 재료',canonical_name='합성 재료',source_type='verified_product' WHERE id=901")
    rule_db.commit()
    return rule_db


def normalize(api,auth,**fields):
    return api('POST','/api/inventory/normalize',{'name':'합성 재료','quantity':1,'unit':'g',**fields},auth)


def conversion(db,*,unit='개',factor=120,source='verified_product',confidence=.99,key=None):
    db.execute('''INSERT INTO unit_conversions(ingredient_id,unit,grams,from_unit,to_unit,factor,source_type,confidence)
        VALUES (901,?,?,?,'g',?,?,?)''',(key or unit,120,unit,factor,source,confidence)); db.commit()


@pytest.mark.parametrize('unit,quantity,amount,grams',[
    ('g',250,250,250),('kg',.5,500,500),('ml',100,100,None),('mL',100,100,None),
    ('L',.5,500,None),('l',.5,500,None),
])
def test_si_and_volume_does_not_assume_density(http,catalog,unit,quantity,amount,grams):
    api,login=http; auth=login()
    response=normalize(api,auth,unit=unit,quantity=quantity)
    assert response.status_code==200
    result=response.json()
    assert set(result)=={'ingredient','conversion','requires_confirmation'}
    assert result['ingredient']['ingredient_id']==901 and result['ingredient']['match_status']=='matched'
    assert result['conversion']['quantity']==amount and result['conversion']['quantity_g']==grams
    assert result['requires_confirmation']==(grams is None)
    assert response.headers['Cache-Control']=='no-store'


@pytest.mark.parametrize('unit',['개','컵','스푼','봉지','알 수 없는 단위'])
def test_no_package_weight_guessing(http,catalog,unit):
    api,login=http; result=normalize(api,login(),unit=unit).json()
    assert result['conversion']['quantity_g'] is None and result['requires_confirmation']


def test_verified_conversion_and_conflicting_factors_require_review(http,catalog):
    api,login=http; auth=login(); conversion(catalog)
    result=normalize(api,auth,unit='개',quantity=2).json()
    assert result['conversion']['quantity_g']==240 and result['requires_confirmation']
    conversion(catalog,key='alternate source',factor=150)
    result=normalize(api,auth,unit='개').json()
    assert result['conversion']['quantity_g'] is None and result['requires_confirmation']


@pytest.mark.parametrize('factor,source,confidence',[
    (120,'unknown',.99),(120,'demo',.99),(120,'verified_product',.94),
    (120,'verified_product',float('inf')),(120,'verified_product',2),
    (float('nan'),'verified_product',.99),(float('inf'),'verified_product',.99),
    (-120,'verified_product',.99),(1e308,'verified_product',.99),
])
def test_untrusted_or_nonfinite_conversion_not_used(http,catalog,factor,source,confidence):
    api,login=http; conversion(catalog,factor=factor,source=source,confidence=confidence)
    result=normalize(api,login(),unit='개',quantity=2).json()
    assert result['conversion']['quantity_g'] is None and result['requires_confirmation']


@pytest.mark.parametrize('mode,expected',[
    ('trusted','matched'),('untrusted','ambiguous'),('invalid_confidence','ambiguous'),
    ('multiple','ambiguous'),('composite','needs_label_check'),('inactive','unknown'),
    ('unknown','unknown'),('product','needs_label_check'),
])
def test_alias_ambiguity_product_and_unknown(http,catalog,mode,expected):
    api,login=http; name='합성 별칭'
    catalog.execute("INSERT INTO ingredient_aliases(ingredient_id,alias,normalized_alias,source_type,confidence) VALUES (901,?,?,'verified_product',.99)",(name,name))
    if mode=='untrusted': catalog.execute("UPDATE ingredient_aliases SET source_type='unknown'")
    if mode=='invalid_confidence': catalog.execute('UPDATE ingredient_aliases SET confidence=2')
    if mode=='multiple': catalog.execute("INSERT INTO ingredients(id,name,canonical_name,category,source_type) VALUES (902,'합성 별칭','다른 재료','other','verified_product')")
    if mode=='composite': catalog.execute('UPDATE ingredients SET is_composite=1 WHERE id=901')
    if mode=='inactive': catalog.execute('UPDATE ingredients SET active=FALSE WHERE id=901')
    if mode=='unknown': name='등록되지 않은 식재료'
    if mode=='product': name='등록되지 않은 제품 소스'
    conversion(catalog); catalog.commit()
    response=normalize(api,login(),name=name,unit='개')
    assert response.status_code==200
    result=response.json()
    assert result['ingredient']['match_status']==expected
    assert result['requires_confirmation']
    if expected in ('unknown','ambiguous'): assert result['conversion']['quantity_g'] is None


def test_search_is_literal_limited_active_and_read_only(http,catalog):
    api,login=http; auth=login()
    for i in range(40):
        catalog.execute("INSERT INTO ingredients(id,name,canonical_name,category) VALUES (?,?,?,'other')",(1000+i,f'search-{i:02}',f'search-{i:02}'))
    catalog.execute("UPDATE ingredients SET active=FALSE WHERE id=1000")
    catalog.execute("INSERT INTO ingredients(id,name,category) VALUES (1100,'fallback name','other')"); catalog.commit()
    before=stock_state(catalog)
    found=api('GET','/api/ingredients/search?q=search-',auth=auth)
    assert found.status_code==200 and found.headers['Cache-Control']=='no-store'
    rows=found.json()['ingredients']
    assert len(rows)==30 and rows[0]['id']==1001
    assert set(rows[0])=={'id','canonical_name','category','source_type','is_composite','requires_label_check'}
    assert api('GET','/api/ingredients/search?q=fallback',auth=auth).json()['ingredients'][0]['canonical_name']=='fallback name'
    for q in ['%','_',"' OR 1=1 --",'없는 재료']:
        assert api('GET','/api/ingredients/search',auth=auth).status_code==400
        assert api('GET','/api/ingredients/search?q='+q,auth=auth).json()['ingredients']==[]
    assert stock_state(catalog)==before


@pytest.mark.parametrize('path,method',[('/api/ingredients/search?q=합성','GET'),('/api/inventory/normalize','POST')])
def test_auth_and_viewer_read_without_mutation(http,catalog,path,method):
    api,login=http; body=None if method=='GET' else {'name':'합성 재료','quantity':1,'unit':'g'}
    assert api(method,path,body).status_code==401
    assert api(method,path,body,{'Authorization':'Bearer invalid-synthetic-token'}).status_code==401
    for actor in [login(),login('rec-viewer'),login('rec-other',2)]:
        assert api(method,path,body,actor).status_code==200


@pytest.mark.parametrize('fields',[{'quantity':0},{'quantity':-1},{'quantity':1000001},{'name':''},{'name':'x'*151},{'unit':''},{'unit':'x'*31}])
def test_bad_input_rejected(http,catalog,fields):
    api,login=http
    assert normalize(api,login(),**fields).status_code==400


def test_postgres_read_transaction_disallows_write(http,catalog,monkeypatch):
    from app.services import recommendation_api
    api,login=http; before=stock_state(catalog)
    def bad(db,text):
        db.execute('UPDATE inventory_items SET quantity_g=0'); return {}
    monkeypatch.setattr(recommendation_api,'normalize_ingredient',bad)
    assert normalize(api,login()).status_code==500
    assert stock_state(catalog)==before


def test_008_upgrade_preserves_unverified_legacy_rows():
    import psycopg
    from psycopg import sql
    from app.services.postgres_repository import PostgresConnection,hybrid_row
    from app.services.ingredient_normalization import normalize_ingredient
    from app.services.unit_conversion import convert_quantity
    from test_recommendation_rules_postgres import seed_family
    root=Path(__file__).resolve().parents[1]; schema='test_ingredient_upgrade_'+uuid4().hex
    url=os.environ['KINI_DATABASE_URL']
    with psycopg.connect(url,autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        try:
            with closing(PostgresConnection(psycopg.connect(url,options=f'-c search_path={schema}',row_factory=hybrid_row))) as db:
                for migration in sorted((root/'db/migrations').glob('*.sql')):
                    if migration.name>='008_': break
                    with db.cursor() as cursor: cursor.execute(migration.read_text(encoding='utf-8'))
                    db.execute('INSERT INTO schema_migrations(version) VALUES (?) ON CONFLICT DO NOTHING',(migration.stem,))
                db.commit(); seed_family(db)
                db.execute("INSERT INTO ingredient_aliases(ingredient_id,alias,normalized_alias) VALUES (901,'legacy alias','legacy alias')")
                db.execute("INSERT INTO unit_conversions(ingredient_id,unit,grams) VALUES (901,'개',120)"); db.commit()
                before=stock_state(db); db.commit()
                env={**os.environ,'PGOPTIONS':f'-c search_path={schema}'}
                for expected in ['008_ingredient_normalization','already up to date']:
                    result=subprocess.run([sys.executable,str(root/'scripts/run_migrations.py')],env=env,capture_output=True,text=True)
                    assert result.returncode==0 and expected in result.stdout
                assert stock_state(db)==before
                assert normalize_ingredient(db,'legacy alias')['match_status']=='ambiguous'
                assert convert_quantity(db,1,'개',901)['quantity_g'] is None
                assert db.execute("SELECT grams,source_type FROM unit_conversions WHERE ingredient_id=901").fetchone()=={'grams':120,'source_type':'unknown'}
        finally:
            admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
