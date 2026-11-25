from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from .validation import NUTRIENTS

MFDS_SOURCE = {'source_key':'mfds_food_nutrition', 'provider_name':'식품의약품안전처', 'dataset_name':'식품영양성분DB정보', 'source_url':'https://apis.data.go.kr/1471000/FoodNtrCpntDbInfo03', 'expected_format':'json'}

def now() -> str: return datetime.now(timezone.utc).isoformat()

def ensure_source(db, source: dict) -> int:
    db.execute('INSERT OR IGNORE INTO data_sources(source_key,provider_name,dataset_name,source_url,expected_format) VALUES (?,?,?,?,?)', (source['source_key'],source['provider_name'],source['dataset_name'],source['source_url'],source['expected_format']))
    return db.execute('SELECT id FROM data_sources WHERE source_key=?',(source['source_key'],)).fetchone()[0]

def create_release(db, source_id: int, raw: bytes, external_release_id=None, source_published_at=None) -> int:
    checksum=hashlib.sha256(raw).hexdigest()
    cur=db.execute("INSERT INTO dataset_releases(data_source_id,external_release_id,source_published_at,checksum,status) VALUES (?,?,?,?, 'staging')",(source_id,external_release_id,source_published_at,checksum))
    return cur.lastrowid

def quarantine(db, release_id: int, record_key: str, code: str, detail: str):
    db.execute('INSERT INTO import_quarantine(release_id,source_record_key,reason_code,safe_error_detail) VALUES (?,?,?,?)',(release_id,record_key,code,detail[:500]))

def insert_food(db, release_id: int, food: dict) -> None:
    cur=db.execute('INSERT INTO official_food_items(release_id,source_food_code,food_name,normalized_name,food_category,manufacturer,product_report_number,serving_reference,basis_amount_g,source_record_date) VALUES (?,?,?,?,?,?,?,?,?,?)',(release_id,food['code'],food['name'],food['normalized_name'],food['food_category'],food['manufacturer'],food['product_report_number'],food['serving_reference'],food['basis_amount_g'],food['source_record_date']))
    db.executemany('INSERT INTO official_nutrient_values(official_food_item_id,nutrient_code,amount,unit,basis_amount_g,confidence,source_field) VALUES (?,?,?,?,?,?,?)',[(cur.lastrowid,key,value,NUTRIENTS[key],food['basis_amount_g'],1.0,key) for key,value in food['nutrients'].items()])

def run_row(db, source_id, release_id, mode, status, input_reference, error=None):
    cur=db.execute('INSERT INTO import_runs(data_source_id,release_id,mode,status,input_reference,error_summary,finished_at) VALUES (?,?,?,?,?,?,?)',(source_id,release_id,mode,status,input_reference,error,now()))
    return cur.lastrowid

def local_reference(path: str) -> str:
    # Keep only a non-sensitive basename; raw payload paths are not exposed or stored.
    return Path(path).name
