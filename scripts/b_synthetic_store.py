"""Initialize a NEW disposable B fixture, never an operational database."""
import getpass
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def prepare(path, login_name, password):
    from app.services.recommendation_repository import open_db
    from app.services.local_identity import create_account
    path = Path(path).resolve()
    if path.exists() or os.getenv('KINI_DB_BACKEND', 'sqlite') != 'sqlite':
        raise RuntimeError('Only a new, isolated SQLite fixture is accepted')
    path.parent.mkdir(parents=True, exist_ok=True)
    with open_db(str(path), seed_demo=True) as db:
        db.execute("INSERT INTO households(id,name) VALUES (2,'synthetic-household-2')")
        db.execute("INSERT INTO members(id,household_id,name,age) VALUES (99,2,'synthetic-member-2',30)")
        db.execute("INSERT INTO metadata(key,value) VALUES ('b_store_purpose','synthetic-only')")
        create_account(db, login_name, password, 1)
        create_account(db, login_name + '-other', password, 2)
    if os.name != 'nt':
        path.chmod(0o600)


if __name__ == '__main__':
    prepare(os.environ['KINI_DB_PATH'], input('Synthetic login name: '),
            getpass.getpass('Synthetic password (12+ chars): '))
    print('Synthetic-only store prepared; no operational data copied.')
