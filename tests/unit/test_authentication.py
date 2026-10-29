from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.application.authenticate_user import (
    AccountCredentials,
    AuthenticationFailed,
    AuthenticateUser,
    SessionToStore,
    StoredSession,
    token_digest,
)
from app.infrastructure.password_hasher import Argon2PasswordHasher
from app.interfaces.schemas import LoginRequest


class FakePasswordHasher:
    def verify(self, password_hash: str, password: str) -> bool:
        return password_hash == "known-hash" and password == "correct-password"


class FakeUserRepository:
    def __init__(self, account: AccountCredentials | None):
        self.account = account

    def find_credentials_by_email(self, email: str) -> AccountCredentials | None:
        if self.account is not None and email == "known@example.test":
            return self.account
        return None


class FakeSessionRepository:
    def __init__(self):
        self.records: dict[str, StoredSession] = {}

    def add(self, session: SessionToStore) -> None:
        self.records[session.token_hash] = StoredSession(
            id=session.id,
            user_id=session.user_id,
            csrf_token_hash=session.csrf_token_hash,
            previous_csrf_token_hash=None,
            previous_csrf_expires_at=None,
            created_at=session.created_at,
            last_seen_at=session.last_seen_at,
            expires_at=session.expires_at,
            revoked_at=None,
            email_verified=True,
        )

    def find_by_token_hash(self, token_hash: str) -> StoredSession | None:
        return self.records.get(token_hash)

    def update_last_seen(self, session_id: str, last_seen_at: datetime) -> None:
        for token_hash, session in self.records.items():
            if session.id == session_id:
                self.records[token_hash] = replace(
                    session, last_seen_at=last_seen_at
                )
                return

    def revoke_by_token_hash(self, token_hash: str, revoked_at: datetime) -> None:
        session = self.records.get(token_hash)
        if session is not None and session.revoked_at is None:
            self.records[token_hash] = replace(session, revoked_at=revoked_at)

    def rotate_csrf_token(
        self,
        session_id: str,
        csrf_token_hash: str,
        previous_csrf_expires_at: datetime,
    ) -> None:
        for token_hash, session in self.records.items():
            if session.id == session_id:
                self.records[token_hash] = replace(
                    session,
                    previous_csrf_token_hash=session.csrf_token_hash,
                    previous_csrf_expires_at=previous_csrf_expires_at,
                    csrf_token_hash=csrf_token_hash,
                )
                return


class FakeUnitOfWork:
    def __init__(self, users, sessions):
        self.users = users
        self.sessions = sessions

    def __enter__(self):
        return self

    def __exit__(self, _error_type, _error, _traceback):
        del _error_type, _error, _traceback
        return False


class FakeUnitOfWorkFactory:
    def __init__(self, account: AccountCredentials | None):
        self.users = FakeUserRepository(account)
        self.sessions = FakeSessionRepository()

    def __call__(self):
        return FakeUnitOfWork(self.users, self.sessions)


def _service(account: AccountCredentials | None):
    now = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
    factory = FakeUnitOfWorkFactory(account)
    tokens = iter(("s" * 43, "c" * 43, "n" * 43, "m" * 43))

    def generate_token(length: int) -> str:
        assert length == 32
        return next(tokens)

    service = AuthenticateUser(
        unit_of_work_factory=factory,
        password_hasher=FakePasswordHasher(),
        dummy_password_hash="dummy-hash",
        token_generator=generate_token,
        clock=lambda: now,
    )
    return service, factory, now


def test_argon2id_password_verification_handles_mismatch_and_malformed_hash():
    hasher = Argon2PasswordHasher()
    password_hash = hasher.hash("secure-test-account-password")

    assert hasher.verify(password_hash, "secure-test-account-password")
    assert not hasher.verify(password_hash, "wrong-password")
    assert not hasher.verify("not-an-argon2-hash", "secure-test-account-password")


def test_login_request_representation_does_not_expose_password():
    password = "secure-test-account-password"
    request = LoginRequest(email="worker@example.test", password=password)

    assert password not in repr(request)


