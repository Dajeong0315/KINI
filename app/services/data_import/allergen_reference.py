from __future__ import annotations

import hashlib, json
from datetime import datetime, timezone
from .repository import ensure_source, run_row

ALLERGEN_SOURCE={'source_key':'allergen_reference_reviewed', 'provider_name':'reviewed reference', 'dataset_name':'allergen labeling reference', 'source_url':'https://www.mfds.go.kr/', 'expected_format':'json'}

def import_allergen_reference(db, raw: bytes, *, input_reference='fixture.json') -> dict:
    payload=json.loads(raw.decode('utf-8-sig'))
    required=('source_name','source_url','legal_or_reference_version','items')
    if any(not payload.get(key) for key in required): raise ValueError('Allergen reference fixture is missing required metadata.')
    checksum=hashlib.sha256(raw).hexdigest()
    source_id=ensure_source(db,{**ALLERGEN_SOURCE,'source_url':payload['source_url']})
    cur=db.execute("INSERT INTO allergen_reference_versions(source_name,source_url,legal_or_reference_version,effective_date,checksum,status) VALUES (?,?,?,?,?,'validated')",(payload['source_name'],payload['source_url'],payload['legal_or_reference_version'],payload.get('effective_date'),checksum))
    version_id=cur.lastrowid; codes=set(); quarantined=0
    for item in payload['items']:
        code=str(item.get('allergen_code','')).strip(); name=str(item.get('display_name_ko','')).strip(); aliases=item.get('aliases',[])
        if not code or not name or code in codes or not isinstance(aliases,list): quarantined+=1; continue
        codes.add(code); db.execute('INSERT INTO allergen_reference_items(version_id,allergen_code,display_name_ko,aliases_json,active) VALUES (?,?,?,?,?)',(version_id,code,name,json.dumps(aliases,ensure_ascii=False),int(item.get('active',True))))
    run_row(db,source_id,None,'import','completed',input_reference)
    return {'version_id':version_id,'accepted':len(codes),'quarantined':quarantined,'input_reference':input_reference}
