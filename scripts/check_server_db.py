"""Read-only deployment gate. Reports status, never credentials or row data."""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.postgres_repository import connect_postgres, database_schema


def check():
    if os.getenv('KINI_DB_BACKEND') != 'postgres' or os.getenv('KINI_SEED_DEMO') != '0':
        raise RuntimeError('postgres_required_and_demo_seed_must_be_disabled')
    schema = database_schema()
    if schema == 'public':
        raise RuntimeError('private_schema_required')
    import psycopg
    info = psycopg.conninfo.conninfo_to_dict(os.getenv('KINI_DATABASE_URL', ''))
    if info.get('sslmode') != 'verify-full' or not info.get('sslrootcert'):
        raise RuntimeError('verified_tls_required')
    db = connect_postgres()
    try:
        db.execute('SET TRANSACTION READ ONLY')
        if db.execute('SELECT current_schema()').fetchone()[0] != schema:
            raise RuntimeError('target_schema_missing')
        role = db.execute('SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone()
        if role[0] or role[1]:
            raise RuntimeError('restricted_runtime_role_required')
        if db.execute('SELECT has_schema_privilege(current_user, ?, ?)', (schema, 'CREATE')).fetchone()[0]:
            raise RuntimeError('runtime_role_must_not_create_tables')
        if db.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_tables WHERE schemaname=? "
            "AND pg_has_role(current_user, tableowner, 'MEMBER'))", (schema,)
        ).fetchone()[0]:
            raise RuntimeError('runtime_role_must_not_own_tables')
        expected = {p.stem for p in (Path(__file__).resolve().parents[1] / 'db/migrations').glob('*.sql')}
        applied = {row[0] for row in db.execute('SELECT version FROM schema_migrations')}
        if applied != expected:
            raise RuntimeError('migration_version_mismatch')
        # Verify a representative authenticated data read with this runtime role.
        db.execute('SELECT id FROM households LIMIT 0')
        return {'status': 'ok', 'database': 'reachable', 'migrations': len(expected)}
    finally:
        db.rollback()
        db.close()


if __name__ == '__main__':
    try:
        result = check()
    except Exception:
        # Driver messages can include endpoint/user details; keep diagnostics private.
        print(json.dumps({'status': 'unavailable', 'database': 'not_ready'}))
        raise SystemExit(1)
    print(json.dumps(result))
