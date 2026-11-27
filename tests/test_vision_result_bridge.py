"""Actual saved output contract + isolated synthetic inventory, no model/DB cloud calls."""
import json
from pathlib import Path
from uuid import uuid5

import pytest
import requests
from app.services import inventory_events as inventory
from app.services.local_identity import create_account, login
from app.services.recommendation_repository import open_db
from fridge_vision.kini_inventory_client import KiniAPIError, KiniInventoryClient
from fridge_vision.kini_result_bridge import EVENT_NAMESPACE, events_from_result, submit_result


def real_result():
    return json.loads((Path(__file__).parent / 'fixtures/vision/real_sample_9159259.json').read_text(encoding='utf-8'))


def test_actual_output_maps_only_candidates_with_existing_uuid_contract():
    result = real_result()
    (candidate,) = events_from_result(result)
    assert candidate == dict(client_event_id=str(uuid5(EVENT_NAMESPACE,
        result['capture_id'] + '|stock_out|butter|0')), source='yolo', action='stock_out',
        detected_label='버터', confidence=None)
    assert 'quantity_g' not in candidate and 'ingredient_id' not in candidate
    result['removed'].append(dict(item_id='unknown_package', name='Unknown', quantity=2))
    assert events_from_result(result) == [candidate]


@pytest.mark.parametrize('update', [dict(source='mock'), dict(capture_id=''),
    dict(removed=[dict(item_id='butter', quantity=1.5)]),
    dict(removed=[dict(item_id='butter', quantity=True)]),
    dict(removed=[dict(item_id='butter', quantity=-1)])])
def test_invalid_result_never_sends(update):
    class Never:
        def record_detection(self, **kwargs): pytest.fail('Invalid capture sent')
    with pytest.raises(ValueError):
        submit_result(Never(), {**real_result(), **update})


def test_actual_capture_pending_confirm_retry_reject_and_household_boundary(tmp_path, monkeypatch):
    monkeypatch.setenv('KINI_DB_BACKEND', 'sqlite')
    db = open_db(str(tmp_path / 'vision.sqlite3'))
    try:
        create_account(db, 'vision-owner', 'synthetic-password', 1)
        token = login(db, 'vision-owner', 'synthetic-password', 1)
        db.execute("INSERT INTO ingredients(id,name,source_type) VALUES (901,'버터','demo')")
        db.execute("INSERT INTO inventory_items(household_id,ingredient_id,quantity_g) VALUES (1,901,100)")
        db.commit()
        lot = db.execute('SELECT id FROM inventory_items WHERE ingredient_id=901').fetchone()[0]
        candidate = events_from_result(real_result())[0]
        first = inventory.ingest(db, token, candidate)
        assert inventory.ingest(db, token, candidate) == first
        assert first in inventory.snapshot(db, token)['pending_events']
        with pytest.raises(ValueError):
            inventory.confirm(db, token, first['event_id'], dict(ingredient_id=901, inventory_item_id=lot))
        body = dict(ingredient_id=901, quantity_g=10, inventory_item_id=lot)
        applied = inventory.confirm(db, token, first['event_id'], body)
        assert inventory.confirm(db, token, first['event_id'], body) == applied
        assert db.execute('SELECT quantity_g FROM inventory_items WHERE id=?', (lot,)).fetchone()[0] == 90
        assert db.execute('SELECT count(*) FROM inventory_event_changes').fetchone()[0] == 1
        another = inventory.ingest(db, token, {**candidate, 'client_event_id': str(uuid5(EVENT_NAMESPACE, 'another'))})
        inventory.reject(db, token, another['event_id'])
        inventory.reject(db, token, another['event_id'])
        assert inventory.snapshot(db, token)['pending_events'] == []
        with pytest.raises(inventory.EventConflict):
            inventory.confirm(db, token, another['event_id'], body)
        db.execute("INSERT INTO households(id,name) VALUES (2,'other')")
        db.commit()
        create_account(db, 'other', 'synthetic-password', 2)
        other = login(db, 'other', 'synthetic-password', 2)
        assert inventory.snapshot(db, other)['pending_events'] == []
        with pytest.raises(LookupError): inventory.reject(db, other, first['event_id'])
    finally:
        db.close()


def test_response_loss_queues_stable_id_and_never_confirms(tmp_path):
    calls = []
    def lost(method, url, **kwargs):
        calls.append(kwargs['json'])
        assert url.endswith('/inventory/events')
        raise requests.ConnectionError('synthetic response loss')
    client = KiniInventoryClient('http://127.0.0.1:1234', 'synthetic-token', tmp_path / 'queue.jsonl', request=lost)
    with pytest.raises(KiniAPIError): submit_result(client, real_result())
    queued = json.loads(client.queue_path.read_text(encoding='utf-8'))
    assert queued['payload']['client_event_id'] == calls[0]['client_event_id']
    assert 'synthetic-token' not in client.queue_path.read_text(encoding='utf-8')
