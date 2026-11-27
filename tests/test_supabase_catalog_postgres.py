"""Disposable container integration. Dedicated URL; never uses production URL."""
import os

import pytest

pytestmark = pytest.mark.skipif(not os.getenv('KINI_STEP2_TEST_URL'), reason='dedicated disposable DB required')


def test_private_migration_and_read_adapter_with_restricted_role(monkeypatch):
    import psycopg
    from app.services.postgres_repository import connect_postgres
    from app.services.supabase_catalog import SupabasePublicCatalog
    from app.services.recommendation_provider import RecommendationNutritionProvider
    from scripts.run_migrations import main

    # This test owns an empty Docker database. Do not point this variable at a
    # Supabase project: it creates synthetic public tables and a test role.
    url = os.environ['KINI_STEP2_TEST_URL']
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute('CREATE SCHEMA kini_step2')
    monkeypatch.setenv('KINI_DATABASE_URL', url)
    monkeypatch.setenv('KINI_DB_BACKEND', 'postgres')
    monkeypatch.setenv('KINI_DB_SCHEMA', 'kini_step2')
    main()
    main()  # Idempotent migration history; public not touched.

    release = '10000000-0000-4000-8000-000000000001'
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute('''CREATE TABLE public.data_sources(code text PRIMARY KEY,rights_status text);
            CREATE TABLE public.data_releases(id uuid PRIMARY KEY,source_code text,status text);
            CREATE TABLE public.active_data_releases(source_code text,release_id uuid,cache_version bigint);
            CREATE TABLE public.catalog_foods(release_id uuid,external_id text,name text,basis_amount numeric,
                basis_unit text,energy_kcal numeric,protein_g numeric,carbohydrate_g numeric,
                fat_g numeric,sodium_mg numeric,sugar_g numeric);
            CREATE ROLE step2_app LOGIN NOSUPERUSER NOBYPASSRLS;
            GRANT USAGE ON SCHEMA public,kini_step2 TO step2_app;
            GRANT SELECT ON public.data_sources,public.data_releases,public.active_data_releases,
                public.catalog_foods,kini_step2.public_catalog_ingredient_links TO step2_app;
            INSERT INTO public.data_sources VALUES ('mfds_nutrition','approved');
            INSERT INTO kini_step2.ingredients(id,name,canonical_name,category) VALUES (901,'synthetic','synthetic','vegetable');''')
        admin.execute("INSERT INTO public.data_releases VALUES (%s,'mfds_nutrition','published')", (release,))
        admin.execute("INSERT INTO public.active_data_releases VALUES ('mfds_nutrition',%s,1)", (release,))
        admin.execute("INSERT INTO public.catalog_foods VALUES (%s,'SYNTHETIC','synthetic raw',100,'g',40,2,8,0,20,NULL)", (release,))
        admin.execute("INSERT INTO kini_step2.public_catalog_ingredient_links VALUES (901,%s,'SYNTHETIC','synthetic raw','2026-11-27','synthetic evidence')", (release,))
        for table in ('data_sources', 'data_releases', 'active_data_releases', 'catalog_foods'):
            admin.execute(f'ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY')
            admin.execute(f'CREATE POLICY synthetic_read ON public.{table} FOR SELECT TO step2_app USING (true)')

    restricted_url = psycopg.conninfo.make_conninfo(url, user='step2_app')
    monkeypatch.setenv('KINI_DATABASE_URL', restricted_url)
    db = connect_postgres()
    try:
        db.execute('SET TRANSACTION READ ONLY')
        record = RecommendationNutritionProvider(SupabasePublicCatalog()).get(db, 901, None)
        assert record.values == dict(energy_kcal=40., protein_g=2., carbohydrate_g=8.,
                                     fat_g=0., sodium_mg=20., basis_amount_g=100.)
        assert record.release_id == release and record.external_id == 'SYNTHETIC'
        assert not db.execute("SELECT has_table_privilege(current_user,'public.catalog_foods','INSERT')").fetchone()[0]
        assert not db.execute("SELECT rolbypassrls FROM pg_roles WHERE rolname=current_user").fetchone()[0]
        assert db.execute('SELECT current_schema()').fetchone()[0] == 'kini_step2'
    finally:
        db.rollback()
        db.close()
