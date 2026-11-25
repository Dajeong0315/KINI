import json
import sqlite3
import pytest
from app.services.recommendation_repository import open_db, initialize
from app.services.catalog_repository import stage, validate, publish, rollback
from app.services.local_identity import create_account, login, authorize, revoke, create_meal_for_session, meal_participants
from app.services.integrations.operational_metrics import validate_metric, SupabaseMetricsRepository


@pytest.fixture
def db(tmp_path):
    connection = open_db(str(tmp_path / 'test.sqlite3'))
    yield connection
    connection.close()


def food(**overrides):
    return dict({'external_id': 'F1', 'kind': 'food', 'name': '두부', 'basis_amount': 100,
                 'basis_unit': 'g', 'nutrients': {'energy_kcal': 76, 'sodium_mg': None}}, **overrides)


def approved(db):
    db.execute("UPDATE catalog_data_sources SET rights_status='approved' WHERE code='mfds_nutrition'")
    db.commit()


def test_private_initialization_never_seeds_on_reopen(tmp_path):
    path = str(tmp_path / 'empty.sqlite3')
    connection = open_db(path, seed_demo=False)
    assert connection.execute('SELECT COUNT(*) FROM households').fetchone()[0] == 0
    connection.close()
    connection = open_db(path)
    assert connection.execute('SELECT COUNT(*) FROM households').fetchone()[0] == 0
    assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
    connection.close()


def test_migration_is_additive_and_repeatable(db):
    before = [tuple(r) for r in db.execute('SELECT * FROM members')]
    initialize(db)
    assert [tuple(r) for r in db.execute('SELECT * FROM members')] == before
    assert [r[0] for r in db.execute('SELECT version FROM platform_migrations ORDER BY version')] == [1, 2]


def test_session_hash_expiry_revocation_and_join(db):
    create_account(db, 'Owner', 'test-password-123', 1)
    token = login(db, 'owner', 'test-password-123', 1)
    stored = db.execute('SELECT token_hash FROM auth_sessions').fetchone()[0]
    assert token != stored and len(stored) == 64
    meal = create_meal_for_session(db, token, [1, 3], 'dinner')
    assert [p['id'] for p in meal_participants(db, token, meal)] == [1, 3]
    with pytest.raises(PermissionError):
        authorize(db, token, household_id=2)
    with pytest.raises(PermissionError):
        authorize(db, token, now=10**12)
    revoke(db, token)
    with pytest.raises(PermissionError):
        authorize(db, token)


def test_cross_household_participants_and_viewer_writes_rejected(db):
    db.execute("INSERT INTO households VALUES(2,'다른 가구')")
    db.execute("INSERT INTO members VALUES(99,2,'다른 구성원',30)")
    db.commit()
    account = create_account(db, 'owner', 'test-password-123', 1)
    token = login(db, 'owner', 'test-password-123', 1)
    with pytest.raises(PermissionError):
        create_meal_for_session(db, token, [99], 'dinner')
    db.execute("UPDATE account_households SET role='viewer' WHERE account_id=?", (account,))
    db.commit()
    with pytest.raises(PermissionError):
        create_meal_for_session(db, token, [1], 'dinner')
    with pytest.raises(PermissionError):
        login(db, 'owner', 'wrong-password', 1)


def test_import_is_not_live_until_explicit_publish_and_rollback_versions_cache(db):
    approved(db)
    first = stage(db, 'mfds_nutrition', [food()], source_revision='fixture-v1')
    assert db.execute('SELECT COUNT(*) FROM active_data_releases').fetchone()[0] == 0
    assert validate(db, first) == []
    publish(db, first)
    # Missing values stay absent; do not turn an unknown sodium value into zero.
    assert db.execute('SELECT COUNT(*) FROM catalog_food_nutrients').fetchone()[0] == 1
    second = stage(db, 'mfds_nutrition', [food(name='새 두부')], source_revision='fixture-v2')
    validate(db, second)
    publish(db, second)
    rollback(db, 'mfds_nutrition', first)
    row = db.execute('SELECT release_id,cache_version FROM active_data_releases').fetchone()
    assert tuple(row) == (first, 3)
    assert db.execute('SELECT COUNT(*) FROM ingredients').fetchone()[0] == 9
    with pytest.raises(ValueError):
        publish(db, first)


