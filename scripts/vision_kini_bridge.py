"""Publish a completed real Vision result as pending KINI events, or replay queue.

KINI_A_API_URL must be loopback HTTP. For a remote server use an authenticated
SSH local forward to its loopback listener; do not relax the client host check.
Token comes only from environment, never command line, result JSON, or logs.
"""
import argparse
import getpass
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fridge_vision.kini_inventory_client import KiniAPIError, KiniInventoryClient
from fridge_vision.kini_result_bridge import submit_result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--result', type=Path)
    parser.add_argument('--retry', action='store_true')
    parser.add_argument('--login', action='store_true', help='Prompt for login without displaying password/token')
    parser.add_argument('--queue', type=Path, required=True,
                        help='Private local JSONL path; never commit it')
    args = parser.parse_args()
    if bool(args.result) == args.retry:
        parser.error('Choose exactly one of --result or --retry')
    try:
        client = KiniInventoryClient(os.environ['KINI_A_API_URL'],
            'login-pending' if args.login else os.environ['KINI_A_API_TOKEN'], args.queue)
        if args.login:
            name = input('KINI login name: ').strip()
            household = int(input('Household ID: ').strip())
            password = getpass.getpass('KINI password (hidden): ')
            auth = client._call('POST', '/api/auth/login', dict(login_name=name, password=password, household_id=household))
            client.token = auth['access_token']
            del password, auth
        if args.retry:
            events = client.retry_pending()
        else:
            if args.result.stat().st_size > 1024 * 1024:
                raise ValueError('Result exceeds 1 MiB')
            events = submit_result(client, json.loads(args.result.read_text(encoding='utf-8')))
        print(json.dumps({'events': [{'event_id': e['event_id'], 'status': e['status']}
                                    for e in events], 'inventory_auto_confirmed': False}))
        return 0
    except KiniAPIError as exc:
        print(json.dumps({'error': 'KINI event delivery failed', 'http_status': exc.status,
                          'login_required': exc.status in (401, 403),
                          'retry_queue': exc.status is None or exc.status in (408, 429, 500, 502, 503, 504)}))
        return 1
    except (ValueError, OSError, KeyError):
        print(json.dumps({'error': 'Check local URL, authentication settings and Vision result'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
