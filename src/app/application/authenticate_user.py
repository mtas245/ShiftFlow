import hashlib
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol
from uuid import uuid4

from app.domain.user import User, UserValidationError


IDLE_SESSION_LIMIT = timedelta(minutes=30)
ABSOLUTE_SESSION_LIMIT = timedelta(hours=12)
CSRF_TOKEN_GRACE_PERIOD = timedelta(minutes=5)
_SESSION_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z")


class AuthenticationFailed(Exception):
    code = "AUTHENTICATION_FAILED"


class AuthenticationUnavailable(Exception):
    code = "AUTHENTICATION_UNAVAILABLE"


class AuthenticationPersistenceError(Exception):
    """Raised when account or session persistence is unavailable."""


class PasswordVerificationFailed(Exception):
    """Raised when the password verification adapter cannot complete."""


@dataclass(frozen=True, slots=True)
class AccountCredentials:
    user_id: str
    password_hash: str = field(repr=False)
    email_verified: bool


@dataclass(frozen=True, slots=True)
class SessionToStore:
    id: str
    user_id: str
    token_hash: str = field(repr=False)
    csrf_token_hash: str = field(repr=False)
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class StoredSession:
    id: str
    user_id: str
    csrf_token_hash: str = field(repr=False)
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    email_verified: bool
    previous_csrf_token_hash: str | None = field(default=None, repr=False)
    previous_csrf_expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    session_token: str = field(repr=False)
    csrf_token: str = field(repr=False)
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class SessionContext:
    user_id: str
    csrf_token_hash: str = field(repr=False)
    previous_csrf_token_hash: str | None = field(default=None, repr=False)
    previous_csrf_expires_at: datetime | None = None


class UserAuthenticationRepository(Protocol):
    def find_credentials_by_email(
        self, email: str
    ) -> AccountCredentials | None: ...


class SessionRepository(Protocol):
    def add(self, session: SessionToStore) -> None: ...

    def find_by_token_hash(self, token_hash: str) -> StoredSession | None: ...

    def update_last_seen(self, session_id: str, last_seen_at: datetime) -> None: ...

    def revoke_by_token_hash(
        self, token_hash: str, revoked_at: datetime
    ) -> None: ...

    def rotate_csrf_token(
        self,
        session_id: str,
        csrf_token_hash: str,
        previous_csrf_expires_at: datetime,
    ) -> None: ...


class AuthenticationUnitOfWork(Protocol):
    users: UserAuthenticationRepository
    sessions: SessionRepository

    def __enter__(self) -> "AuthenticationUnitOfWork": ...

    def __exit__(self, error_type, error, traceback) -> bool | None: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> AuthenticationUnitOfWork: ...