@pytest.mark.parametrize('change', [dict(basis_amount=None), dict(basis_unit='serving'),
                                   dict(nutrients={'sodium_mg': -1}), dict(nutrients={})])
def test_invalid_food_quarantined(db, change):
    release = stage(db, 'mfds_nutrition', [food(**change)], source_revision='invalid')
    assert validate(db, release)
    with pytest.raises(ValueError):
        publish(db, release)


def test_rights_review_required_and_duplicate_stage_atomic(db):
    release = stage(db, 'mfds_nutrition', [food()], source_revision='test')
    validate(db, release)
    with pytest.raises(ValueError):
        publish(db, release)
    before = db.execute('SELECT COUNT(*) FROM data_releases').fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):
        stage(db, 'mfds_nutrition', [food(), food()], source_revision='duplicate')
    assert db.execute('SELECT COUNT(*) FROM data_releases').fetchone()[0] == before


def test_meal_pins_release_and_snapshot_does_not_change_on_new_import(db):
    approved(db)
    release = stage(db, 'mfds_nutrition', [food()], source_revision='v1')
    validate(db, release)
    publish(db, release)
    create_account(db, 'owner', 'test-password-123', 1)
    token = login(db, 'owner', 'test-password-123', 1)
    meal = create_meal_for_session(db, token, [1], 'dinner')
    second = stage(db, 'mfds_nutrition', [food()], source_revision='v2')
    validate(db, second)
    publish(db, second)
    assert db.execute('SELECT release_id FROM meal_data_context WHERE meal_session_id=?', (meal,)).fetchone()[0] == release


def test_recipe_steps_are_normalized_without_guessing_servings(db):
    db.execute("UPDATE catalog_data_sources SET rights_status='approved' WHERE code='mfds_recipe'")
    db.commit()
    release = stage(db, 'mfds_recipe', [{'external_id': 'R1', 'kind': 'recipe', 'name': '두부찜',
        'ingredients_text': '두부 100g', 'steps': ['두부를 찐다.']}], source_revision='test')
    assert validate(db, release) == []
    publish(db, release)
    assert db.execute('SELECT servings FROM catalog_recipes').fetchone()[0] is None
    assert db.execute('SELECT instruction FROM catalog_recipe_steps').fetchone()[0] == '두부를 찐다.'


def test_cookrcp01_nutrition_keeps_serving_basis_and_missing_values(db):
    from app.services.data_import.mfds_recipe import normalize
    recipe = normalize({'RCP_SEQ': '1', 'RCP_NM': '두부찜', 'RCP_PARTS_DTLS': '두부 100g',
        'MANUAL01': '찐다', 'INFO_ENG': '76', 'INFO_NA': ''})
    assert recipe['servings'] is None
    db.execute("UPDATE catalog_data_sources SET rights_status='approved' WHERE code='mfds_recipe'")
    db.commit()
    release = stage(db, 'mfds_recipe', [recipe], source_revision='test')
    assert validate(db, release) == []
    publish(db, release)
    row = db.execute('SELECT nutrient,amount,basis_amount,basis_unit FROM catalog_recipe_nutrients').fetchone()
    assert tuple(row) == ('energy_kcal', 76, 1, 'serving')


def test_metric_boundary_blocks_pii_before_network():
    class Network:
        called = False
        def post(self, *args, **kwargs):
            self.called = True
            raise AssertionError('network must not be called')
    network = Network()
    repo = SupabaseMetricsRepository(url='https://zyjrwychceuxxfdproev.supabase.co', secret_key='fake', session=network)
    with pytest.raises(ValueError):
        repo.write_daily_snapshot({'metric_date': '2026-11-25', 'metric_code': 'api_error',
            'sample_count': 1, 'total_value': 1, 'member_id': 3})
    assert not network.called
    assert validate_metric({'metric_date': '2026-11-25', 'metric_code': 'cache_hit',
        'sample_count': 2, 'total_value': 2})['sample_count'] == 2


def test_data_status_does_not_expose_personal_data(monkeypatch, tmp_path):
    monkeypatch.setenv('KINI_DB_PATH', str(tmp_path / 'status.sqlite3'))
    from app.main import app
    response = app.test_client().get('/api/data/status')
    assert response.status_code == 200
    assert response.json['authentication']['http_enforced'] is False
    assert not {'members', 'household_id', 'token', 'password_hash'} & set(response.json)
