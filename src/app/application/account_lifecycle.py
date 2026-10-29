import logging
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Protocol
from uuid import uuid4

from app.application.authenticate_user import token_digest
from app.application.create_user import (
    DuplicateEmailConflict,
    PasswordHashingFailed,
)
from app.domain.user import User, UserValidationError


_LOGGER = logging.getLogger(__name__)
_ACTION_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
EMAIL_VERIFICATION_LIFETIME = timedelta(hours=24)
PASSWORD_RESET_LIFETIME = timedelta(hours=1)


class AccountActionPurpose(StrEnum):
    VERIFY_EMAIL = "verify_email"
    RESET_PASSWORD = "reset_password"


class InvalidAccountRegistration(Exception):
    code = "INVALID_ACCOUNT"


class InvalidAccountActionToken(Exception):
    code = "INVALID_ACCOUNT_ACTION_TOKEN"


class AccountLifecyclePersistenceError(Exception):
    """Raised when an account lifecycle database operation fails."""


class AccountLifecycleUnavailable(Exception):
    code = "ACCOUNT_LIFECYCLE_UNAVAILABLE"


class RateLimiterUnavailable(Exception):
    code = "RATE_LIMITING_UNAVAILABLE"


class RateLimitExceeded(Exception):
    code = "RATE_LIMITED"


@dataclass(frozen=True, slots=True)
class AccountIdentity:
    id: str
    email: str = field(repr=False)
    email_verified: bool


@dataclass(frozen=True, slots=True)
class AccountActionTokenToStore:
    id: str
    user_id: str
    purpose: AccountActionPurpose
    token_digest: str = field(repr=False)
    created_at: datetime
    expires_at: datetime


class AccountLifecycleUserRepository(Protocol):
    def find_account_by_email(self, email: str) -> AccountIdentity | None: ...

    def add(self, user: User, password_hash: str) -> None: ...

    def mark_email_verified(self, user_id: str, verified_at: datetime) -> None: ...

    def update_password_hash(self, user_id: str, password_hash: str) -> None: ...


class AccountActionTokenRepository(Protocol):
    def add(self, token: AccountActionTokenToStore) -> None: ...

    def activate(self, token_digest: str, activated_at: datetime) -> bool: ...

    def revoke_active_for_user(
        self,
        user_id: str,
        purpose: AccountActionPurpose,
        revoked_at: datetime,
    ) -> None: ...

    def revoke(self, token_digest: str, revoked_at: datetime) -> None: ...

    def consume(
        self,
        token_digest: str,
        purpose: AccountActionPurpose,
        consumed_at: datetime,
    ) -> str | None: ...


class AccountLifecycleSessionRepository(Protocol):
    def revoke_all_for_user(self, user_id: str, revoked_at: datetime) -> None: ...


class AccountLifecycleUnitOfWork(Protocol):
    users: AccountLifecycleUserRepository
    action_tokens: AccountActionTokenRepository
    sessions: AccountLifecycleSessionRepository

    def __enter__(self) -> "AccountLifecycleUnitOfWork": ...

    def __exit__(self, error_type, error, traceback) -> bool | None: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> AccountLifecycleUnitOfWork: ...


class PasswordHasher(Protocol):
    def hash(self, password: str) -> str: ...


class EmailDelivery(Protocol):
    def send_verification(self, email: str, token: str) -> None: ...

    def send_password_reset(self, email: str, token: str) -> None: ...


