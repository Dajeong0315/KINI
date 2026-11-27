"""Explicit, one-time feedback schema setup candidate. Never run at app startup.

A owns production adoption. Run only against a deliberately selected database
after existing A migrations. Re-running fails instead of silently accepting an
unknown table layout. This command never creates base tables or demo data.
"""
from contextlib import closing
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.meal_feedback_store import feedback_connection


SCHEMA = Path(__file__).resolve().parents[1] / 'app/services/meal_feedback_schema.sql'


def install(db):
    postgres = getattr(db, 'is_postgres', False)
    if postgres:
        if db.execute('SELECT current_schema()').fetchone()[0] == 'public':
            raise RuntimeError('A private application schema is required')
        db.execute('SELECT pg_advisory_xact_lock(hashtextextended(?,0))', ('kini:meal_feedback:schema:v1',))
    else:
        db.execute('BEGIN IMMEDIATE')
    db.execute('SELECT id FROM meal_records LIMIT 0')
    db.execute('SELECT id FROM members LIMIT 0')
    db.execute(SCHEMA.read_text(encoding='utf-8'))
    if postgres:
        # Same private-schema/server-authorization boundary as A's base tables.
        # No JWT/RLS model or runtime grants are invented. A assigns DML to the
        # restricted runtime role explicitly; browser roles get no privileges.
        db.execute('REVOKE ALL ON meal_feedback FROM PUBLIC')
        for role in ('anon', 'authenticated'):
            if db.execute('SELECT 1 FROM pg_roles WHERE rolname=?', (role,)).fetchone():
                db.execute(f'REVOKE ALL ON meal_feedback FROM {role}')


def main():
    try:
        with closing(feedback_connection()) as db, db:
            install(db)
    except Exception:
        # Connection strings and SQL/driver details must not reach operator logs.
        raise SystemExit('Feedback schema setup failed. Verify base schema, permissions and prior setup.') from None
    print('Feedback schema installed explicitly. Production adoption requires A review.')


if __name__ == '__main__':
    main()
