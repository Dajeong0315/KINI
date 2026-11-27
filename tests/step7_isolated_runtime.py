"""Standalone smoke on the deployed source with a NEW synthetic-only SQLite.

Run from the release directory, passing the saved Vision fixture path. This
never loads runtime.env or connects to PostgreSQL; no operational DB is copied.
"""
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
from threading import Thread


def main():
    root = Path.cwd().resolve()
    assert (root / 'app/b_runtime.py').is_file()
    sys.path.insert(0, str(root))
    import requests
    from werkzeug.serving import make_server
    from app.b_runtime import create_app
    from app.services.recommendation_repository import open_db
    from fridge_vision.kini_inventory_client import KiniInventoryClient
    from fridge_vision.kini_result_bridge import submit_result
    from scripts.b_synthetic_store import prepare
    result = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    with tempfile.TemporaryDirectory(prefix='kini-vision-synthetic-') as temporary:
        db_path = Path(temporary) / 'new.sqlite3'
        os.environ.update(KINI_DB_BACKEND='sqlite', KINI_DB_PATH=str(db_path),
            KINI_B_STORAGE='synthetic-sqlite', KINI_B_AI_MODE='rules-only',
            KINI_B_MEAL_FEEDBACK_ENABLED='0', KINI_USE_OPENAI='0', KINI_SEED_DEMO='0')
        os.environ.pop('KINI_DATABASE_URL', None)
        password = secrets.token_urlsafe(24)
        prepare(db_path, 'synthetic-vision-server', password)
        with open_db() as db:
            db.execute("INSERT INTO ingredients(id,name,canonical_name,source_type) VALUES (901,'butter','butter','demo')")
            db.execute('INSERT INTO inventory_items(household_id,ingredient_id,quantity_g) VALUES (1,901,100)')
            lot = db.execute('SELECT id FROM inventory_items WHERE ingredient_id=901').fetchone()[0]
        server = make_server('127.0.0.1', 0, create_app(), threaded=True)
        thread = Thread(target=server.serve_forever, daemon=True); thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            with requests.Session() as http:
                http.trust_env = False
                auth = http.post(base + '/api/auth/login', json=dict(login_name='synthetic-vision-server',
                    password=password, household_id=1), timeout=20)
                assert auth.status_code == 200
                token = auth.json()['access_token']
                client = KiniInventoryClient(base, token, Path(temporary) / 'queue.jsonl', timeout=20)
                first = submit_result(client, result)[0]
                assert first['requires_confirmation'] and first['source'] == 'yolo'
                assert submit_result(client, result) == [first]
                snapshot = http.get(base + '/api/data/inventory',
                    headers={'Authorization':'Bearer ' + token}, timeout=20).json()
                assert snapshot['pending_events'] == [first]
                assert next(x for x in snapshot['items'] if x['id'] == lot)['quantity_g'] == 100
                applied = client.confirm(first['event_id'], ingredient_id=901, quantity_g=10, inventory_item_id=lot)
                assert client.confirm(first['event_id'], ingredient_id=901, quantity_g=10, inventory_item_id=lot) == applied
                second = submit_result(client, {**result, 'capture_id':result['capture_id'] + '-reject-validation'})[0]
                assert client.reject(second['event_id'])['status'] == 'rejected'
                assert client.reject(second['event_id'])['status'] == 'rejected'
                with open_db() as db:
                    assert db.execute('SELECT quantity_g FROM inventory_items WHERE id=?', (lot,)).fetchone()[0] == 90
                    assert db.execute('SELECT count(*) FROM inventory_event_changes WHERE event_id=?', (first['event_id'],)).fetchone()[0] == 1
            print(json.dumps(dict(server_local_http='passed', real_saved_yolo_result=True,
                isolated_synthetic_db=True, production_inventory_writes=0, duplicate_deductions=0)))
        finally:
            server.shutdown(); thread.join(timeout=5); server.server_close()


if __name__ == '__main__':
    main()
