import sqlite3
import sys
from types import SimpleNamespace

import pytest

from app.services.postgres_repository import database_schema, connect_postgres
from scripts.backup_sqlite import backup
from scripts.check_server_db import check


@pytest.mark.parametrize('schema', ['a,b', 'public;DROP TABLE members', 'auth', 'pg_catalog', 'storage'])
def test_disallow_schema_injection_and_managed_schemas(monkeypatch, schema):
    monkeypatch.setenv('KINI_DB_SCHEMA', schema)
    with pytest.raises(RuntimeError):
        database_schema()


def test_connection_has_no_public_fallback(monkeypatch):
    captured = {}
    def connect(url, **kwargs):
        captured.update(kwargs)
        return object()
    monkeypatch.setitem(sys.modules, 'psycopg', SimpleNamespace(connect=connect))
    monkeypatch.setenv('KINI_DATABASE_URL', 'postgresql://synthetic@localhost/synthetic')
    monkeypatch.setenv('KINI_DB_SCHEMA', 'kini_private')
    connect_postgres()
    assert 'search_path=kini_private,pg_catalog' in captured['options']
    assert captured['connect_timeout'] == 5


def test_backup_includes_wal_and_refuses_overwrite(tmp_path):
    source = tmp_path / 'source.sqlite3'
    destination = tmp_path / 'snapshot.sqlite3'
    db = sqlite3.connect(source)
    try:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE synthetic(id INTEGER PRIMARY KEY)')
        db.execute('INSERT INTO synthetic VALUES (42)')
        db.commit()
        result = backup(source, destination)
        with sqlite3.connect(destination) as snapshot:
            assert snapshot.execute('SELECT id FROM synthetic').fetchone()[0] == 42
        assert result['status'] == 'ok'
        assert len(result['sha256']) == 64
        with pytest.raises(FileExistsError):
            backup(source, destination)
        with pytest.raises(FileExistsError):
            backup(source, source)
    finally:
        db.close()


def test_missing_source_does_not_create_an_empty_database(tmp_path):
    source = tmp_path / 'absent.sqlite3'
    with pytest.raises(FileNotFoundError):
        backup(source, tmp_path / 'snapshot.sqlite3')
    assert not source.exists()


def test_server_gate_refuses_sqlite_and_demo_seed(monkeypatch):
    monkeypatch.setenv('KINI_DB_BACKEND', 'sqlite')
    monkeypatch.setenv('KINI_SEED_DEMO', '0')
    with pytest.raises(RuntimeError, match='postgres_required'):
        check()
    monkeypatch.setenv('KINI_DB_BACKEND', 'postgres')
    monkeypatch.setenv('KINI_SEED_DEMO', '1')
    with pytest.raises(RuntimeError, match='demo_seed'):
        check()


def test_server_gate_refuses_public_schema(monkeypatch):
    monkeypatch.setenv('KINI_DB_BACKEND', 'postgres')
    monkeypatch.setenv('KINI_SEED_DEMO', '0')
    monkeypatch.setenv('KINI_DB_SCHEMA', 'public')
    with pytest.raises(RuntimeError, match='private_schema_required'):
        check()


def test_server_gate_requires_verified_tls(monkeypatch):
    monkeypatch.setenv('KINI_DB_BACKEND', 'postgres')
    monkeypatch.setenv('KINI_SEED_DEMO', '0')
    monkeypatch.setenv('KINI_DB_SCHEMA', 'kini_private')
    monkeypatch.setitem(sys.modules, 'psycopg', SimpleNamespace(
        conninfo=SimpleNamespace(conninfo_to_dict=lambda _: {'sslmode': 'require'})))
    with pytest.raises(RuntimeError, match='verified_tls_required'):
        check()
