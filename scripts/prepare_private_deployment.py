"""Build the explicitly authorized, atomic Supabase app-schema setup SQL.

No credentials and no personal data. Refuses an existing schema/role rather
than modifying an unknown deployment. Review and run as the DB administrator.
"""
from pathlib import Path
import argparse
import hashlib

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_TABLES = ('data_sources', 'data_releases', 'active_data_releases', 'catalog_foods',
                 'catalog_recipes', 'catalog_recipe_steps', 'catalog_recipe_nutrients')
WRITE_TABLES = ('households', 'members', 'local_accounts', 'account_households', 'auth_sessions',
    'local_account_sessions', 'member_preferences', 'member_allergies', 'member_health_constraints',
    'member_nutrition_targets', 'inventory_items', 'inventory_events', 'inventory_sync_state',
    'inventory_event_changes', 'meal_sessions', 'meal_session_participants', 'meal_records',
    'meal_record_items', 'nutrition_intakes', 'recommendation_requests', 'recommendation_candidates',
    'recommendation_feedback', 'preference_evidence', 'inferred_preferences',
    'household_behavior_summary', 'cooking_sessions', 'cooking_inventory_event_links')


def build():
    sql = '''-- Authorized KINI private schema setup; no SQLite data or credentials.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';
SELECT pg_advisory_xact_lock(1263095369);
DO $guard$ BEGIN
    IF to_regnamespace('kini_private') IS NOT NULL OR EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname IN ('kini_migrator','kini_app')) THEN
        RAISE EXCEPTION 'Existing KINI schema/role: inspect instead of overwriting';
    END IF;
END $guard$;
CREATE ROLE kini_migrator NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOREPLICATION;
CREATE ROLE kini_app LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOREPLICATION CONNECTION LIMIT 10;
-- No password assigned. External app login is not provisioned by this script.
GRANT kini_migrator TO postgres;
-- PostgreSQL 16+ creator ADMIN membership need not include SET permission.
-- Let the administrator verify the app role without inheriting it by default.
GRANT kini_app TO postgres WITH SET TRUE;
GRANT kini_app TO postgres WITH INHERIT FALSE;
CREATE SCHEMA kini_private AUTHORIZATION kini_migrator;
REVOKE ALL ON SCHEMA kini_private FROM PUBLIC, anon, authenticated, service_role;
SET LOCAL ROLE kini_migrator;
SET LOCAL search_path = kini_private, pg_catalog;
'''
    migrations = sorted((ROOT / 'db/migrations').glob('*.sql'))
    if len(migrations) != 12:
        raise ValueError('Review a new migration set before deployment')
    for path in migrations:
        contents = path.read_text(encoding='utf-8')
        sql += f'\n-- {path.name} SHA256 {hashlib.sha256(contents.encode()).hexdigest()}\n'
        sql += contents + '\n'
        sql += "INSERT INTO schema_migrations(version) VALUES ('" + path.stem + "') ON CONFLICT DO NOTHING;\n"
    sql += '''
-- Non-exposed schema plus RLS: only the dedicated backend role is admitted.
-- Household isolation remains the server's authenticated object checks.
DO $rls$ DECLARE t record; BEGIN
    FOR t IN SELECT tablename FROM pg_tables WHERE schemaname='kini_private' LOOP
        EXECUTE format('ALTER TABLE kini_private.%I ENABLE ROW LEVEL SECURITY', t.tablename);
        IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='kini_private'
            AND tablename=t.tablename AND policyname='kini_app_backend_access') THEN
            EXECUTE format('CREATE POLICY kini_app_backend_access ON kini_private.%I TO kini_app USING (true) WITH CHECK (true)', t.tablename);
        END IF;
    END LOOP;
END $rls$;
RESET ROLE;
REVOKE ALL ON ALL TABLES IN SCHEMA kini_private FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA kini_private FROM PUBLIC, anon, authenticated, service_role;
GRANT USAGE ON SCHEMA kini_private, public TO kini_app;
GRANT SELECT ON ALL TABLES IN SCHEMA kini_private TO kini_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA kini_private TO kini_app;
GRANT INSERT ON kini_private.meal_data_context TO kini_app;
GRANT INSERT, UPDATE ON kini_private.meal_feedback TO kini_app;
'''
    sql += 'GRANT INSERT, UPDATE, DELETE ON ' + ','.join('kini_private.'+t for t in WRITE_TABLES) + ' TO kini_app;\n'
    # Policies apply only to the new backend role. Existing public rows,
    # service_role policies and anon/authenticated access stay unchanged.
    for table in PUBLIC_TABLES:
        sql += f'GRANT SELECT ON public.{table} TO kini_app;\n'
        sql += f'CREATE POLICY kini_app_catalog_read ON public.{table} FOR SELECT TO kini_app USING (true);\n'
    sql += '''
ALTER ROLE kini_app SET search_path = kini_private, pg_catalog;
ALTER ROLE kini_app SET statement_timeout = '15s';
ALTER ROLE kini_app SET lock_timeout = '5s';
COMMIT;
SELECT 'kini_private_setup_applied' AS status,
       (SELECT count(*) FROM kini_private.schema_migrations) AS migrations;
'''
    return sql


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    path = Path(args.output)
    path.write_text(build(), encoding='utf-8')
    print('Prepared SQL; SHA256:', hashlib.sha256(path.read_bytes()).hexdigest())
