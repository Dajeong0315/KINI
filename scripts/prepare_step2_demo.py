"""Initialize one explicitly synthetic household with the restricted app role.

Reference data must already be reviewed. No SQLite copying, no destructive
tests, no fake nutrition. Password goes only to a protected server file.
"""
import json
import os
from pathlib import Path
import secrets
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.recommendation_repository import open_db
from app.services.local_identity import create_account, login
from app.services import inventory_events


def prepare(db, login_name, password):
    if db.execute("SELECT 1 FROM metadata WHERE key='step2_demo_reference_version'").fetchone() is None:
        raise RuntimeError('Reviewed demonstration references required')
    if db.execute('SELECT 1 FROM households LIMIT 1').fetchone():
        raise RuntimeError('Existing household data: inspect instead of reseeding')
    with db:
        household = db.execute("INSERT INTO households(name) VALUES ('[시연] 합성 가구')").lastrowid
        member = db.execute("INSERT INTO members(household_id,name,age) VALUES (?,'합성 참여자',30)", (household,)).lastrowid
        db.executemany('INSERT INTO member_nutrition_targets(member_id,nutrient,amount) VALUES (?,?,?)',
                       [(member,'energy_kcal',1800),(member,'protein_g',60),(member,'sodium_mg',2000)])
        create_account(db, login_name, password, household)
    token = login(db, login_name, password, household)
    for ingredient in (900001,900002,900003):
        event = inventory_events.ingest(db, token, dict(client_event_id=str(uuid4()), source='mock',
            action='stock_in', detected_label='explicit synthetic demonstration inventory', confidence=1.0))
        inventory_events.confirm(db, token, event['event_id'], dict(ingredient_id=ingredient, quantity_g=1000))
    return dict(household_id=household,participant_ids=[member],synthetic=True)


if __name__ == '__main__':
    path = Path('/etc/kini-b/step2-demo.json')
    if path.exists():
        raise SystemExit('Demo credential file exists; inspect rather than overwrite')
    password = secrets.token_urlsafe(32)
    name = 'kini-step2-demo'
    # Store before creating the account, so an interrupted run does not lose its credential.
    fd = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
    with os.fdopen(fd,'w') as stream:
        json.dump(dict(login_name=name,password=password),stream)
    with open_db() as db:
        result = prepare(db,name,password)
    with path.open('w') as stream:
        json.dump(dict(login_name=name,password=password,**result),stream)
    print(json.dumps(dict(status='demo_prepared',synthetic=True,households=1,members=1,inventory_items=3)))
