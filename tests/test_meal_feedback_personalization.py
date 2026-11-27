"""One shared evaluation, current-fact ranking, and safety on synthetic SQLite."""
from pathlib import Path

import pytest

from app.services.recommendation_repository import open_db
from app.services.recommendation_engine import recommend
from app.services.meal_feedback import MealFeedbackService
from app.services.meal_feedback_store import SqlFeedbackStore
from app.services.local_identity import create_account, login
from app.meal_feedback_app import load_feedback_options
from scripts.setup_meal_feedback import install
from test_recommendation_rules_postgres import seed_family, request


def test_edit_skip_and_guidance_change_next_recommendation_not_safety(tmp_path, monkeypatch):
    path = tmp_path / 'feedback-personalization.sqlite3'
    monkeypatch.setenv('KINI_DB_BACKEND', 'sqlite')
    monkeypatch.setenv('KINI_DB_PATH', str(path))
    monkeypatch.setenv('KINI_B_MEAL_FEEDBACK_ENABLED', '1')
    db = open_db(str(path), seed_demo=False)
    seed_family(db)
    install(db)
    db.execute("INSERT INTO recipes(id,name,servings,cook_time_minutes,spicy) VALUES (502,'second synthetic',2,15,0)")
    db.execute('INSERT INTO recipe_ingredients VALUES (502,901,200)')
    db.execute("INSERT INTO members(id,household_id,name,age) VALUES (12,1,'non-eating participant',30)")
    db.execute("INSERT INTO meal_sessions(id,household_id,meal_type) VALUES (201,1,'dinner')")
    db.executemany('INSERT INTO meal_session_participants VALUES (?,?)', [(201,11),(201,12)])
    db.execute("INSERT INTO meal_records(id,session_id,recipe_id,eaten_at) VALUES (201,201,501,'2026-11-27T12:00:00+09:00')")
    db.execute('INSERT INTO meal_record_items(record_id,member_id,servings) VALUES (201,11,1),(201,12,0)')
    # Equal prior meal counts keep variety from masking the bounded taste signal.
    db.execute("INSERT INTO meal_sessions(id,household_id,meal_type) VALUES (202,1,'dinner')")
    db.execute('INSERT INTO meal_session_participants VALUES (202,11)')
    db.execute("INSERT INTO meal_records(id,session_id,recipe_id,eaten_at) VALUES (202,202,502,'2026-11-27T12:00:00+09:00')")
    db.commit()
    create_account(db, 'feedback-policy-test', 'synthetic-password', 1)
    token = login(db, 'feedback-policy-test', 'synthetic-password', 1)
    options = load_feedback_options(Path(__file__).resolve().parents[1] / 'deploy/b/meal-feedback-options.v1.json')
    service = MealFeedbackService(SqlFeedbackStore().transaction, options=options)
    before = recommend(db, request(), persist=False)
    original = next(c for c in before['recommendations'] if c['recipe_id'] == 501)
    stock = [tuple(row) for row in db.execute('SELECT id,quantity_g FROM inventory_items')]
    payload = dict(status='submitted', expected_revision=0, ratings=dict(taste='taste_satisfied',
                   seasoning='seasoning_salty', difficulty='difficulty_hard'))
    saved = service.put(token, 201, 12, payload)
    assert service.put(token, 201, 11, payload) == saved
    after = recommend(db, request(), persist=False)
    candidate = next(c for c in after['recommendations'] if c['recipe_id'] == 501)
    assert candidate['scores']['total'] == pytest.approx(original['scores']['total'] + 2)
    assert candidate['nutrition_per_serving'] == original['nutrition_per_serving']
    assert [x['code'] for x in candidate['cooking_guidance']] == ['seasoning_salty','difficulty_hard']
    assert after['recommendations'][0]['recipe_id'] == 501
    with monkeypatch.context() as cap:
        cap.setattr('app.services.recommendation_engine.score_personalization', lambda *args: (15., [], []))
        capped = recommend(db, request(), persist=False)
        limited = next(c for c in capped['recommendations'] if c['recipe_id'] == 501)
        assert limited['personalization_score'] == 15
        assert limited['applied_preference_signals'][-1]['adjustment'] == 0
    service.put(token, 201, 11, dict(status='submitted', expected_revision=1, ratings={'taste':'taste_dissatisfied'}))
    changed = recommend(db, request(), persist=False)
    assert changed['recommendations'][0]['recipe_id'] == 502
    assert db.execute('SELECT count(*) FROM meal_feedback').fetchone()[0] == 1
    assert db.execute('SELECT count(*) FROM preference_evidence').fetchone()[0] == 0
    # Skip removes all effects; it is neither neutral nor a negative sample.
    service.put(token, 201, 12, dict(status='skipped', expected_revision=2))
    skipped = recommend(db, request(), persist=False)
    candidate = next(c for c in skipped['recommendations'] if c['recipe_id'] == 501)
    assert candidate['scores']['total'] == original['scores']['total']
    assert candidate['cooking_guidance'] == []
    service.put(token, 201, 11, payload | {'expected_revision':3})
    db.execute("INSERT INTO member_allergies VALUES (11,'synthetic')")
    db.commit()
    unsafe = recommend(db, request(), persist=False)
    assert not unsafe['recommendations']
    assert [tuple(row) for row in db.execute('SELECT id,quantity_g FROM inventory_items')] == stock
    db.close()
