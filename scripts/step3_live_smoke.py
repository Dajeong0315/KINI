"""Positive flow on the existing synthetic demo household; never print credentials."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from uuid import uuid4

import requests


def smoke(url, credentials):
    session = requests.Session()
    session.trust_env = False
    timings = {}

    def call(method, path, payload=None, expected=200):
        started = time.monotonic()
        response = session.request(method, url + path, json=payload, timeout=20)
        timings[path.split('?')[0]] = round(time.monotonic() - started, 3)
        print(json.dumps(dict(method=method, path=path.split('?')[0], status=response.status_code,
                              seconds=timings[path.split('?')[0]])), flush=True)
        assert response.status_code == expected, f'{method} {path.split("?")[0]}: HTTP {response.status_code}'
        return response.json()

    call('GET', '/health/ready')
    login = call('POST', '/api/auth/login', {k: credentials[k] for k in
                 ('login_name', 'password', 'household_id')})
    session.headers['Authorization'] = 'Bearer ' + login['access_token']
    household = credentials['household_id']
    participants = credentials['participant_ids']
    query = f'?household_id={household}&period=today'
    before = call('GET', '/api/meal-records' + query)
    recommendation = call('POST', '/api/recommendations', dict(household_id=household,
        participant_ids=participants, meal_type='dinner', purchase_allowed=False), 201)
    candidate = recommendation['recommendations'][0]
    meal = call('POST', '/api/meal-sessions', dict(household_id=household,
        participant_ids=participants, meal_type='dinner'), 201)
    cooking = call('POST', '/api/cooking-sessions', dict(meal_session_id=meal['session_id'],
        recommendation_request_id=recommendation['request_id'], recipe_id=candidate['recipe_id']), 201)
    inventory = call('GET', '/api/data/inventory')
    events = []
    for ingredient in cooking['ingredients']:
        lot = next(row for row in inventory['items'] if row['ingredient_id'] == ingredient['ingredient_id'])
        event = call('POST', '/api/data/inventory/events', dict(source='manual', action='stock_out',
            client_event_id=str(uuid4()), detected_label=lot['name']))
        call('POST', f'/api/data/inventory/events/{event["event_id"]}/confirm',
             dict(ingredient_id=ingredient['ingredient_id'], inventory_item_id=lot['id'],
                  quantity_g=ingredient['quantity_g']))
        events.append(event['event_id'])
    after_a = call('GET', '/api/data/inventory')
    completed = call('POST', f'/api/cooking-sessions/{cooking["id"]}/complete', dict(confirmed=True,
        eaten_at=datetime.now(timezone.utc).isoformat(), items=[dict(member_id=participants[0], servings=.5)],
        inventory_event_ids=events), 201)
    assert call('GET', '/api/data/inventory') == after_a
    history = call('GET', '/api/meal-records' + query)
    assert history['total'] == before['total'] + 1
    record = completed['record_id']
    detail = call('GET', f'/api/meal-records/{record}?household_id={household}')
    assert detail['items'][0]['servings'] == .5
    assert detail['items'][0]['nutrition']['energy_kcal'] == 198.5
    report = call('GET', '/api/reports/nutrition' + query)
    assert report['nutrition_totals']['energy_kcal'] >= 198.5
    return dict(saved=True, history_reloaded=True, detail_reloaded=True, report_reloaded=True,
                inventory_unchanged_at_b=True, ai='rules-only', timings_seconds=timings)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url')
    args = parser.parse_args()
    try:
        result = smoke(args.url, json.loads(Path('/etc/kini-b/step2-demo.json').read_text()))
    except Exception as error:
        import traceback
        frame = traceback.extract_tb(error.__traceback__)[-1]
        print(json.dumps(dict(status='not_verified', error_type=type(error).__name__,
                             line=frame.lineno, check=frame.line)))
        raise SystemExit(1)
    print(json.dumps(result))
