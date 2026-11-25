from __future__ import annotations

import json
from .repository import MFDS_SOURCE, ensure_source, create_release, insert_food, quarantine, run_row, local_reference
from .validation import validate_food

def records_from_payload(raw: bytes) -> tuple[list[dict], dict]:
    payload=json.loads(raw.decode('utf-8-sig'))
    if isinstance(payload, list): return payload, {}
    records=payload.get('records') or payload.get('body',{}).get('items') or payload.get('response',{}).get('body',{}).get('items') or []
    if isinstance(records, dict): records=records.get('item', [])
    return records, payload.get('metadata') or {}

def import_mfds(db, raw: bytes, *, input_reference='fixture.json', mode='dry_run') -> dict:
    records, metadata=records_from_payload(raw)
    source_id=ensure_source(db,MFDS_SOURCE)
    if mode == 'dry_run':
        accepted=quarantined=0; seen=set(); reasons=[]
        for record in records:
            item,error=validate_food(record); key=str(record.get('food_code') or record.get('FOOD_CD') or '')
            if item and key not in seen: accepted+=1; seen.add(key)
            else: quarantined+=1; reasons.append(error or 'duplicate_food_code')
        run_row(db,source_id,None,'dry_run','completed',input_reference)
        return {'mode':mode,'record_count':len(records),'accepted':accepted,'quarantined':quarantined,'skipped':0,'failed':0,'reasons':reasons}
    release_id=create_release(db,source_id,raw,metadata.get('external_release_id'),metadata.get('source_published_at'))
    accepted=quarantined=0; seen=set()
    try:
        for index,record in enumerate(records):
            key=str(record.get('food_code') or record.get('FOOD_CD') or index)
            item,error=validate_food(record)
            if key in seen: item,error=None,'duplicate_food_code'
            seen.add(key)
            if error:
                quarantined+=1; quarantine(db,release_id,key,error,'Record did not meet import validation.'); continue
            insert_food(db,release_id,item); accepted+=1
        db.execute("UPDATE dataset_releases SET status='validated',record_count=?,accepted_count=?,quarantined_count=? WHERE id=?",(len(records),accepted,quarantined,release_id))
        run_row(db,source_id,release_id,'import','completed',input_reference)
        return {'release_id':release_id,'mode':mode,'record_count':len(records),'accepted':accepted,'quarantined':quarantined,'skipped':0,'failed':0}
    except Exception as exc:
        db.execute("UPDATE dataset_releases SET status='failed',notes=? WHERE id=?",(type(exc).__name__,release_id)); run_row(db,source_id,release_id,'import','failed',input_reference,type(exc).__name__); raise
