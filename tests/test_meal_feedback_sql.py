"""Real SQL persistence/Flask tests on disposable synthetic DBs.

SQLite always runs. PostgreSQL runs only with KINI_FEEDBACK_TEST_POSTGRES=1 and
KINI_DATABASE_URL set to the disposable container started for this feature.
The PG fixture creates/drops its own unique schema, never truncates other schemas.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import os
from pathlib import Path
from threading import Barrier, Thread
from uuid import uuid4

import pytest
import requests
from werkzeug.serving import make_server

from app.meal_feedback_app import create_app
from app.services.local_identity import create_account, login
from app.services.meal_feedback import FeedbackOptions, MealFeedbackService
from app.services.meal_feedback_store import SqlFeedbackStore
from app.services.recommendation_repository import open_db
from scripts.setup_meal_feedback import install


ROOT = Path(__file__).resolve().parents[1]
OPTIONS = FeedbackOptions({
    'taste': frozenset({'test_taste_a', 'test_taste_b'}),
    'seasoning': frozenset({'test_seasoning'}), 'difficulty': frozenset({'test_difficulty'}),
})


def body(revision=0, code='test_taste_a'):
    return {'status': 'submitted', 'expected_revision': revision, 'ratings': {'taste': code}}


@pytest.fixture(params=['sqlite', 'postgres'])
def sql_context(request, tmp_path, monkeypatch):
    if request.param == 'sqlite':
        monkeypatch.setenv('KINI_DB_BACKEND', 'sqlite')
        path = tmp_path / 'feedback.sqlite3'
        with closing(open_db(str(path))) as db:
            db.commit()
        monkeypatch.setenv('KINI_DB_PATH', str(path))
        from app.services.meal_feedback_store import feedback_connection
        connect = feedback_connection
        cleanup = lambda: None
    else:
        if os.getenv('KINI_FEEDBACK_TEST_POSTGRES') != '1' or not os.getenv('KINI_DATABASE_URL'):
            pytest.skip('requires explicitly opted-in disposable PostgreSQL')
        import psycopg
        from psycopg import sql
        from app.services.postgres_repository import PostgresConnection, hybrid_row
        schema = 'feedback_test_' + uuid4().hex
        url = os.environ['KINI_DATABASE_URL']
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))

        def connect():
            return PostgresConnection(psycopg.connect(
                url, row_factory=hybrid_row,
                options=f'-c search_path={schema},pg_catalog -c statement_timeout=10000',
            ))

        def cleanup():
            with psycopg.connect(url, autocommit=True) as admin:
                admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))

    try:
        with closing(connect()) as db:
            if request.param == 'postgres':
                for migration in sorted((ROOT / 'db/migrations').glob('*.sql')):
                    with db.cursor() as cursor:
                        cursor.execute(migration.read_text(encoding='utf-8'))
                db.commit()
            install(db)
            db.commit()
            # Use IDs outside demo family, no real meals or raw voice data.
            db.execute("INSERT INTO households(id,name) VALUES (901,'synthetic-feedback'),(902,'synthetic-foreign')")
            db.execute("INSERT INTO members(id,household_id,name,age) VALUES (901,901,'synthetic-a',30),(902,902,'synthetic-b',30),(903,901,'synthetic-c',30)")
            db.execute("INSERT INTO recipes(id,name,servings,cook_time_minutes) VALUES (901,'synthetic-recipe',2,10)")
            db.execute("INSERT INTO meal_sessions(id,household_id,meal_type) VALUES (101,901,'dinner'),(102,902,'dinner')")
            db.executemany('INSERT INTO meal_session_participants VALUES (?,?)', [(101, 901), (101, 903), (102, 902)])
            db.execute("INSERT INTO meal_records(id,session_id,recipe_id,eaten_at) VALUES (201,101,901,'2026-11-27T09:00:00Z'),(202,102,901,'2026-11-27T09:00:00Z')")
            db.execute('INSERT INTO meal_record_items(record_id,member_id,servings) VALUES (201,901,1),(201,903,1),(202,902,1)')
            db.commit()
            account = create_account(db, 'synthetic-feedback-owner', 'synthetic-password', 901)
            token = login(db, 'synthetic-feedback-owner', 'synthetic-password', 901)
        app = create_app(options=OPTIONS, connection_factory=connect)
        yield app, connect, token, account
    finally:
        cleanup()


def target(member_id=901, record_id=201):
    return f'/api/meal-records/{record_id}/participants/{member_id}/feedback'


def auth(token):
    return {'Authorization': 'Bearer ' + token}


def test_http_persistence_restart_retry_edit_and_skip(sql_context):
    app, connect, token, _ = sql_context
    client = app.test_client()
    assert client.get(target(), headers=auth(token)).json['status'] == 'not_submitted'
    first = client.put(target(), json=body(), headers=auth(token))
    assert first.status_code == 200 and first.json['revision'] == 1
    assert first.headers['Cache-Control'] == 'no-store'
    # New app/new DB connections must see persisted state (no memory fallback).
    restarted = create_app(options=OPTIONS, connection_factory=connect).test_client()
    assert restarted.get(target(), headers=auth(token)).json == first.json
    assert restarted.put(target(), json=body(), headers=auth(token)).json == first.json
    edited = client.put(target(), json=body(1, 'test_taste_b'), headers=auth(token))
    assert edited.status_code == 200 and edited.json['revision'] == 2
    assert client.put(target(), json=body(), headers=auth(token)).status_code == 409
    skipped = client.put(target(), json={'status': 'skipped', 'expected_revision': 2}, headers=auth(token))
    assert skipped.status_code == 200 and skipped.json['personalization_status'] == 'no_evidence'
    second = client.put(target(903), json=body(), headers=auth(token))
    assert second.status_code == 200
    with closing(connect()) as db:
        assert db.execute('SELECT count(*) FROM meal_feedback').fetchone()[0] == 2
        row = db.execute('SELECT * FROM meal_feedback WHERE member_id=901').fetchone()
        assert row['taste'] is None and row['revision'] == 3
        assert db.execute("SELECT count(*) FROM preference_evidence WHERE source_type='meal_feedback'").fetchone()[0] == 0


def test_http_permissions_and_invalid_requests(sql_context):
    app, connect, token, account = sql_context
    client = app.test_client()
    assert client.get(target()).status_code == 401
    assert client.put(target(), json=body(), headers=auth('invalid')).status_code == 401
    for record_id, member_id in [(202, 902), (201, 902), (999, 901), (201, 999)]:
        for method in ('GET', 'PUT'):
            response = client.open(target(member_id, record_id), method=method, json=body(), headers=auth(token))
            assert response.status_code == 404
            assert response.headers['Cache-Control'] == 'no-store'
    for payload in ([], None, {}, {'status': 'skipped', 'expected_revision': True},
                    {**body(), 'evaluator_id': 901}, body(code='unknown')):
        response = client.put(target(), data=json.dumps(payload), content_type='application/json', headers=auth(token))
        assert response.status_code == 400
    assert client.put(target(), data='{', content_type='application/json', headers=auth(token)).status_code == 400
    assert client.put(target(), data='not-json', headers=auth(token)).status_code == 400
    assert client.put(target(), data='x' * 17000, content_type='application/json', headers=auth(token)).status_code == 413
    assert client.get(target() + '?household_id=902', headers=auth(token)).status_code == 400
    assert client.put(target(), json=body(), headers=auth(token)).status_code == 200
    with closing(connect()) as db, db:
        db.execute("UPDATE account_households SET role='viewer' WHERE account_id=?", (account,))
    assert client.get(target(), headers=auth(token)).status_code == 200
    assert client.put(target(), json=body(), headers=auth(token)).status_code == 403
    with closing(connect()) as db, db:
        db.execute('UPDATE auth_sessions SET revoked_at=1 WHERE account_id=?', (account,))
    assert client.get(target(), headers=auth(token)).status_code == 401


def test_unconfigured_options_and_missing_schema_fail_closed(sql_context):
    _, connect, token, _ = sql_context
    client = create_app(connection_factory=connect).test_client()
    assert client.put(target(), json=body(), headers=auth(token)).status_code == 503
    assert client.put(target(), json={'status': 'skipped', 'expected_revision': 0}, headers=auth(token)).status_code == 200
    with closing(connect()) as db, db:
        db.execute('DROP TABLE meal_feedback')
    response = client.get(target(), headers=auth(token))
    assert response.status_code == 503
    assert response.json['code'] == 'feedback_storage_unavailable'
    assert response.headers['Cache-Control'] == 'no-store'


def test_corrupt_stored_rating_is_storage_error_not_client_error(sql_context):
    app, connect, token, _ = sql_context
    client = app.test_client()
    assert client.put(target(), json=body(), headers=auth(token)).status_code == 200
    with closing(connect()) as db, db:
        db.execute("UPDATE meal_feedback SET taste=' ' WHERE record_id=201 AND member_id=901")
    response = client.get(target(), headers=auth(token))
    assert response.status_code == 503 and response.json['code'] == 'feedback_storage_unavailable'


def test_real_http_concurrent_duplicate_and_conflicting_updates(sql_context):
    app, connect, token, _ = sql_context
    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}' + target()

    def concurrent(payloads, request_url=url):
        barrier = Barrier(len(payloads))

        def submit(payload):
            barrier.wait(timeout=10)
            with requests.Session() as session:
                session.trust_env = False
                response = session.put(request_url, json=payload, headers=auth(token), timeout=15)
                return response.status_code, response.json()
        with ThreadPoolExecutor(max_workers=len(payloads)) as pool:
            return list(pool.map(submit, payloads))

    try:
        duplicates = concurrent([body()] * 4)
        assert all(status == 200 and result['revision'] == 1 for status, result in duplicates)
        assert all(result == duplicates[0][1] for _, result in duplicates)
        competing_first = concurrent([body(), {'status': 'skipped', 'expected_revision': 0}],
                                     f'http://127.0.0.1:{server.server_port}' + target(903))
        assert sorted(status for status, _ in competing_first) == [200, 409]
        competing = concurrent([body(1, 'test_taste_b'),
                                {'status': 'skipped', 'expected_revision': 1}])
        assert sorted(status for status, _ in competing) == [200, 409]
        with closing(connect()) as db:
            assert db.execute('SELECT count(*) FROM meal_feedback').fetchone()[0] == 2
            assert db.execute('SELECT revision FROM meal_feedback WHERE member_id=901').fetchone()[0] == 2
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize('point', ['save', 'commit'])
def test_sql_rollback_on_injected_failure(sql_context, point):
    _, connect, token, _ = sql_context
    normal = MealFeedbackService(SqlFeedbackStore(connect).transaction, options=OPTIONS)
    original = normal.put(token, 201, 901, body())

    class FailingConnection:
        def __init__(self):
            self.db = connect()

        def __getattr__(self, name):
            return getattr(self.db, name)

        def execute(self, query, params=()):
            cursor = self.db.execute(query, params)
            if point == 'save' and query.lstrip().startswith('UPDATE meal_feedback'):
                raise OSError('synthetic failure after SQL mutation')
            return cursor

        def commit(self):
            if point == 'commit':
                raise OSError('synthetic commit failure')
            self.db.commit()

    client = create_app(options=OPTIONS, connection_factory=FailingConnection).test_client()
    response = client.put(target(), json=body(1, 'test_taste_b'), headers=auth(token))
    assert response.status_code == 503
    assert 'synthetic' not in response.text
    assert normal.get(token, 201, 901) == original


def test_no_fake_fallback_or_runtime_schema_creation(monkeypatch, tmp_path):
    from app.services.meal_feedback_store import feedback_connection
    monkeypatch.delenv('KINI_DB_BACKEND', raising=False)
    with pytest.raises(RuntimeError):
        feedback_connection()
    monkeypatch.setenv('KINI_DB_BACKEND', 'sqlite')
    missing = tmp_path / 'not-created.sqlite3'
    monkeypatch.setenv('KINI_DB_PATH', str(missing))
    with pytest.raises(RuntimeError):
        feedback_connection()
    assert not missing.exists()


def test_explicit_approved_option_file(monkeypatch, tmp_path):
    from app.meal_feedback_app import load_feedback_options
    path = tmp_path / 'approved-options.json'
    approved = ROOT / 'deploy/b/meal-feedback-options.v1.json'
    path.write_text(approved.read_text(encoding='utf-8'), encoding='utf-8')
    assert load_feedback_options(path).codes == load_feedback_options(approved).codes
    monkeypatch.setenv('KINI_MEAL_FEEDBACK_OPTIONS_FILE', str(path))
    assert create_app().name == 'kini_meal_feedback'
    path.write_text(json.dumps({key: list(codes) for key, codes in OPTIONS.codes.items()}), encoding='utf-8')
    with pytest.raises(RuntimeError, match='Invalid approved'):
        create_app()
    path.write_text('{"taste":"not-an-array"}', encoding='utf-8')
    with pytest.raises(RuntimeError, match='Invalid approved'):
        create_app()
    with pytest.raises(RuntimeError):
        load_feedback_options('relative-options.json')


def test_postgres_restricted_runtime_role_uses_server_authorization(sql_context):
    _, connect, token, _ = sql_context
    with closing(connect()) as db:
        if not getattr(db, 'is_postgres', False):
            # The other adapter's permission behavior is covered above.
            return
        schema = db.execute('SELECT current_schema()').fetchone()[0]
        role = 'feedback_role_' + uuid4().hex
        # Names are generated locally, never supplied by an HTTP request.
        db.execute(f'CREATE ROLE {role} NOLOGIN NOSUPERUSER NOBYPASSRLS')
        db.execute(f'GRANT USAGE ON SCHEMA {schema} TO {role}')
        db.execute(f'GRANT SELECT,INSERT,UPDATE ON ALL TABLES IN SCHEMA {schema} TO {role}')
        db.commit()
    try:
        def restricted():
            db = connect()
            db.execute(f'SET ROLE {role}')
            db.commit()
            return db
        with closing(restricted()) as db:
            assert db.execute('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone()[0] is False
            assert db.execute('SELECT has_schema_privilege(current_user,?,?)', (schema, 'CREATE')).fetchone()[0] is False
            assert db.execute("SELECT pg_has_role(current_user,tableowner,'MEMBER') FROM pg_tables WHERE schemaname=? AND tablename='meal_feedback'", (schema,)).fetchone()[0] is False
            assert db.execute('''SELECT NOT EXISTS(
                SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace,
                LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) acl
                WHERE n.nspname=? AND c.relname='meal_feedback' AND acl.grantee=0)''', (schema,)).fetchone()[0] is True
        client = create_app(options=OPTIONS, connection_factory=restricted).test_client()
        assert client.put(target(), json=body(), headers=auth(token)).status_code == 200
        assert client.get(target(), headers=auth(token)).json['revision'] == 1
        assert client.put(target(902, 202), json=body(), headers=auth(token)).status_code == 404
    finally:
        with closing(connect()) as db, db:
            db.execute(f'REVOKE ALL ON ALL TABLES IN SCHEMA {schema} FROM {role}')
            db.execute(f'REVOKE ALL ON SCHEMA {schema} FROM {role}')
            db.execute(f'DROP ROLE {role}')