class RateLimiter(Protocol):
    def allow(self, scope: str, key: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class AccountLifecycle:
    unit_of_work_factory: UnitOfWorkFactory
    password_hasher: PasswordHasher
    email_delivery: EmailDelivery
    token_generator: Callable[[int], str] = secrets.token_urlsafe
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def register(self, email: str, password: str) -> None:
        normalized_email = self._normalize_email(email)
        if normalized_email is None or not self._valid_password(password):
            raise InvalidAccountRegistration

        try:
            password_hash = self.password_hasher.hash(password)
        except PasswordHashingFailed:
            raise AccountLifecycleUnavailable from None

        now = self.clock()
        token_to_store: AccountActionTokenToStore | None = None
        raw_token: str | None = None
        try:
            with self.unit_of_work_factory() as unit_of_work:
                existing = unit_of_work.users.find_account_by_email(
                    normalized_email
                )
                if existing is not None:
                    return

                user = User.create(email=normalized_email)
                unit_of_work.users.add(user, password_hash)
                token_to_store, raw_token = self._new_token(
                    user_id=user.id,
                    purpose=AccountActionPurpose.VERIFY_EMAIL,
                    now=now,
                    lifetime=EMAIL_VERIFICATION_LIFETIME,
                )
                unit_of_work.action_tokens.revoke_active_for_user(
                    user.id,
                    AccountActionPurpose.VERIFY_EMAIL,
                    now,
                )
                unit_of_work.action_tokens.add(token_to_store)
        except DuplicateEmailConflict:
            return
        except AccountLifecyclePersistenceError:
            raise AccountLifecycleUnavailable from None

        if token_to_store is None or raw_token is None:
            return
        self._deliver(
            purpose=AccountActionPurpose.VERIFY_EMAIL,
            email=normalized_email,
            token=raw_token,
            token_digest_value=token_to_store.token_digest,
            now=now,
        )

    def request_email_verification(self, email: str) -> None:
        self._issue_for_existing_account(
            email=email,
            purpose=AccountActionPurpose.VERIFY_EMAIL,
        )

    def request_password_reset(self, email: str) -> None:
        self._issue_for_existing_account(
            email=email,
            purpose=AccountActionPurpose.RESET_PASSWORD,
        )

    def verify_email(self, raw_token: str) -> None:
        self._consume_token(
            raw_token,
            AccountActionPurpose.VERIFY_EMAIL,
            self._mark_email_verified,
        )

    def reset_password(self, raw_token: str, new_password: str) -> None:
        if not self._valid_password(new_password):
            raise InvalidAccountRegistration

        if not self._is_valid_token(raw_token):
            raise InvalidAccountActionToken

        now = self.clock()
        try:
            with self.unit_of_work_factory() as unit_of_work:
                user_id = unit_of_work.action_tokens.consume(
                    token_digest(raw_token),
                    AccountActionPurpose.RESET_PASSWORD,
                    now,
                )
                if user_id is None:
                    raise InvalidAccountActionToken
                try:
                    password_hash = self.password_hasher.hash(new_password)
                except PasswordHashingFailed:
                    raise AccountLifecycleUnavailable from None
                unit_of_work.users.update_password_hash(user_id, password_hash)
                unit_of_work.sessions.revoke_all_for_user(user_id, now)
        except InvalidAccountActionToken:
            raise
        except AccountLifecyclePersistenceError:
            raise AccountLifecycleUnavailable from None
        except AccountLifecycleUnavailable:
            raise

    def _issue_for_existing_account(
        self,
        *,
        email: str,
        purpose: AccountActionPurpose,
    ) -> None:
        normalized_email = self._normalize_email(email)
        if normalized_email is None:
            return

        now = self.clock()
        lifetime = (
            EMAIL_VERIFICATION_LIFETIME
            if purpose is AccountActionPurpose.VERIFY_EMAIL
            else PASSWORD_RESET_LIFETIME
        )
        raw_token: str | None = None
        token_to_store: AccountActionTokenToStore | None = None
        try:
            with self.unit_of_work_factory() as unit_of_work:
                account = unit_of_work.users.find_account_by_email(
                    normalized_email
                )
                if account is None or (
                    purpose is AccountActionPurpose.VERIFY_EMAIL
                    and account.email_verified
                ) or (
                    purpose is AccountActionPurpose.RESET_PASSWORD
                    and not account.email_verified
                ):
                    return

                token_to_store, raw_token = self._new_token(
                    user_id=account.id,
                    purpose=purpose,
                    now=now,
                    lifetime=lifetime,
                )
                unit_of_work.action_tokens.revoke_active_for_user(
                    account.id,
                    purpose,
                    now,
                )
                unit_of_work.action_tokens.add(token_to_store)
        except AccountLifecyclePersistenceError:
            raise AccountLifecycleUnavailable from None

        if token_to_store is None or raw_token is None:
            return
        self._deliver(
            purpose=purpose,
            email=normalized_email,
            token=raw_token,
            token_digest_value=token_to_store.token_digest,
            now=now,
        )

    def _consume_token(
        self,
        raw_token: str,
        purpose: AccountActionPurpose,
        mutate_account: Callable[
            [AccountLifecycleUnitOfWork, str, datetime], None
        ],
    ) -> None:
        if not self._is_valid_token(raw_token):
            raise InvalidAccountActionToken
        now = self.clock()
        try:
            with self.unit_of_work_factory() as unit_of_work:
                user_id = unit_of_work.action_tokens.consume(
                    token_digest(raw_token),
                    purpose,
                    now,
                )
                if user_id is None:
                    raise InvalidAccountActionToken
                mutate_account(unit_of_work, user_id, now)
        except InvalidAccountActionToken:
            raise
        except AccountLifecyclePersistenceError:
            raise AccountLifecycleUnavailable from None

    @staticmethod
    def _mark_email_verified(
        unit_of_work: AccountLifecycleUnitOfWork,
        user_id: str,
        verified_at: datetime,
    ) -> None:
        unit_of_work.users.mark_email_verified(user_id, verified_at)

    def _deliver(
        self,
        *,
        purpose: AccountActionPurpose,
        email: str,
        token: str,
        token_digest_value: str,
        now: datetime,
    ) -> None:
        try:
            if purpose is AccountActionPurpose.VERIFY_EMAIL:
                self.email_delivery.send_verification(email, token)
            else:
                self.email_delivery.send_password_reset(email, token)
        except Exception:
            try:
                self._revoke_after_delivery_failure(token_digest_value, now)
            except AccountLifecycleUnavailable:
                _LOGGER.error(
                    "Failed to revoke an undelivered account action token"
                )
            _LOGGER.warning(
                "Account action email delivery failed",
                extra={"purpose": purpose.value},
            )
            return

        try:
            with self.unit_of_work_factory() as unit_of_work:
                unit_of_work.action_tokens.activate(
                    token_digest_value,
                    self.clock(),
                )
        except AccountLifecyclePersistenceError:
            raise AccountLifecycleUnavailable from None

    def _revoke_after_delivery_failure(
        self, token_digest_value: str, revoked_at: datetime
    ) -> None:
        try:
            with self.unit_of_work_factory() as unit_of_work:
                unit_of_work.action_tokens.revoke(
                    token_digest_value,
                    revoked_at,
                )
        except AccountLifecyclePersistenceError:
            raise AccountLifecycleUnavailable from None

    def _new_token(
        self,
        *,
        user_id: str,
        purpose: AccountActionPurpose,
        now: datetime,
        lifetime: timedelta,
    ) -> tuple[AccountActionTokenToStore, str]:
        raw_token = self.token_generator(32)
        if not self._is_valid_token(raw_token):
            raise AccountLifecycleUnavailable
        return (
            AccountActionTokenToStore(
                id=str(uuid4()),
                user_id=user_id,
                purpose=purpose,
                token_digest=token_digest(raw_token),
                created_at=now,
                expires_at=now + lifetime,
            ),
            raw_token,
        )

    @staticmethod
    def _normalize_email(email: str) -> str | None:
        try:
            return User.create(email=email).email
        except UserValidationError:
            return None

    @staticmethod
    def _valid_password(password: str) -> bool:
        return isinstance(password, str) and 12 <= len(password) <= 1024

    @staticmethod
    def _is_valid_token(token: str) -> bool:
        return (
            isinstance(token, str)
            and _ACTION_TOKEN_PATTERN.fullmatch(token) is not None
        )
