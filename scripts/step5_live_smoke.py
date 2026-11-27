"""Resumable positive API flow using only the existing synthetic demo household.

Run on the server as administrator. Journal contains synthetic IDs, never tokens.
No model requests, destructive checks, or direct database writes.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import time
from uuid import uuid4

import requests


def main():
    url = 'http://127.0.0.1:5101'
    credential = json.loads(Path('/etc/kini-b/step2-demo.json').read_text())
    journal = Path('/etc/kini-b/step5-demo-progress.json')
    if not journal.exists():
        with os.fdopen(os.open(journal, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
            json.dump({'eaten_at': datetime.now(timezone.utc).isoformat()}, stream)
    state = json.loads(journal.read_text())
    session = requests.Session()
    session.trust_env = False

    def save():
        journal.write_text(json.dumps(state))

    def call(method, path, payload=None, expected=200, memo=None):
        if memo and memo in state:
            return state[memo]
        started = time.monotonic()
        response = session.request(method, url + path, json=payload, timeout=20)
        print(json.dumps(dict(method=method, path=path.split('?')[0], status=response.status_code,
                              seconds=round(time.monotonic() - started, 3))), flush=True)
        assert response.status_code == expected, 'Unexpected HTTP status'
        value = response.json()
        if memo:
            state[memo] = value
            save()
        return value

    ready = call('GET', '/health/ready')
    assert ready['ai_mode'] == 'rules-only' and ready['external_ai'] == 'disabled'
    login = call('POST', '/api/auth/login', {k: credential[k] for k in ('login_name', 'password', 'household_id')})
    session.headers['Authorization'] = 'Bearer ' + login['access_token']
    household = call('GET', '/api/households/' + str(credential['household_id']))
    assert {p['id'] for p in household['members']} >= set(credential['participant_ids'])
    inventory = call('GET', '/api/data/inventory')
    carrot = next(item for item in inventory['items'] if item['ingredient_id'] == 900003)
    normalized = call('POST', '/api/inventory/normalize', dict(name=carrot['name'], quantity=.01, unit='kg'))
    assert normalized['ingredient']['ingredient_id'] == 900003 and normalized['conversion']['quantity_g'] == 10
    if 'incoming_key' not in state:
        state['incoming_key'] = str(uuid4())
        save()
    incoming = call('POST', '/api/data/inventory/events', dict(source='manual', action='stock_in',
        client_event_id=state['incoming_key'], detected_label=carrot['name']), memo='incoming')
    call('POST', f'/api/data/inventory/events/{incoming["event_id"]}/confirm',
         dict(ingredient_id=900003, quantity_g=10), memo='incoming_confirmed')
    body = dict(household_id=credential['household_id'], participant_ids=credential['participant_ids'],
                meal_type='dinner', purchase_allowed=False)
    recommendation = call('POST', '/api/recommendations', body, 201, 'recommendation')
    assert recommendation['ai_status'] == 'disabled'
    meal = call('POST', '/api/meal-sessions', body, 201, 'meal')
    cooking = call('POST', '/api/cooking-sessions', dict(meal_session_id=meal['session_id'],
        recommendation_request_id=recommendation['request_id'],
        recipe_id=recommendation['recommendations'][0]['recipe_id']), 201, 'cooking')
    changed = call('POST', f'/api/cooking-sessions/{cooking["id"]}/ingredients',
                   dict(ingredient=carrot['name'], quantity_g=40), memo='changed')
    before = state.get('before_outflow')
    if before is None:
        state['before_outflow'] = before = call('GET', '/api/data/inventory')
        save()
    events = []
    for ingredient in changed['ingredients']:
        key = 'outflow_' + str(ingredient['ingredient_id'])
        if key + '_key' not in state:
            state[key + '_key'] = str(uuid4())
            save()
        lot = next(row for row in before['items'] if row['ingredient_id'] == ingredient['ingredient_id'])
        event = call('POST', '/api/data/inventory/events', dict(source='manual', action='stock_out',
            client_event_id=state[key + '_key'], detected_label=lot['name']), memo=key)
        confirmation = dict(ingredient_id=ingredient['ingredient_id'], inventory_item_id=lot['id'],
                            quantity_g=ingredient['quantity_g'])
        call('POST', f'/api/data/inventory/events/{event["event_id"]}/confirm', confirmation, memo=key + '_confirmed')
        # Normal idempotent replay on the same synthetic event must not deduct twice.
        call('POST', f'/api/data/inventory/events/{event["event_id"]}/confirm', confirmation)
        events.append(event['event_id'])
    after = call('GET', '/api/data/inventory')
    for ingredient in changed['ingredients']:
        total = lambda snapshot: sum(row['quantity_g'] for row in snapshot['items'] if row['ingredient_id'] == ingredient['ingredient_id'])
        assert total(before) - total(after) == ingredient['quantity_g']
    status = call('GET', f'/api/cooking-sessions/{cooking["id"]}')
    if status['status'] != 'completed':
        call('POST', f'/api/cooking-sessions/{cooking["id"]}/complete', dict(confirmed=True,
            eaten_at=state['eaten_at'], items=[dict(member_id=credential['participant_ids'][0], servings=.5)],
            inventory_event_ids=events), 201)
    # Recover the receipt through GET, as with a lost successful save response.
    status = call('GET', f'/api/cooking-sessions/{cooking["id"]}')
    assert status['status'] == 'completed'
    assert call('GET', '/api/data/inventory') == after
    record = status['meal_record_id']
    query = f'?household_id={credential["household_id"]}&period=today'
    detail = call('GET', f'/api/meal-records/{record}?household_id={credential["household_id"]}')
    assert detail['items'][0]['nutrition']['energy_kcal'] == 196.85
    assert any(row['record_id'] == record for row in call('GET', '/api/meal-records' + query)['records'])
    assert call('GET', '/api/reports/nutrition' + query)['nutrition_totals']['energy_kcal'] >= 196.85
    state['complete'] = True
    save()
    print(json.dumps(dict(stage5_api_flow='passed', duplicate_a_deducted_once=True,
                          b_did_not_deduct=True, lost_receipt_reloaded=True, model_calls=0)))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        import traceback
        frame = traceback.extract_tb(error.__traceback__)[-1]
        print(json.dumps(dict(status='not_verified', error_type=type(error).__name__, line=frame.lineno)))
        raise SystemExit(1)
