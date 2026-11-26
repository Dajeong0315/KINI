"""Apply checked-in PostgreSQL migrations explicitly."""
from pathlib import Path
import os
import sys

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.services.postgres_repository import connect_postgres


def main():
    if os.getenv("KINI_DB_BACKEND", "").lower() != "postgres":
        raise SystemExit("Set KINI_DB_BACKEND=postgres before running migrations")
    sql = (Path(__file__).parents[1] / "db" / "migrations" / "001_step1_postgresql.sql").read_text(encoding="utf-8")
    db = connect_postgres()
    try:
        with db.cursor() as cur:
            cur.execute(sql)
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
