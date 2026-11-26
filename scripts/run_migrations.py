"""Apply checked-in PostgreSQL migrations explicitly."""
from pathlib import Path
import os
import sys

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.services.postgres_repository import connect_postgres


def main():
    if os.getenv("KINI_DB_BACKEND", "").lower() != "postgres":
        raise SystemExit("Set KINI_DB_BACKEND=postgres before running migrations")
    migrations = sorted((Path(__file__).parents[1] / 'db' / 'migrations').glob('*.sql'))
    db = connect_postgres()
    try:
        # Serialize explicit migration runners, independently of service writes.
        db.execute('SELECT pg_advisory_xact_lock(1263095369)')
        has_history = db.execute("SELECT to_regclass('schema_migrations') IS NOT NULL").fetchone()[0]
        applied = {row[0] for row in db.execute('SELECT version FROM schema_migrations')} if has_history else set()
        completed = []
        for migration in migrations:
            if migration.stem in applied:
                continue
            with db.cursor() as cur:
                cur.execute(migration.read_text(encoding='utf-8'))
            db.execute('INSERT INTO schema_migrations(version) VALUES (?) ON CONFLICT DO NOTHING',
                       (migration.stem,))
            completed.append(migration.stem)
        db.commit()
        print('Applied PostgreSQL migrations: ' + ', '.join(completed) if completed
              else 'PostgreSQL migrations already up to date')
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