class PasswordVerifier(Protocol):
    def verify(self, password_hash: str, password: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class AuthenticateUser:
    unit_of_work_factory: UnitOfWorkFactory
    password_hasher: PasswordVerifier
    dummy_password_hash: str = field(repr=False)
    token_generator: Callable[[int], str] = secrets.token_urlsafe
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def login(
        self,
        email: str,
        password: str,
        *,
        replaces_token: str | None = None,
    ) -> AuthenticatedSession:
        normalized_email = self._normalize_email(email)
        if not isinstance(password, str) or len(password) > 1024:
            raise AuthenticationFailed

        try:
            with self.unit_of_work_factory() as unit_of_work:
                account = (
                    unit_of_work.users.find_credentials_by_email(normalized_email)
                    if normalized_email is not None
                    else None
                )
        except AuthenticationPersistenceError:
            raise AuthenticationUnavailable from None

        password_hash = (
            account.password_hash if account is not None else self.dummy_password_hash
        )
        try:
            password_matches = self.password_hasher.verify(password_hash, password)
        except PasswordVerificationFailed:
            raise AuthenticationUnavailable from None
        if (
            account is None
            or not account.email_verified
            or not password_matches
        ):
            raise AuthenticationFailed

        now = self.clock()
        session_token = self.token_generator(32)
        csrf_token = self.token_generator(32)
        if not is_valid_session_token(session_token) or not is_valid_session_token(
            csrf_token
        ):
            raise AuthenticationUnavailable
        new_session = SessionToStore(
            id=str(uuid4()),
            user_id=account.user_id,
            token_hash=token_digest(session_token),
            csrf_token_hash=token_digest(csrf_token),
            created_at=now,
            last_seen_at=now,
            expires_at=now + ABSOLUTE_SESSION_LIMIT,
        )
        try:
            with self.unit_of_work_factory() as unit_of_work:
                if replaces_token is not None and is_valid_session_token(
                    replaces_token
                ):
                    unit_of_work.sessions.revoke_by_token_hash(
                        token_digest(replaces_token), now
                    )
                unit_of_work.sessions.add(new_session)
        except AuthenticationPersistenceError:
            raise AuthenticationUnavailable from None

        return AuthenticatedSession(
            session_token=session_token,
            csrf_token=csrf_token,
            expires_at=new_session.expires_at,
        )

    def authenticate(self, session_token: str) -> SessionContext | None:
        if not is_valid_session_token(session_token):
            return None

        now = self.clock()
        try:
            with self.unit_of_work_factory() as unit_of_work:
                session = unit_of_work.sessions.find_by_token_hash(
                    token_digest(session_token)
                )
                if (
                    session is None
                    or session.revoked_at is not None
                    or not session.email_verified
                ):
                    return None
                if self.session_is_expired(
                    created_at=session.created_at,
                    last_seen_at=session.last_seen_at,
                    expires_at=session.expires_at,
                    now=now,
                ):
                    unit_of_work.sessions.revoke_by_token_hash(
                        token_digest(session_token), now
                    )
                    return None
                if now > session.last_seen_at:
                    unit_of_work.sessions.update_last_seen(session.id, now)
        except AuthenticationPersistenceError:
            raise AuthenticationUnavailable from None

        return SessionContext(
            user_id=session.user_id,
            csrf_token_hash=session.csrf_token_hash,
            previous_csrf_token_hash=session.previous_csrf_token_hash,
            previous_csrf_expires_at=session.previous_csrf_expires_at,
        )

    def bootstrap_csrf_token(self, session_token: str) -> str | None:
        if not is_valid_session_token(session_token):
            return None

        now = self.clock()
        try:
            with self.unit_of_work_factory() as unit_of_work:
                session = unit_of_work.sessions.find_by_token_hash(
                    token_digest(session_token)
                )
                if (
                    session is None
                    or session.revoked_at is not None
                    or not session.email_verified
                ):
                    return None
                if self.session_is_expired(
                    created_at=session.created_at,
                    last_seen_at=session.last_seen_at,
                    expires_at=session.expires_at,
                    now=now,
                ):
                    unit_of_work.sessions.revoke_by_token_hash(
                        token_digest(session_token), now
                    )
                    return None

                csrf_token = self.token_generator(32)
                if not is_valid_session_token(csrf_token):
                    raise AuthenticationUnavailable
                unit_of_work.sessions.rotate_csrf_token(
                    session.id,
                    token_digest(csrf_token),
                    now + CSRF_TOKEN_GRACE_PERIOD,
                )
        except AuthenticationPersistenceError:
            raise AuthenticationUnavailable from None

        return csrf_token

    def logout(self, session_token: str) -> None:
        if not is_valid_session_token(session_token):
            return
        try:
            with self.unit_of_work_factory() as unit_of_work:
                unit_of_work.sessions.revoke_by_token_hash(
                    token_digest(session_token), self.clock()
                )
        except AuthenticationPersistenceError:
            raise AuthenticationUnavailable from None

    @staticmethod
    def session_is_expired(
        *,
        created_at: datetime,
        last_seen_at: datetime,
        expires_at: datetime,
        now: datetime,
    ) -> bool:
        return (
            now >= expires_at
            or now >= created_at + ABSOLUTE_SESSION_LIMIT
            or now >= last_seen_at + IDLE_SESSION_LIMIT
        )

    @staticmethod
    def _normalize_email(email: str) -> str | None:
        try:
            return User.create(email=email).email
        except UserValidationError:
            return None


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def is_valid_session_token(token: str) -> bool:
    return (
        isinstance(token, str)
        and _SESSION_TOKEN_PATTERN.fullmatch(token) is not None
    )
