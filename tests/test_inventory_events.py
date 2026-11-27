from uuid import uuid4
import pytest
from pydantic import ValidationError
from app.services.recommendation_repository import open_db
from app.services.local_identity import create_account, login
from app.services.inventory_events import ingest, confirm, reject, snapshot, EventConflict


@pytest.fixture
def setup(tmp_path):
    db = open_db(str(tmp_path / 'events.sqlite3'))
    create_account(db, 'owner', 'test-password-123', 1)
    token = login(db, 'owner', 'test-password-123', 1)
    yield db, token
    db.close()


def event(**updates):
    return {'client_event_id': str(uuid4()), 'source': 'mock', 'action': 'stock_in',
            'detected_label': '두부', 'confidence': .99, **updates}


def test_confidence_does_not_mutate_inventory_and_duplicate_is_idempotent(setup):
    db, token = setup
    payload = event()
    before = snapshot(db, token)
    first = ingest(db, token, payload)
    second = ingest(db, token, payload)
    assert first == second and first['requires_confirmation'] and first['is_demo']
    after = snapshot(db, token)
    assert before['items'] == after['items']
    assert after['inventory_version'] == 0 and after['pending_event_count'] == 1
    with pytest.raises(EventConflict):
        ingest(db, token, {**payload, 'action': 'stock_out'})


def test_confirm_is_atomic_and_retry_does_not_duplicate_stock(setup):
    db, token = setup
    queued = ingest(db, token, event())
    payload = {'ingredient_id': 4, 'quantity_g': 200, 'expiry_date': '2027-01-01'}
    result = confirm(db, token, queued['event_id'], payload)
    assert confirm(db, token, queued['event_id'], payload) == result
    state = snapshot(db, token)
    assert state['inventory_version'] == 1 and state['pending_event_count'] == 0
    item = next(i for i in state['items'] if i['id'] == result['inventory_item_id'])
    assert item['quantity_g'] == 200 and item['data_status'] == 'needs_confirmation'
    changes = db.execute('SELECT quantity_g,before_quantity_g,after_quantity_g FROM inventory_event_changes').fetchall()
    assert [tuple(row) for row in changes] == [(200, 0, 200)]
    with pytest.raises(EventConflict):
        confirm(db, token, queued['event_id'], {**payload, 'quantity_g': 201})


def test_stock_out_never_goes_negative_or_updates_different_ingredient(setup):
    db, token = setup
    queued = ingest(db, token, event(action='stock_out'))
    item = db.execute('SELECT id,quantity_g FROM inventory_items WHERE ingredient_id=4').fetchone()
    payload = {'ingredient_id': 4, 'quantity_g': item['quantity_g'] + 1, 'inventory_item_id': item['id']}
    with pytest.raises(ValueError):
        confirm(db, token, queued['event_id'], payload)
    assert snapshot(db, token)['inventory_version'] == 0
    assert snapshot(db, token)['pending_event_count'] == 1
    with pytest.raises(ValueError):
        confirm(db, token, queued['event_id'], {**payload, 'ingredient_id': 1, 'quantity_g': 1})
    confirm(db, token, queued['event_id'], {**payload, 'quantity_g': 100})
    assert db.execute('SELECT quantity_g FROM inventory_items WHERE id=?', (item['id'],)).fetchone()[0] == item['quantity_g'] - 100


def test_cross_household_event_and_item_access_blocked(setup):
    db, token = setup
    queued = ingest(db, token, event())
    db.execute("INSERT INTO households VALUES(2,'other')")
    db.commit()
    create_account(db, 'other', 'test-password-123', 2)
    other = login(db, 'other', 'test-password-123', 2)
    with pytest.raises(LookupError):
        confirm(db, other, queued['event_id'], {'ingredient_id': 4, 'quantity_g': 1})
    assert snapshot(db, other)['items'] == []
    assert snapshot(db, other)['pending_event_count'] == 0


def test_rejected_events_do_not_change_stock(setup):
    db, token = setup
    queued = ingest(db, token, event())
    reject(db, token, queued['event_id'])
    with pytest.raises(EventConflict):
        confirm(db, token, queued['event_id'], {'ingredient_id': 4, 'quantity_g': 1})
    assert snapshot(db, token)['inventory_version'] == 0


def test_concurrent_stock_out_cannot_overdraw(setup):
    from concurrent.futures import ThreadPoolExecutor
    from app.services.recommendation_repository import connect
    db, token = setup
    path = db.execute('PRAGMA database_list').fetchone()['file']
    item = db.execute('SELECT id,quantity_g FROM inventory_items WHERE ingredient_id=4').fetchone()
    events = [ingest(db, token, event(action='stock_out'))['event_id'] for _ in range(2)]
    def apply(event_id):
        connection = connect(path)
        try:
            confirm(connection, token, event_id, {'ingredient_id': 4, 'quantity_g': 700,
                'inventory_item_id': item['id']})
            return True
        except ValueError:
            return False
        finally:
            connection.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(apply, events))
    assert sorted(results) == [False, True]
    assert db.execute('SELECT quantity_g FROM inventory_items WHERE id=?', (item['id'],)).fetchone()[0] == 300
    assert snapshot(db, token)['inventory_version'] == 1


@pytest.mark.parametrize('updates', [{'quantity_g': 1}, {'confidence': float('nan')}, {'confidence': 1.01}, {'household_id': 2}])
def test_detection_payload_rejects_untrusted_quantity_and_household(setup, updates):
    db, token = setup
    with pytest.raises(ValidationError):
        ingest(db, token, event(**updates))


def test_http_contract_requires_local_token_and_reflects_sqlite(monkeypatch, tmp_path):
    path = str(tmp_path / 'http.sqlite3')
    monkeypatch.setenv('KINI_DB_PATH', path)
    db = open_db(path)
    create_account(db, 'owner', 'test-password-123', 1)
    token = login(db, 'owner', 'test-password-123', 1)
    db.close()
    from app.main import app
    client = app.test_client()
    assert client.get('/api/data/inventory').status_code == 401
    headers = {'Authorization': 'Bearer ' + token}
    result = client.post('/api/data/inventory/events', json=event(), headers=headers)
    assert result.status_code == 200 and result.json['requires_confirmation']
    result = client.post('/api/data/inventory/events/' + str(result.json['event_id']) + '/confirm',
        json={'ingredient_id': 4, 'quantity_g': 123}, headers=headers)
    assert result.status_code == 200
    inventory = client.get('/api/data/inventory', headers=headers).json
    assert inventory['inventory_version'] == 1
    assert any(i['quantity_g'] == 123 for i in inventory['items'])
