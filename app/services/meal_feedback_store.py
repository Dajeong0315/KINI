"""SQL persistence for meal feedback; uses A connections/auth, never initializes DB.

Schema is an explicit feature-owned setup candidate for A to review/integrate.
Pending evidence is derived from the current row; no scored projection is written.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3

from .local_identity import authorize
from .meal_feedback import Feedback, FeedbackError, Identity, MealTarget, Ratings


def feedback_connection():
    """Explicit backend and existing DB only; no demo seeds or fallback."""
    backend = os.getenv('KINI_DB_BACKEND', '').lower()
    if backend == 'postgres':
        from .postgres_repository import connect_postgres
        if not os.getenv('KINI_DB_SCHEMA') or os.environ['KINI_DB_SCHEMA'] == 'public':
            raise RuntimeError('An explicit private PostgreSQL schema is required')
        return connect_postgres()
    if backend == 'sqlite':
        path = Path(os.environ['KINI_DB_PATH'])
        if not path.is_absolute() or not path.is_file():
            raise RuntimeError('An existing absolute SQLite database path is required')
        db = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        return db
    raise RuntimeError('Explicit sqlite or postgres backend is required')


class SqlFeedbackStore:
    def __init__(self, connection_factory=feedback_connection):
        self.connection_factory = connection_factory

    @contextmanager
    def transaction(self):
        db = self.connection_factory()
        try:
            yield SqlFeedbackTransaction(db)
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()


class SqlFeedbackTransaction:
    def __init__(self, db):
        self.db = db
        self.write = False

    def authorize(self, token, *, write):
        self.write = write
        if getattr(self.db, 'is_postgres', False):
            if not write:
                self.db.execute('SET TRANSACTION READ ONLY')
        else:
            self.db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            if not write:
                self.db.execute('PRAGMA query_only=ON')
        row = authorize(self.db, token, write=write)
        return Identity(row['household_id'], row['role'])

    def target(self, household_id, record_id, member_id):
        postgres = getattr(self.db, 'is_postgres', False)
        if postgres and self.write:
            # Covers absent-row races too. Authorization locks precede this lock.
            self.db.execute('SELECT pg_advisory_xact_lock(hashtextextended(?,0))',
                            (f'kini:meal_feedback:{record_id}',))
        lock = ' FOR SHARE OF r,s,p,m' if postgres and self.write else ''
        row = self.db.execute('''SELECT r.id record_id,p.member_id,s.household_id,r.recipe_id
            FROM meal_records r JOIN meal_sessions s ON s.id=r.session_id
            JOIN meal_session_participants p ON p.session_id=s.id
            JOIN members m ON m.id=p.member_id AND m.household_id=s.household_id
            WHERE s.household_id=? AND r.id=? AND p.member_id=?''' + lock,
                              (household_id, record_id, member_id)).fetchone()
        return MealTarget(**dict(row)) if row else None

    def get(self, target):
        row = self.db.execute('''SELECT member_id,status,taste,seasoning,difficulty,revision,updated_at
            FROM meal_feedback WHERE record_id=?''', (target.record_id,)).fetchone()
        if row is None:
            return None
        try:
            original = MealTarget(target.record_id, row['member_id'], target.household_id, target.recipe_id)
            return Feedback(original, row['status'], Ratings(**{k: row[k] for k in ('taste', 'seasoning', 'difficulty')}),
                            row['revision'], row['updated_at'])
        except (ValueError, TypeError) as error:
            # Invalid stored data is a storage failure, not bad client input.
            raise RuntimeError('Invalid stored feedback') from error

    def save(self, feedback, *, expected_revision):
        if not self.write:
            raise RuntimeError('Feedback write transaction required')
        values = (feedback.status, feedback.ratings.taste, feedback.ratings.seasoning,
                  feedback.ratings.difficulty, feedback.revision, feedback.updated_at,
                  feedback.target.record_id, feedback.target.member_id)
        if expected_revision == 0:
            cursor = self.db.execute('''INSERT INTO meal_feedback
                (status,taste,seasoning,difficulty,revision,updated_at,record_id,member_id)
                VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(record_id) DO NOTHING''', values)
        else:
            cursor = self.db.execute('''UPDATE meal_feedback SET
                status=?,taste=?,seasoning=?,difficulty=?,revision=?,updated_at=?
                WHERE record_id=? AND member_id=? AND revision=?''', (*values, expected_revision))
        if cursor.rowcount != 1:
            raise FeedbackError('revision_conflict', 409, '평가가 변경됐습니다. 다시 조회한 뒤 수정하세요.')
