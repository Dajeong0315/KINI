"""Local perception events -> explicit confirmation -> atomic inventory changes.

No image, household history or event is exported to any cloud service here.
These operations own a transaction; callers pass a connection without pending writes.
"""
import hashlib
import json
from datetime import date, datetime, timezone, timedelta
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field
from .local_identity import authorize


def _begin_write(db):
    # SQLite needs its immediate writer lock; PostgreSQL rejects that syntax.
    db.execute('BEGIN' if _is_postgres(db) else 'BEGIN IMMEDIATE')


def _is_postgres(db):
    return getattr(db, 'is_postgres', False)


def _lock_event(db, event_id, household):
    query = 'SELECT * FROM inventory_events WHERE id=? AND household_id=?'
    if _is_postgres(db):
        query += ' FOR UPDATE'
    return db.execute(query, (event_id, household)).fetchone()


class DetectionEvent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    client_event_id: UUID
    source: Literal['mock', 'yolo', 'manual'] = 'mock'
    action: Literal['stock_in', 'stock_out']
    detected_label: str = Field(min_length=1, max_length=100)
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)


class Confirmation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    ingredient_id: int = Field(gt=0, strict=True)
    quantity_g: float = Field(gt=0, le=1000000, allow_inf_nan=False, strict=True)
    inventory_item_id: int | None = Field(default=None, gt=0, strict=True)
    expiry_date: date | None = None


class EventConflict(ValueError):
    pass


def _fingerprint(model):
    return hashlib.sha256(json.dumps(model.model_dump(mode='json'), sort_keys=True).encode()).hexdigest()


def _event_result(row):
    return {'event_id': row['id'], 'client_event_id': row['client_event_id'], 'source': row['source'],
            'is_demo': row['source'] == 'mock', 'action': row['action'],
            'detected_label': row['detected_label'], 'confidence': row['confidence'],
            'status': row['status'], 'requires_confirmation': row['status'] == 'pending_confirmation',
            'inventory_item_id': row['inventory_item_id']}


def ingest(db, token, payload):
    obj = DetectionEvent.model_validate(payload)
    fingerprint = _fingerprint(obj)
    with db:
        _begin_write(db)
        household = authorize(db, token, write=True)['household_id']
        if _is_postgres(db):
            db.execute('''INSERT INTO inventory_events
                (household_id,client_event_id,source,action,detected_label,confidence,payload_sha256)
                VALUES (?,?,?,?,?,?,?) ON CONFLICT (household_id,client_event_id) DO NOTHING''',
                       (household, str(obj.client_event_id), obj.source, obj.action,
                        obj.detected_label, obj.confidence, fingerprint))
            old = db.execute('SELECT * FROM inventory_events WHERE household_id=? AND client_event_id=?',
                             (household, str(obj.client_event_id))).fetchone()
            if old['payload_sha256'] != fingerprint:
                raise EventConflict('같은 이벤트 ID의 내용이 다릅니다.')
            return _event_result(old)
        old = db.execute('SELECT * FROM inventory_events WHERE household_id=? AND client_event_id=?',
                         (household, str(obj.client_event_id))).fetchone()
        if old:
            if old['payload_sha256'] != fingerprint:
                raise EventConflict('같은 이벤트 ID의 내용이 다릅니다.')
            return _event_result(old)
        event_id = db.execute('''INSERT INTO inventory_events
            (household_id,client_event_id,source,action,detected_label,confidence,payload_sha256)
            VALUES (?,?,?,?,?,?,?)''', (household, str(obj.client_event_id), obj.source, obj.action,
                                       obj.detected_label, obj.confidence, fingerprint)).lastrowid
        return _event_result(db.execute('SELECT * FROM inventory_events WHERE id=?', (event_id,)).fetchone())