def test_unknown_and_incorrect_credentials_have_the_same_failure():
    account = AccountCredentials(
        user_id="account-1",
        password_hash="known-hash",
        email_verified=True,
    )
    service, _, _ = _service(account)

    with pytest.raises(AuthenticationFailed) as unknown:
        service.login("missing@example.test", "wrong-password")
    with pytest.raises(AuthenticationFailed) as incorrect:
        service.login("known@example.test", "wrong-password")

    assert type(unknown.value) is type(incorrect.value)
    assert unknown.value.code == incorrect.value.code == "AUTHENTICATION_FAILED"


def test_login_persists_only_token_digests_and_hides_secrets_from_repr():
    account = AccountCredentials(
        user_id="account-1",
        password_hash="known-hash",
        email_verified=True,
    )
    service, factory, _ = _service(account)

    authenticated = service.login(" Known@Example.Test ", "correct-password")
    stored = factory.sessions.records[token_digest(authenticated.session_token)]

    assert stored.user_id == "account-1"
    assert stored.csrf_token_hash == token_digest(authenticated.csrf_token)
    assert authenticated.session_token not in repr(authenticated)
    assert authenticated.csrf_token not in repr(authenticated)
    assert authenticated.session_token not in repr(stored)
    assert authenticated.csrf_token not in repr(stored)


def test_expired_or_revoked_session_does_not_authenticate_and_logout_revokes():
    account = AccountCredentials(
        user_id="account-1",
        password_hash="known-hash",
        email_verified=True,
    )
    service, factory, now = _service(account)
    authenticated = service.login("known@example.test", "correct-password")
    token_hash = token_digest(authenticated.session_token)
    fresh = factory.sessions.records[token_hash]

    assert service.authenticate(authenticated.session_token).user_id == "account-1"
    factory.sessions.records[token_hash] = replace(
        fresh,
        created_at=now - timedelta(hours=12, seconds=1),
    )
    assert service.authenticate(authenticated.session_token) is None

    factory.sessions.records[token_hash] = fresh
    service.logout(authenticated.session_token)
    assert service.authenticate(authenticated.session_token) is None


def test_csrf_bootstrap_rotates_digest_and_retains_previous_for_five_minutes():
    account = AccountCredentials(
        user_id="account-1",
        password_hash="known-hash",
        email_verified=True,
    )
    service, factory, now = _service(account)
    authenticated = service.login("known@example.test", "correct-password")

    next_token = service.bootstrap_csrf_token(authenticated.session_token)
    stored = factory.sessions.records[token_digest(authenticated.session_token)]

    assert next_token == "n" * 43
    assert stored.csrf_token_hash == token_digest(next_token)
    assert stored.previous_csrf_token_hash == token_digest(authenticated.csrf_token)
    assert stored.previous_csrf_expires_at == now + timedelta(minutes=5)


def test_csrf_bootstrap_keeps_only_the_immediately_previous_token():
    account = AccountCredentials(
        user_id="account-1",
        password_hash="known-hash",
        email_verified=True,
    )
    service, factory, _ = _service(account)
    authenticated = service.login("known@example.test", "correct-password")

    csrf_2 = service.bootstrap_csrf_token(authenticated.session_token)
    csrf_3 = service.bootstrap_csrf_token(authenticated.session_token)
    stored = factory.sessions.records[token_digest(authenticated.session_token)]

    assert csrf_2 == "n" * 43
    assert csrf_3 == "m" * 43
    assert stored.csrf_token_hash == token_digest(csrf_3)
    assert stored.previous_csrf_token_hash == token_digest(csrf_2)
    assert stored.previous_csrf_token_hash != token_digest(authenticated.csrf_token)


@pytest.mark.parametrize("session_state", ["expired", "revoked"])
def test_csrf_bootstrap_does_not_rotate_expired_or_revoked_sessions(session_state):
    account = AccountCredentials(
        user_id="account-1",
        password_hash="known-hash",
        email_verified=True,
    )
    service, factory, now = _service(account)
    authenticated = service.login("known@example.test", "correct-password")
    token_hash = token_digest(authenticated.session_token)
    stored = factory.sessions.records[token_hash]
    if session_state == "expired":
        factory.sessions.records[token_hash] = replace(
            stored,
            last_seen_at=now - timedelta(minutes=31),
        )
    else:
        factory.sessions.records[token_hash] = replace(
            stored,
            revoked_at=now,
        )

    assert service.bootstrap_csrf_token(authenticated.session_token) is None
