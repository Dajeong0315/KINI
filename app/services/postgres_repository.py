"""Small PostgreSQL connection adapter used by the service repositories.

Runtime code only opens a connection. Schema changes are applied by the
explicit migration command in scripts/run_migrations.py.
"""
from __future__ import annotations

import os
import re
from contextlib import contextmanager


def _qmark_to_postgres(query: str) -> str:
    """Keep the existing repository contract while using psycopg placeholders."""
    return query.replace("?", "%s")


class HybridRow(dict):
    """Mapping row compatible with legacy tuple-style fetchone()[0] callers."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return tuple(self.values())[key]
        return super().__getitem__(key)


def hybrid_row(cursor):
    if cursor.description is None:
        return None
    columns = [column.name for column in cursor.description]
    return lambda values: HybridRow(zip(columns, values))


class PostgresConnection:
    is_postgres = True

    def __init__(self, connection):
        self._connection = connection

    def execute(self, query, params=()):
        query = _qmark_to_postgres(query)
        match = re.match(r"\s*INSERT\s+INTO\s+([a-z_]+)\s*\([^)]*\)", query, re.I)
        if match and "RETURNING" not in query.upper() and match.group(1) in {"households", "members", "ingredients", "inventory_items", "inventory_events", "meal_sessions", "recipes", "recommendation_requests", "recommendation_feedback", "preference_evidence", "inferred_preferences", "meal_records", "meal_record_items", "cooking_sessions"}:
            query += " RETURNING id"
        return PostgresCursor(self._connection.execute(query, params))

    def executemany(self, query, params_seq):
        with self._connection.cursor() as cursor:
            cursor.executemany(_qmark_to_postgres(query), params_seq)
            return cursor

    def cursor(self, *args, **kwargs):
        return self._connection.cursor(*args, **kwargs)

    def commit(self):
        return self._connection.commit()

    def rollback(self):
        return self._connection.rollback()

    def close(self):
        return self._connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            self._connection.commit()
        else:
            self._connection.rollback()
        return False


class PostgresCursor:
    def __init__(self, cursor):
        self._cursor = cursor
        self.lastrowid = None
        self._buffered = None
        if cursor.description and len(cursor.description) == 1 and cursor.description[0].name == "id":
            self._buffered = cursor.fetchone()
            self.lastrowid = self._buffered["id"] if self._buffered else None

    def fetchone(self):
        if self._buffered is not None:
            row, self._buffered = self._buffered, None
            return row
        return self._cursor.fetchone()

    def fetchall(self):
        rows = []
        if self._buffered is not None:
            rows.append(self._buffered)
            self._buffered = None
        return rows + list(self._cursor.fetchall())

    def __iter__(self):
        return iter(self.fetchall())

    @property
    def rowcount(self):
        return self._cursor.rowcount


def database_schema():
    """One explicit schema; never fall through to a different public catalog."""
    schema = os.getenv("KINI_DB_SCHEMA", "public")
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema):
        raise RuntimeError("KINI_DB_SCHEMA must be a lowercase PostgreSQL identifier")
    if schema.startswith("pg_") or schema in {"auth", "storage", "vault", "information_schema", "supabase_migrations"}:
        raise RuntimeError("KINI_DB_SCHEMA cannot be a system or Supabase managed schema")
    return schema


def connect_postgres():
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - dependency is deployment-only
        raise RuntimeError("PostgreSQL backend requires psycopg[binary]") from exc
    url = os.getenv("KINI_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("KINI_DATABASE_URL is required for PostgreSQL backend")
    schema = database_schema()
    timeout = int(os.getenv("KINI_DB_CONNECT_TIMEOUT", "5"))
    if not 1 <= timeout <= 60:
        raise RuntimeError("KINI_DB_CONNECT_TIMEOUT must be between 1 and 60 seconds")
    # Session pooler/direct connections only. No public fallback or implicit DDL.
    return PostgresConnection(psycopg.connect(
        url, row_factory=hybrid_row, connect_timeout=timeout,
        options=f"-c search_path={schema},pg_catalog -c statement_timeout=15000",
    ))


@contextmanager
def postgres_transaction(db):
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
