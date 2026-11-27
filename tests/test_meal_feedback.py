"""Synthetic meals + real SQLite identity checks; feedback persistence is a fake.

No PostgreSQL, production migration, registered HTTP route or external AI here.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.services.local_identity import (
    AuthenticationError, AuthorizationError, authorize, create_account, login, revoke,
)
from app.services.meal_feedback import (
    FeedbackError, FeedbackOptions, FeedbackRequest, Identity, MealFeedbackService, MealTarget,
)
from app.services.recommendation_repository import open_db


MOCK_PATH = Path(__file__).parent / 'fixtures/api_contract/meal_feedback_v1.json'


class FakeStore:
    """Test-only atomic snapshot store, with existing authorization on SQLite."""
    def __init__(self, db):
        self.db = db
        self.rows = {}
        self.saves = 0
        self.fail = None

    @contextmanager
    def transaction(self):
        previous = deepcopy(self.rows)
        try:
            yield self
            if self.fail == 'commit':
                raise OSError('synthetic commit failure')
        except Exception:
            self.rows = previous
            raise

    def authorize(self, token, *, write):
        row = authorize(self.db, token, write=write)
        return Identity(row['household_id'], row['role'])

    def target(self, household_id, record_id, member_id):
        row = self.db.execute('''SELECT r.id record_id, p.member_id, s.household_id, r.recipe_id
            FROM meal_records r JOIN meal_sessions s ON s.id=r.session_id
            JOIN meal_session_participants p ON p.session_id=s.id
            JOIN members m ON m.id=p.member_id AND m.household_id=s.household_id
            WHERE s.household_id=? AND r.id=? AND p.member_id=?''',
                              (household_id, record_id, member_id)).fetchone()
        return MealTarget(**dict(row)) if row else None

    def get(self, target):
        if self.fail == 'read':
            raise OSError('synthetic read failure')
        return self.rows.get(target.record_id)

    def save(self, feedback, *, expected_revision):
        key = feedback.target.record_id
        old = self.rows.get(key)
        if self.fail == 'race' or (old.revision if old else 0) != expected_revision:
            raise FeedbackError('revision_conflict', 409, '다시 조회하세요.')
        self.rows[key] = feedback
        if self.fail == 'save':
            raise OSError('synthetic write failure after mutation')
        self.saves += 1

    def evidence(self):
        return {(record_id, row.target.member_id, dimension): code
                for record_id, row in self.rows.items()
                for dimension, code in row.evidence()}


@pytest.fixture
def context(tmp_path):
    db = open_db(str(tmp_path / 'feedback-test.sqlite3'))
    account = create_account(db, 'feedback-test', 'synthetic-password', 1)
    token = login(db, 'feedback-test', 'synthetic-password', 1)
    with db:
        db.execute("INSERT INTO households(id,name) VALUES (900,'synthetic-other')")
        db.execute("INSERT INTO members(id,household_id,name,age) VALUES (900,900,'synthetic',30)")
        db.execute("INSERT INTO meal_sessions(id,household_id,meal_type) VALUES (101,1,'dinner'),(102,900,'dinner')")
        db.executemany('INSERT INTO meal_session_participants VALUES (?,?)', [(101, 1), (101, 2), (102, 900)])
        db.execute("INSERT INTO meal_records(id,session_id,recipe_id,eaten_at) VALUES (201,101,1,'2026-11-27T09:00:00Z'),(202,102,1,'2026-11-27T09:00:00Z')")
        db.execute('INSERT INTO meal_record_items(record_id,member_id,servings) VALUES (201,1,1),(201,2,0),(202,900,1)')
    store = FakeStore(db)
    options = FeedbackOptions({key: frozenset(values) for key, values in json.loads(MOCK_PATH.read_text(encoding='utf-8'))['approved_options'].items()})
    service = MealFeedbackService(store.transaction, options=options)
    yield service, store, token, account
    db.close()


def put(service, token, *, revision=0, status='submitted', ratings=None, member_id=1):
    return service.put(token, 201, member_id, {
        'status': status, 'expected_revision': revision,
        'ratings': ratings if ratings is not None else {'taste': 'taste_satisfied'},
    })


def test_normal_duplicate_edit_and_skip_replace_evidence(context):
    service, store, token, _ = context
    assert service.get(token, 201, 1)['status'] == 'not_submitted'
    first = put(service, token, ratings={'taste': 'taste_satisfied', 'seasoning': 'seasoning_balanced'})
    assert first['revision'] == 1 and first['recipe_id'] == 1
    assert first['personalization_status'] == 'ready_for_next_recommendation'
    assert put(service, token, ratings={'taste': 'taste_satisfied', 'seasoning': 'seasoning_balanced'}) == first
    assert store.saves == 1 and len(store.evidence()) == 2
    edited = put(service, token, revision=1, ratings={'difficulty': 'difficulty_moderate'})
    assert edited['revision'] == 2
    assert store.evidence() == {(201, 1, 'difficulty'): 'difficulty_moderate'}
    skipped = put(service, token, revision=2, status='skipped', ratings={})
    assert skipped['revision'] == 3 and skipped['personalization_status'] == 'no_evidence'
    assert store.evidence() == {}
    assert put(service, token, revision=2, status='skipped', ratings={}) == skipped
    assert store.saves == 3
    assert put(service, token, revision=3)['revision'] == 4


def test_skip_without_options_or_previous_evidence(context):
    _, store, token, _ = context
    service = MealFeedbackService(store.transaction)
    result = put(service, token, status='skipped', ratings={})
    assert result['status'] == 'skipped' and not store.evidence()
    with pytest.raises(FeedbackError, match='선택지') as caught:
        put(service, token, revision=1)
    assert caught.value.status_code == 503
    assert service.get(token, 201, 1) == result


def test_shared_meal_has_one_editable_evaluation(context):
    service, store, token, _ = context
    first = put(service, token)
    assert service.get(token, 201, 2) == first
    assert put(service, token, member_id=2) == first
    edited = put(service, token, member_id=2, revision=1, ratings={'taste':'taste_dissatisfied'})
    assert edited['member_id'] == 1 and edited['revision'] == 2
    assert len(store.rows) == 1 and len(store.evidence()) == 1
    assert service.get(token, 201, 1) == edited


@pytest.mark.parametrize('record_id,member_id', [(999, 1), (202, 900), (201, 900), (201, 999), (201, 3)])
def test_missing_foreign_and_nonparticipant_targets(context, record_id, member_id):
    service, store, token, _ = context
    for operation in (lambda: service.get(token, record_id, member_id),
                      lambda: service.put(token, record_id, member_id, {'status': 'skipped', 'expected_revision': 0})):
        with pytest.raises(FeedbackError) as caught:
            operation()
        assert caught.value.status_code == 404
    assert not store.rows


def test_session_participant_without_intake_can_evaluate(context):
    service, store, token, _ = context
    with store.db:
        store.db.execute('DELETE FROM meal_record_items WHERE record_id=201 AND member_id=2')
    assert put(service, token, member_id=2)['status'] == 'submitted'


@pytest.mark.parametrize('payload', [
    {}, {'status': 'neutral', 'expected_revision': 0},
    {'status': 'submitted', 'expected_revision': 0},
    {'status': 'skipped', 'expected_revision': 0, 'ratings': {'taste': 'taste_satisfied'}},
    {'status': 'submitted', 'expected_revision': True, 'ratings': {'taste': 'taste_satisfied'}},
    {'status': 'submitted', 'expected_revision': -1, 'ratings': {'taste': 'taste_satisfied'}},
    {'status': 'submitted', 'expected_revision': 0, 'ratings': {'taste': 3}},
    {'status': 'submitted', 'expected_revision': 0, 'ratings': {'salt': 'seasoning_balanced'}},
    {'status': 'submitted', 'expected_revision': 0, 'ratings': {'taste': ''}},
    {'status': 'submitted', 'expected_revision': 0, 'ratings': {'taste': ' '}},
    {'status': 'submitted', 'expected_revision': 0, 'ratings': {'taste': 'x' * 101}},
    {'status': 'skipped', 'expected_revision': 0, 'household_id': 900},
    {'status': 'skipped', 'expected_revision': 0, 'safety_override': True},
    None, [],
])
def test_invalid_payload_rejected(context, payload):
    service, store, token, _ = context
    with pytest.raises(ValidationError):
        service.put(token, 201, 1, payload)
    assert store.saves == 0


def test_unknown_option_rejected_and_zero_portions_share_one_slot(context):
    service, store, token, _ = context
    with pytest.raises(ValueError):
        put(service, token, ratings={'taste': 'not_approved'})
    put(service, token)
    # All meal participants, including zero portions, share the same evaluation.
    put(service, token, member_id=2)
    assert len(store.evidence()) == 1


def test_revision_conflict_and_stale_retry_cannot_restore_old_evidence(context):
    service, store, token, _ = context
    put(service, token)
    put(service, token, revision=1, ratings={'taste': 'taste_dissatisfied'})
    with pytest.raises(FeedbackError) as caught:
        put(service, token)
    assert caught.value.code == 'revision_conflict'
    assert store.evidence() == {(201, 1, 'taste'): 'taste_dissatisfied'}


@pytest.mark.parametrize('failure', ['read', 'save', 'commit', 'race'])
def test_storage_failure_never_returns_success_and_rolls_back(context, failure):
    service, store, token, _ = context
    original = put(service, token)
    store.fail = failure
    with pytest.raises(FeedbackError) as caught:
        put(service, token, revision=1, ratings={'taste': 'taste_dissatisfied'})
    assert caught.value.status_code == (409 if failure == 'race' else 503)
    assert 'synthetic' not in str(caught.value.public_body())
    store.fail = None
    assert service.get(token, 201, 1) == original


@pytest.mark.parametrize('failure', ['save', 'commit'])
def test_first_save_failure_leaves_no_feedback_or_evidence(context, failure):
    service, store, token, _ = context
    store.fail = failure
    with pytest.raises(FeedbackError) as caught:
        put(service, token)
    assert caught.value.status_code == 503
    assert not store.rows and not store.evidence()
    store.fail = None
    assert service.get(token, 201, 1)['status'] == 'not_submitted'


@pytest.mark.parametrize('token', ['', 'bad-token'])
def test_missing_or_invalid_authentication(context, token):
    service, store, _, _ = context
    with pytest.raises(AuthenticationError):
        put(service, token)
    assert not store.rows


def test_viewer_read_only_and_revoked_session_even_on_duplicate(context):
    service, store, token, account = context
    put(service, token)
    with store.db:
        store.db.execute("UPDATE account_households SET role='viewer' WHERE account_id=?", (account,))
    assert service.get(token, 201, 1)['revision'] == 1
    with pytest.raises(AuthorizationError):
        put(service, token)
    revoke(store.db, token)
    with pytest.raises(AuthenticationError):
        put(service, token)


def test_expired_and_disabled_accounts_cannot_read_or_retry(context):
    service, store, token, account = context
    put(service, token)
    with store.db:
        store.db.execute('UPDATE local_accounts SET active=FALSE WHERE id=?', (account,))
    with pytest.raises(AuthenticationError):
        service.get(token, 201, 1)
    with store.db:
        store.db.execute('UPDATE local_accounts SET active=TRUE WHERE id=?', (account,))
        store.db.execute('UPDATE auth_sessions SET created_at=0,expires_at=1 WHERE account_id=?', (account,))
    with pytest.raises(AuthenticationError):
        put(service, token)


def test_feedback_does_not_change_existing_safety_or_personalization(context):
    from app.services.recommendation_engine import recommend
    service, store, token, _ = context
    req = {'household_id': 1, 'participant_ids': [3], 'meal_type': 'dinner', 'purchase_allowed': False}
    before = recommend(store.db, req, persist=False)
    evidence = list(store.db.execute('SELECT * FROM preference_evidence'))
    profiles = list(store.db.execute('SELECT * FROM member_allergies'))
    put(service, token)
    after = recommend(store.db, req, persist=False)
    assert before['excluded'] == after['excluded']
    assert before['recommendations'] == after['recommendations']
    assert any(row['recipe_id'] == 3 for row in after['excluded'])
    assert list(store.db.execute('SELECT * FROM preference_evidence')) == evidence
    assert list(store.db.execute('SELECT * FROM member_allergies')) == profiles


def test_mock_examples_match_service_contract(context):
    service, _, token, _ = context
    mock = json.loads(MOCK_PATH.read_text(encoding='utf-8'))
    for name in ('normal', 'duplicate', 'edit', 'skip'):
        example = mock['examples'][name]
        actual = service.put(token, 201, 1, example['request'])
        assert {k: v for k, v in actual.items() if k != 'updated_at'} == {
            k: v for k, v in example['response'].items() if k != 'updated_at'
        }
        assert datetime.fromisoformat(actual['updated_at']).utcoffset() is not None
    for name in ('invalid', 'unconfigured', 'conflict'):
        FeedbackRequest.model_validate(mock['examples'][name]['request'])
