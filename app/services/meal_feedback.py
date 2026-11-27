"""Meal-linked feedback domain service. No routes, DB adapter or default storage.

Option codes are supplied by an approved plan. Raw explicit evidence stays pending
mapping: it must never be passed to personalization.add_evidence with guessed weights.
"""
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Annotated, Callable, Literal, Mapping, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .local_identity import AuthenticationError, AuthorizationError


DIMENSIONS = ('taste', 'seasoning', 'difficulty')
Revision = Annotated[int, Field(strict=True, ge=0)]
OptionCode = Annotated[str, Field(strict=True, min_length=1, max_length=100, pattern=r'^\S+$')]


class Ratings(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    taste: OptionCode | None = None
    seasoning: OptionCode | None = None
    difficulty: OptionCode | None = None


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    status: Literal['submitted', 'skipped']
    expected_revision: Revision
    ratings: Ratings = Field(default_factory=Ratings)

    @model_validator(mode='after')
    def check_ratings(self):
        has_ratings = any(self.ratings.model_dump().values())
        if (self.status == 'submitted') != has_ratings:
            raise ValueError('평가는 한 항목 이상, 건너뛰기는 평가 없이 제출하세요.')
        return self


@dataclass(frozen=True)
class FeedbackOptions:
    """No production defaults; each dimension needs planner-approved codes."""
    codes: Mapping[str, frozenset[str]]

    def __post_init__(self):
        if set(self.codes) != set(DIMENSIONS):
            raise ValueError('맛·간·난이도 선택지를 각각 제공하세요.')
        copied = {key: frozenset(values) for key, values in self.codes.items()}
        for values in copied.values():
            if not values:
                raise ValueError('빈 선택지는 사용할 수 없습니다.')
            for code in values:
                # Use the same validation for configuration and request codes.
                Ratings(taste=code)
        object.__setattr__(self, 'codes', MappingProxyType(copied))

    def validate(self, ratings: Ratings):
        if any(value is not None and value not in self.codes[key]
               for key, value in ratings.model_dump().items()):
            raise ValueError('확정된 평가 선택지를 사용하세요.')


class FeedbackError(RuntimeError):
    """Map status_code and public_body at the HTTP boundary; never expose causes."""
    def __init__(self, code, status_code, message):
        super().__init__(message)
        self.code = code
        self.status_code = status_code

    def public_body(self):
        return {'error': str(self), 'code': self.code}


@dataclass(frozen=True)
class Identity:
    household_id: int
    role: str


@dataclass(frozen=True)
class MealTarget:
    record_id: int
    member_id: int
    household_id: int
    recipe_id: int


@dataclass(frozen=True)
class Feedback:
    target: MealTarget
    status: Literal['submitted', 'skipped']
    ratings: Ratings
    revision: int
    updated_at: str

    def evidence(self):
        """Current explicit facts, NOT scored preference_evidence rows.

        Stable slot is (meal_feedback, record_id, member_id, dimension).
        Editing replaces that slot; skipping yields none, including after an edit.
        """
        return tuple((dimension, code) for dimension, code in self.ratings.model_dump().items()
                     if self.status == 'submitted' and code is not None)


class FeedbackTransaction(Protocol):
    def authorize(self, token: str, *, write: bool) -> Identity:
        """Use local_identity.authorize on this transaction's DB connection.

        Revalidate revocation, expiry, active account, household membership and role.
        Preserve AuthenticationError/AuthorizationError. Lock permissions for writes
        using the existing PostgreSQL authorization behavior.
        """
        ...

    def target(self, household_id: int, record_id: int, member_id: int) -> MealTarget | None:
        """Read committed meal_records -> meal_sessions (household) and require
        matching meal_record_items, meal_session_participants and members.household_id.
        Return None for missing OR out-of-scope objects. Never create a meal.
        """
        ...

    def get(self, target: MealTarget) -> Feedback | None: ...

    def save(self, feedback: Feedback, *, expected_revision: int) -> None:
        """Atomic compare-and-swap; absent row has revision 0.

        UNIQUE(record_id, member_id), serialize absent-row races too. Persist the
        current snapshot only. Its evidence() is the canonical pending-mapping
        evidence; any derived projection must REPLACE all slots for this source
        in the same transaction (skip clears them). Never append on retries.
        Raise FeedbackError('revision_conflict', 409, ...) on CAS failure.
        """
        ...


TransactionFactory = Callable[[], AbstractContextManager[FeedbackTransaction]]


class MealFeedbackService:
    """Any recorded meal participant may respond; no designated evaluator.

    Display and voice clients use the same member-scoped contract after resolving
    the responding participant. A household session authorizes the shared device;
    it is not an assertion that the logged-in account is the responding member.
    Opting out before selecting a participant needs no service call.
    """

    def __init__(self, transactions: TransactionFactory, *, options: FeedbackOptions | None = None):
        # Required dependency: no fake/in-memory/SQLite fallback in this module.
        self.transactions = transactions
        self.options = options

    def _execute(self, token, record_id, member_id, payload):
        if not isinstance(token, str) or not token.strip():
            raise AuthenticationError('인증이 필요합니다.')
        if any(type(value) is not int or value <= 0 for value in (record_id, member_id)):
            raise ValueError('식사와 참여자 ID를 확인하세요.')
        # __exit__ must commit before success leaves this method; commit failures
        # are handled exactly like write failures. A supplies rollback semantics.
        try:
            with self.transactions() as tx:
                identity = tx.authorize(token, write=payload is not None)
                if payload is not None and identity.role == 'viewer':
                    raise AuthorizationError('쓰기 권한이 없습니다.')
                target = tx.target(identity.household_id, record_id, member_id)
                if target is None or target.household_id != identity.household_id or (
                    target.record_id, target.member_id
                ) != (record_id, member_id):
                    raise FeedbackError('feedback_target_not_found', 404, '평가할 식사·참여자를 찾을 수 없습니다.')
                current = tx.get(target)
                if payload is None:
                    result = self._response(target, current)
                else:
                    result = self._put(tx, target, current, payload)
            return result
        except (AuthenticationError, AuthorizationError, FeedbackError, ValueError):
            raise
        except Exception as error:
            raise FeedbackError('feedback_storage_unavailable', 503, '평가 저장소에 접근하지 못했습니다. 다시 시도하세요.') from error

    def get(self, token: str, record_id: int, member_id: int):
        return self._execute(token, record_id, member_id, None)

    def put(self, token: str, record_id: int, member_id: int, payload: dict):
        parsed = FeedbackRequest.model_validate(payload)
        return self._execute(token, record_id, member_id, parsed)

    def _put(self, tx, target, current, payload):
        # Exact retry is a no-op even with its original expected_revision, but
        # authentication and target permissions are always rechecked first.
        if current and current.status == payload.status and current.ratings == payload.ratings:
            return self._response(target, current)
        revision = current.revision if current else 0
        if payload.expected_revision != revision:
            raise FeedbackError('revision_conflict', 409, '평가가 변경됐습니다. 다시 조회한 뒤 수정하세요.')
        if payload.status == 'submitted':
            if self.options is None:
                raise FeedbackError('feedback_options_unconfigured', 503, '평가 선택지가 아직 확정되지 않았습니다.')
            self.options.validate(payload.ratings)
        updated = Feedback(target, payload.status, payload.ratings, revision + 1,
                           datetime.now(timezone.utc).isoformat())
        tx.save(updated, expected_revision=revision)
        return self._response(target, updated)

    @staticmethod
    def _response(target, current):
        return {
            'record_id': target.record_id, 'member_id': target.member_id,
            'recipe_id': target.recipe_id,
            'status': current.status if current else 'not_submitted',
            'ratings': current.ratings.model_dump() if current else Ratings().model_dump(),
            'revision': current.revision if current else 0,
            'updated_at': current.updated_at if current else None,
            'personalization_status': 'pending_mapping' if current and current.evidence() else 'no_evidence',
        }