def confirm(db, token, event_id, payload):
    obj = Confirmation.model_validate(payload)
    fingerprint = _fingerprint(obj)
    with db:
        # Serialize before reading quantity so concurrent confirmations cannot overdraw stock.
        _begin_write(db)
        identity = authorize(db, token, write=True)
        household = identity['household_id']
        event = _lock_event(db, event_id, household)
        if not event:
            raise LookupError('이벤트를 찾을 수 없습니다.')
        if event['status'] == 'applied':
            if event['confirmation_sha256'] != fingerprint:
                raise EventConflict('이미 다른 내용으로 반영한 이벤트입니다.')
            return _event_result(event)
        if event['status'] != 'pending_confirmation':
            raise EventConflict('반영할 수 없는 이벤트 상태입니다.')
        ingredient = db.execute('SELECT * FROM ingredients WHERE id=? AND active=TRUE', (obj.ingredient_id,)).fetchone()
        if not ingredient:
            raise ValueError('등록된 재료를 선택하세요.')
        item_id = obj.inventory_item_id
        if event['action'] == 'stock_in':
            before, remaining = 0, obj.quantity_g
            if item_id is not None:
                raise ValueError('입고는 소비기한별 새 재고로 저장합니다.')
            expiry = obj.expiry_date.isoformat() if obj.expiry_date else None
            # Quantity confirmation does not certify freshness, labels or allergen safety.
            item_id = db.execute('''INSERT INTO inventory_items
                (household_id,ingredient_id,quantity_g,quantity,unit,original_name,data_status,
                 freshness_state,expiry_date,use_by,received_at)
                VALUES (?,?,?,?,?,?,'needs_confirmation','normal',?,?,?)''',
                (household, obj.ingredient_id, obj.quantity_g, obj.quantity_g, 'g', ingredient['name'], expiry, expiry,
                 datetime.now(timezone(timedelta(hours=9))).date().isoformat())).lastrowid
        else:
            if item_id is None or obj.expiry_date is not None:
                raise ValueError('출고할 재고 ID가 필요하며 소비기한은 변경할 수 없습니다.')
            item_query = 'SELECT * FROM inventory_items WHERE id=? AND household_id=? AND ingredient_id=?'
            if _is_postgres(db):
                item_query += ' FOR UPDATE'
            item = db.execute(item_query, (item_id, household, obj.ingredient_id)).fetchone()
            if not item or item['quantity_g'] is None or obj.quantity_g > item['quantity_g']:
                raise ValueError('재고 소속·재료·남은 수량을 확인하세요.')
            remaining = max(0, item['quantity_g'] - obj.quantity_g)
            before = item['quantity_g']
            if _is_postgres(db):
                updated = db.execute("""UPDATE inventory_items SET quantity_g=?,quantity=?,unit='g'
                    WHERE id=? AND household_id=? AND ingredient_id=? AND quantity_g >= ?""",
                    (remaining, remaining, item_id, household, obj.ingredient_id, obj.quantity_g))
                if updated.rowcount != 1:
                    raise ValueError('inventory quantity changed during confirmation')
            else:
                db.execute("UPDATE inventory_items SET quantity_g=?,quantity=?,unit='g' WHERE id=?", (remaining, remaining, item_id))
        db.execute('''INSERT INTO inventory_event_changes
            (event_id,ingredient_id,inventory_item_id,quantity_g,before_quantity_g,after_quantity_g,confirmed_by)
            VALUES (?,?,?,?,?,?,?)''', (event_id, obj.ingredient_id, item_id, obj.quantity_g, before, remaining, identity['account_id']))
        db.execute('''UPDATE inventory_events SET status='applied',inventory_item_id=?,confirmation_sha256=?,
            applied_at=CURRENT_TIMESTAMP WHERE id=?''', (item_id, fingerprint, event_id))
        db.execute('''INSERT INTO inventory_sync_state VALUES (?,1,CURRENT_TIMESTAMP)
            ON CONFLICT(household_id) DO UPDATE SET inventory_version=inventory_sync_state.inventory_version+1,last_applied_at=CURRENT_TIMESTAMP''', (household,))
        return _event_result(db.execute('SELECT * FROM inventory_events WHERE id=?', (event_id,)).fetchone())


def reject(db, token, event_id):
    with db:
        _begin_write(db)
        household = authorize(db, token, write=True)['household_id']
        event = _lock_event(db, event_id, household)
        if not event:
            raise LookupError('이벤트를 찾을 수 없습니다.')
        if event['status'] == 'applied':
            raise EventConflict('반영된 재고는 새 보정 이벤트로 변경해야 합니다.')
        db.execute("UPDATE inventory_events SET status='rejected' WHERE id=?", (event_id,))
        return {'event_id': event_id, 'status': 'rejected', 'requires_confirmation': False}


def snapshot(db, token):
    household = authorize(db, token)['household_id']
    state = db.execute('SELECT * FROM inventory_sync_state WHERE household_id=?', (household,)).fetchone()
    items = [dict(r) for r in db.execute('''SELECT x.id,x.ingredient_id,i.name,x.quantity_g,x.expiry_date,x.data_status
        FROM inventory_items x JOIN ingredients i ON i.id=x.ingredient_id
        WHERE x.household_id=? ORDER BY x.id''', (household,))]
    pending = db.execute("SELECT count(*) FROM inventory_events WHERE household_id=? AND status='pending_confirmation'", (household,)).fetchone()[0]
    vision_pending = db.execute("""SELECT count(*) FROM inventory_events WHERE household_id=?
        AND source='yolo' AND status='pending_confirmation'""", (household,)).fetchone()[0]
    events = [_event_result(r) for r in db.execute("""SELECT * FROM inventory_events
        WHERE household_id=? AND source='yolo' AND status='pending_confirmation' ORDER BY id LIMIT 100""", (household,))]
    return {'items': items, 'inventory_version': state['inventory_version'] if state else 0,
            'last_applied_at': state['last_applied_at'] if state else None, 'pending_event_count': pending,
            'pending_events': events, 'pending_vision_count': vision_pending,
            'source': 'postgresql' if _is_postgres(db) else 'local_sqlite', 'cloud_inventory_export_enabled': False}
