"""Authenticated synthetic smoke; reports status/count/latency only, never tokens."""
import argparse
import getpass
import json
import os
import time
from urllib.parse import urlparse
import requests


def smoke(url, name, password, expected='recommendations_available'):
    parsed = urlparse(url)
    if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost')):
        raise ValueError('Use verified HTTPS or loopback HTTP')
    with requests.Session() as session:
        session.trust_env = False
        def call(method, path, **kwargs):
            return session.request(method, url.rstrip('/') + path, timeout=20, **kwargs)
        assert call('GET', '/health/live').json()['status'] == 'alive'
        ready = call('GET', '/health/ready')
        print(json.dumps({'readiness_http': ready.status_code, 'state': ready.json()}, ensure_ascii=False))
        payload = {'household_id': 1, 'participant_ids': [1, 3], 'meal_type': 'dinner'}
        assert call('POST', '/api/recommendations', json=payload).status_code == 401
        credentials = {'login_name': name, 'password': password, 'household_id': 1}
        assert call('POST', '/api/auth/login', json=credentials | {'password': 'intentionally-wrong'}).status_code == 401
        response = call('POST', '/api/auth/login', json=credentials)
        assert response.status_code == 200
        headers = {'Authorization': 'Bearer ' + response.json()['access_token']}
        started = time.monotonic()
        response = call('POST', '/api/recommendations', json=payload, headers=headers)
        body = response.json()
        assert body['status'] == expected
        assert bool(body['recommendations']) == (expected == 'recommendations_available')
        print(json.dumps({'scenario': 'authenticated', 'http': response.status_code,
                          'status': body['status'], 'candidates': len(body['recommendations']),
                          'elapsed_seconds': round(time.monotonic()-started, 3)}))
        blocked = call('POST', '/api/recommendations', json=payload | {'name': 'synthetic-private'}, headers=headers)
        assert blocked.json()['status'] == 'privacy_blocked' and blocked.json()['recommendations'] == []
        foreign = call('GET', '/api/members?household_id=2', headers=headers)
        assert foreign.status_code == 403
        print('PASS: authentication, source privacy block, empty blocked candidates, household boundary')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url')
    parser.add_argument('--expect', choices=['recommendations_available', 'api_error'], default='recommendations_available')
    args = parser.parse_args()
    try:
        smoke(args.url, os.getenv('KINI_B_SMOKE_LOGIN') or input('Synthetic login: '),
              os.getenv('KINI_B_SMOKE_PASSWORD') or getpass.getpass('Synthetic password: '), args.expect)
    except Exception:
        print('FAIL: smoke assertion or connection failure (private details suppressed)')
        raise SystemExit(1)
