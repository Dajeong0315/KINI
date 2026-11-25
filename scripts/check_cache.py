"""Exercise real cache + DB using synthetic records in an isolated temporary SQLite DB."""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from dotenv import load_dotenv

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.services.recommendation_repository import open_db
from app.services.catalog_repository import stage,validate,publish
from app.services.public_catalog import service


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend',choices=['redis','memory','disabled'],default='memory')
    args=parser.parse_args()
    load_dotenv(ROOT/'.env')
    os.environ['KINI_CACHE_BACKEND']=args.backend
    current=service()
    keys=[]
    try:
        with tempfile.TemporaryDirectory() as directory:
            db=open_db(str(Path(directory)/'cache-check.sqlite3'),seed_demo=False)
            try:
                db.execute("UPDATE catalog_data_sources SET rights_status='approved' WHERE code='mfds_nutrition'");db.commit()
                release=stage(db,'mfds_nutrition',[{'kind':'food','external_id':'synthetic-check','name':'합성 캐시 검증 자료',
                    'basis_amount':100,'basis_unit':'g','nutrients':{'energy_kcal':10}}],source_revision='synthetic-cache-check')
                validate(db,release);publish(db,release)
                from app.services.public_catalog import cache_key
                keys.append(cache_key('mfds_nutrition',release,1,'food','synthetic-check'))
                first=current.read(db,'mfds_nutrition','food','synthetic-check')
                second=current.read(db,'mfds_nutrition','food','synthetic-check')
                if args.backend!='disabled' and (first.cache_status!='miss' or second.cache_status!='hit'):
                    raise RuntimeError('캐시 실제 연결을 확인하지 못했습니다.')
                print(json.dumps({'data':'synthetic','backend':args.backend,'first':first.cache_status,
                    'second':second.cache_status,'source_database':'temporary_sqlite','production_data_modified':False},ensure_ascii=False))
            finally:db.close()
    finally:
        for key in keys:
            try:current.cache.delete(key)
            except Exception:pass


if __name__=='__main__':main()
