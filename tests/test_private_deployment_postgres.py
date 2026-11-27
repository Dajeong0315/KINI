"""Exact deployment bundle on an empty disposable PostgreSQL only."""
import os

import pytest

pytestmark = pytest.mark.skipif(not os.getenv('KINI_PROVISION_TEST_URL'), reason='empty disposable DB required')


def test_atomic_bundle_roles_grants_and_preserved_catalog():
    import psycopg
    from scripts.prepare_private_deployment import build, PUBLIC_TABLES

    with psycopg.connect(os.environ['KINI_PROVISION_TEST_URL'], autocommit=True) as db:
        db.execute('CREATE ROLE anon; CREATE ROLE authenticated; CREATE ROLE service_role')
        for table in PUBLIC_TABLES:
            db.execute(f'CREATE TABLE public.{table}(id integer PRIMARY KEY)')
            db.execute(f'INSERT INTO public.{table} VALUES (42)')
            db.execute(f'ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY')
            db.execute(f'CREATE POLICY backend_catalog_access ON public.{table} TO service_role USING (true)')
        db.execute(build())
        assert db.execute('SELECT count(*) FROM kini_private.schema_migrations').fetchone()[0] == 9
        owners = db.execute("SELECT DISTINCT tableowner FROM pg_tables WHERE schemaname='kini_private'").fetchall()
        assert owners == [('kini_migrator',)]
        assert db.execute("SELECT count(*) FROM pg_tables WHERE schemaname='kini_private' AND NOT rowsecurity").fetchone()[0] == 0
        runtime_url = psycopg.conninfo.make_conninfo(os.environ['KINI_PROVISION_TEST_URL'], user='kini_app')
        with psycopg.connect(runtime_url) as runtime:
            assert runtime.execute('SELECT current_schema()').fetchone()[0] == 'kini_private'
        db.execute('SET ROLE kini_app')
        # SET ROLE does not reload login-time ALTER ROLE settings; the actual
        # runtime login above does. The app also sets search_path per connection.
        db.execute('SET search_path = kini_private,pg_catalog')
        assert db.execute('SELECT current_schema()').fetchone()[0] == 'kini_private'
        assert db.execute("SELECT has_schema_privilege(current_user,'kini_private','CREATE')").fetchone()[0] is False
        assert db.execute("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user").fetchone() == (False, False)
        assert db.execute('SELECT count(*) FROM kini_private.households').fetchone()[0] == 0
        db.execute("INSERT INTO kini_private.households(name) VALUES ('synthetic grant probe')")
        for table in PUBLIC_TABLES:
            assert db.execute(f'SELECT id FROM public.{table}').fetchall() == [(42,)]
            assert db.execute("SELECT has_table_privilege(current_user,%s,'INSERT')", ('public.'+table,)).fetchone()[0] is False
        assert db.execute("SELECT has_table_privilege(current_user,'kini_private.public_catalog_ingredient_links','UPDATE')").fetchone()[0] is False
        db.execute('RESET ROLE')
        assert not db.execute("SELECT has_schema_privilege('anon','kini_private','USAGE')").fetchone()[0]
        assert not db.execute("SELECT has_schema_privilege('authenticated','kini_private','USAGE')").fetchone()[0]
        assert db.execute("SELECT count(*) FROM pg_policies WHERE schemaname='public' AND policyname='backend_catalog_access'").fetchone()[0] == 7
        with pytest.raises(psycopg.errors.RaiseException):
            db.execute(build())
        db.execute('ROLLBACK')
        assert db.execute('SELECT count(*) FROM kini_private.schema_migrations').fetchone()[0] == 9
