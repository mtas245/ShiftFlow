import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.infrastructure.password_hasher import Argon2PasswordHasher
from app.infrastructure.persistence.models import (
    Base,
    AccountActionTokenRecord,
    UserRecord,
    UserSessionRecord,
)
from app.interfaces.main import create_app


pytestmark = pytest.mark.postgres


class RecordingEmailDelivery:
    def __init__(self, *, failure: bool = False):
        self.messages: list[tuple[str, str, str]] = []
        self.failure = failure

    def send_verification(self, email: str, token: str) -> None:
        self.messages.append(("verification", email, token))
        if self.failure:
            raise RuntimeError(f"mail failure with token {token}")

    def send_password_reset(self, email: str, token: str) -> None:
        self.messages.append(("password_reset", email, token))
        if self.failure:
            raise RuntimeError(f"mail failure with token {token}")


class AllowAllRateLimiter:
    def __init__(self, *, allow_requests: bool = True):
        self.scopes: list[str] = []
        self.allow_requests = allow_requests

    def allow(self, scope: str, key: str) -> bool:
        del key
        self.scopes.append(scope)
        return self.allow_requests


def _test_database_url():
    raw_url = os.environ.get("SHIFTFLOW_TEST_DATABASE_URL")
    if not raw_url:
        raise RuntimeError(
            "SHIFTFLOW_TEST_DATABASE_URL must target disposable PostgreSQL"
        )
    url = make_url(raw_url)
    if (
        url.drivername != "postgresql+psycopg"
        or url.host not in {"127.0.0.1", "::1"}
        or url.database != "shiftflow_test"
        or url.username != "shiftflow_test_user"
    ):
        raise RuntimeError(
            "Refusing PostgreSQL URL outside the dedicated local test database"
        )
    return raw_url


def _repository_root():
    return Path(__file__).resolve().parents[2]


def _upgrade_head():
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=_repository_root(),
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def account_api(monkeypatch):
    database_url = _test_database_url()
    engine = create_engine(database_url, pool_pre_ping=True)
    safe_database = False
    try:
        with engine.connect() as connection:
            database, username = connection.execute(
                text("SELECT current_database(), current_user")
            ).one()
        if database != "shiftflow_test" or username != "shiftflow_test_user":
            pytest.fail(
                "Refusing connection not authenticated to the dedicated test database"
            )
        safe_database = True
        Base.metadata.drop_all(engine)
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
        monkeypatch.setenv("DATABASE_URL", database_url)
        _upgrade_head()
        mailer = RecordingEmailDelivery()
        limiter = AllowAllRateLimiter()
        app = create_app(
            engine=engine,
            public_origin="https://testserver",
            email_delivery=mailer,
            rate_limiter=limiter,
        )
        client = TestClient(
            app,
            base_url="https://testserver",
            raise_server_exceptions=False,
        )
        yield client, engine, mailer, limiter, app
        client.close()
    finally:
        if safe_database:
            Base.metadata.drop_all(engine)
            with engine.begin() as connection:
                connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
        engine.dispose()


def _request_headers():
    return {"Origin": "https://testserver"}


def _register(
    client,
    *,
    email="worker@example.test",
    password="secure-test-account-password",
):
    return client.post(
        "/auth/register",
        json={"email": email, "password": password},
        headers=_request_headers(),
    )


def _verify(client, token):
    return client.post(
        "/auth/email-verification/confirm",
        json={"token": token},
        headers=_request_headers(),
    )


def _request_reset(client, email, csrf_token=None):
    headers = _request_headers()
    if csrf_token is not None:
        headers["X-CSRF-Token"] = csrf_token
    return client.post(
        "/auth/password-reset/request",
        json={"email": email},
        headers=headers,
    )


def _complete_reset(
    client,
    token,
    password="a-new-secure-password",
    csrf_token=None,
):
    headers = _request_headers()
    if csrf_token is not None:
        headers["X-CSRF-Token"] = csrf_token
    return client.post(
        "/auth/password-reset/confirm",
        json={"token": token, "new_password": password},
        headers=headers,
    )


def _login(
    client,
    email="worker@example.test",
    password="secure-test-account-password",
):
    return client.post(
        "/auth/login",
        json={"email": email, "password": password},
        headers=_request_headers(),
    )


def test_registration_is_disabled_without_delivery_and_shared_rate_limiters(
    monkeypatch,
):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    engine = create_engine("sqlite://")
    app = create_app(
        engine=engine,
        public_origin="https://testserver",
    )
    client = TestClient(app, raise_server_exceptions=False)
    try:
        response = _register(client)
    finally:
        client.close()
        engine.dispose()

    assert response.status_code == 503
    assert response.json() == {
        "error": {"code": "RATE_LIMITING_UNAVAILABLE"}
    }


def test_registration_is_suppressed_by_shared_rate_limiter(monkeypatch):
    engine = create_engine("sqlite://")
    mailer = RecordingEmailDelivery()
    app = create_app(
        engine=engine,
        public_origin="https://testserver",
        email_delivery=mailer,
        rate_limiter=AllowAllRateLimiter(allow_requests=False),
    )
    client = TestClient(app, raise_server_exceptions=False)
    try:
        response = _register(client)
    finally:
        client.close()
        engine.dispose()

    assert response.status_code == 202
    assert response.json() == {"message": "If eligible, an email will be sent."}
    assert mailer.messages == []


def test_registration_rejects_client_supplied_identity(account_api):
    client, _, _, _, _ = account_api
    response = client.post(
        "/auth/register",
        json={
            "email": "worker@example.test",
            "password": "secure-test-account-password",
            "user_id": "client-chosen-id",
        },
        headers=_request_headers(),
    )

    assert response.status_code == 400
    assert response.json() == {"error": {"code": "INVALID_REQUEST"}}


def test_registration_creates_unverified_user_and_emits_only_one_use_token(
    account_api, caplog
):
    client, engine, mailer, limiter, _ = account_api
    response = _register(client)

    assert response.status_code == 202
    assert response.json() == {"message": "If eligible, an email will be sent."}
    assert len(mailer.messages) == 1
    purpose, address, token = mailer.messages[0]
    assert purpose == "verification"
    assert address == "worker@example.test"
    assert len(token) == 43
    assert _login(client).status_code == 401
    with Session(engine) as session:
        user = session.scalars(select(UserRecord)).one()
        action_token = session.scalars(select(AccountActionTokenRecord)).one()
    assert user.email_verified_at is None
    assert token not in action_token.token_digest
    assert action_token.purpose == "verify_email"
    assert action_token.user_id == user.id
    assert action_token.activated_at is not None
    assert Argon2PasswordHasher().verify(
        user.password_hash,
        "secure-test-account-password",
    )
    assert user.password_hash not in response.text
    assert "secure-test-account-password" not in response.text
    assert "secure-test-account-password" not in caplog.text
    assert limiter.scopes == [
        "register:email",
        "register:ip",
        "login:email",
        "login:ip",
    ]


def test_duplicate_registration_and_resend_are_enumeration_safe(account_api):
    client, _, mailer, _, _ = account_api
    first = _register(client)
    original_token = mailer.messages[-1][2]
    before_duplicate = len(mailer.messages)
    duplicate = _register(client)
    known = client.post(
        "/auth/email-verification/request",
        json={"email": "worker@example.test"},
        headers=_request_headers(),
    )
    unknown = client.post(
        "/auth/email-verification/request",
        json={"email": "unknown@example.test"},
        headers=_request_headers(),
    )

    assert first.status_code == duplicate.status_code == 202
    assert known.status_code == unknown.status_code == 202
    assert first.json() == duplicate.json() == known.json() == unknown.json()
    assert len(mailer.messages) == before_duplicate + 1
    assert _verify(client, original_token).status_code == 400


def test_verification_rejects_malformed_expired_and_reused_tokens(account_api):
    client, engine, mailer, _, _ = account_api
    _register(client)
    token = mailer.messages[-1][2]

    malformed = _verify(client, "not-a-valid-token")
    verified = _verify(client, token)
    reused = _verify(client, token)
    _register(client, email="expired@example.test")
    expired_token = mailer.messages[-1][2]
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE account_action_tokens "
                "SET created_at = CURRENT_TIMESTAMP - INTERVAL '2 seconds', "
                "expires_at = CURRENT_TIMESTAMP - INTERVAL '1 second' "
                "WHERE token_digest = :digest"
            ),
            {"digest": hashlib.sha256(expired_token.encode("ascii")).hexdigest()},
        )
    expired = _verify(client, expired_token)

    assert malformed.status_code == 400
    assert verified.status_code == 204
    assert reused.status_code == 400
    assert expired.status_code == 400
    assert _login(client).status_code == 200


def test_password_reset_request_is_generic_and_reset_revokes_sessions(account_api):
    client, engine, mailer, _, _ = account_api
    _register(client)
    _verify(client, mailer.messages[-1][2])
    login = _login(client)
    assert login.status_code == 200
    csrf_token = login.json()["csrf_token"]
    reset_known = _request_reset(client, "worker@example.test", csrf_token)
    token = mailer.messages[-1][2]
    reset_unknown = _request_reset(client, "missing@example.test", csrf_token)
    invalid = _complete_reset(client, "malformed", csrf_token=csrf_token)
    completed = _complete_reset(client, token, csrf_token=csrf_token)
    reused = _complete_reset(client, token)

    assert reset_known.status_code == reset_unknown.status_code == 202
    assert reset_known.json() == reset_unknown.json()
    assert invalid.status_code == 400
    assert completed.status_code == 204
    assert reused.status_code == 400
    assert _login(client).status_code == 401
    with Session(engine) as session:
        sessions_after_reset = session.scalars(select(UserSessionRecord)).all()
    assert sessions_after_reset
    assert all(item.revoked_at is not None for item in sessions_after_reset)
    new_login = _login(client, password="a-new-secure-password")
    assert new_login.status_code == 200
    with Session(engine) as session:
        sessions = session.scalars(select(UserSessionRecord)).all()
    assert sessions
    assert any(item.revoked_at is not None for item in sessions)
    assert csrf_token not in repr(sessions)


def test_authenticated_account_lifecycle_requests_require_csrf(account_api):
    client, _, mailer, _, _ = account_api
    _register(client)
    _verify(client, mailer.messages[-1][2])
    login = _login(client)
    csrf_token = login.json()["csrf_token"]

    missing_csrf = client.post(
        "/auth/password-reset/request",
        json={"email": "worker@example.test"},
        headers=_request_headers(),
    )
    wrong_origin = client.post(
        "/auth/password-reset/request",
        json={"email": "worker@example.test"},
        headers={
            "Origin": "https://attacker.example",
            "X-CSRF-Token": csrf_token,
        },
    )
    accepted = _request_reset(client, "worker@example.test", csrf_token)

    assert missing_csrf.status_code == 403
    assert wrong_origin.status_code == 403
    assert accepted.status_code == 202


def test_password_reset_rejects_expired_token(account_api):
    client, engine, mailer, _, _ = account_api
    _register(client)
    _verify(client, mailer.messages[-1][2])
    _request_reset(client, "worker@example.test")
    token = mailer.messages[-1][2]
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE account_action_tokens "
                "SET created_at = CURRENT_TIMESTAMP - INTERVAL '2 seconds', "
                "expires_at = CURRENT_TIMESTAMP - INTERVAL '1 second' "
                "WHERE token_digest = :digest"
            ),
            {"digest": hashlib.sha256(token.encode("ascii")).hexdigest()},
        )

    response = _complete_reset(client, token)

    assert response.status_code == 400
    assert response.json() == {
        "error": {"code": "INVALID_ACCOUNT_ACTION_TOKEN"}
    }


def test_reset_token_cannot_be_consumed_twice_concurrently(account_api):
    client, _, mailer, _, app = account_api
    _register(client)
    _verify(client, mailer.messages[-1][2])
    _request_reset(client, "worker@example.test")
    token = mailer.messages[-1][2]

    def submit_reset():
        with TestClient(
            app,
            base_url="https://testserver",
            raise_server_exceptions=False,
        ) as another_client:
            return _complete_reset(another_client, token)

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: submit_reset(), range(2)))

    assert sorted(response.status_code for response in responses) == [204, 400]


def test_delivery_failure_keeps_account_unverified_and_revokes_token(account_api):
    client, engine, mailer, _, _ = account_api
    mailer.failure = True

    response = _register(client)

    assert response.status_code == 202
    assert response.json() == {"message": "If eligible, an email will be sent."}
    assert "token" not in response.text.lower()
    with Session(engine) as session:
        user = session.scalars(select(UserRecord)).one()
        action_token = session.scalars(select(AccountActionTokenRecord)).one()
    assert user.email_verified_at is None
    assert action_token.revoked_at is not None
    assert action_token.activated_at is None
    assert action_token.consumed_at is None
    failed_token = mailer.messages[-1][2]
    assert _verify(client, failed_token).status_code == 400


def test_reset_delivery_failure_has_same_generic_response_and_revokes_token(
    account_api, caplog
):
    client, engine, mailer, _, _ = account_api
    _register(client)
    _verify(client, mailer.messages[-1][2])
    mailer.failure = True

    failed_response = _request_reset(client, "worker@example.test")
    failed_token = mailer.messages[-1][2]
    unknown_response = _request_reset(client, "unknown@example.test")

    assert failed_response.status_code == unknown_response.status_code == 202
    assert failed_response.json() == unknown_response.json()
    assert "worker@example.test" not in failed_response.text
    assert failed_token not in failed_response.text
    with Session(engine) as session:
        reset_token = session.scalars(
            select(AccountActionTokenRecord).where(
                AccountActionTokenRecord.purpose == "reset_password"
            )
        ).one()
    assert reset_token.revoked_at is not None
    assert reset_token.activated_at is None
    assert "worker@example.test" not in caplog.text
    assert failed_token not in caplog.text
    assert "mail failure with token" not in caplog.text


def test_activation_failure_keeps_delivered_token_unusable(account_api):
    client, engine, mailer, _, _ = account_api
    suffix = uuid4().hex
    function_name = f"reject_token_activation_{suffix}"
    trigger_name = f"reject_token_activation_{suffix}"
    with engine.begin() as connection:
        connection.exec_driver_sql(
            f"CREATE FUNCTION {function_name}() RETURNS trigger LANGUAGE plpgsql "
            "AS $$ BEGIN "
            "IF NEW.activated_at IS NOT NULL THEN "
            "RAISE EXCEPTION 'sensitive token activation detail'; "
            "END IF; RETURN NEW; END $$"
        )
        connection.exec_driver_sql(
            f"CREATE TRIGGER {trigger_name} BEFORE UPDATE ON account_action_tokens "
            f"FOR EACH ROW EXECUTE FUNCTION {function_name}()"
        )

    try:
        response = _register(client)
        assert response.status_code == 503
        assert response.json() == {
            "error": {"code": "ACCOUNT_LIFECYCLE_UNAVAILABLE"}
        }
        assert "sensitive token activation detail" not in response.text
        assert len(mailer.messages) == 1
        with Session(engine) as session:
            action_token = session.scalars(
                select(AccountActionTokenRecord)
            ).one()
            user = session.scalars(select(UserRecord)).one()
        assert action_token.activated_at is None
        assert action_token.consumed_at is None
        assert user.email_verified_at is None
        assert _verify(client, mailer.messages[0][2]).status_code == 400
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                f"DROP TRIGGER IF EXISTS {trigger_name} ON account_action_tokens"
            )
            connection.exec_driver_sql(f"DROP FUNCTION IF EXISTS {function_name}()")


def test_reset_database_failure_rolls_back_token_password_and_revocation(
    account_api, caplog
):
    client, engine, mailer, _, _ = account_api
    _register(client)
    _verify(client, mailer.messages[-1][2])
    login = _login(client)
    csrf_token = login.json()["csrf_token"]
    _request_reset(client, "worker@example.test", csrf_token)
    reset_token = mailer.messages[-1][2]
    suffix = uuid4().hex
    function_name = f"reject_user_update_{suffix}"
    trigger_name = f"reject_user_update_{suffix}"
    with engine.begin() as connection:
        connection.exec_driver_sql(
            f"CREATE FUNCTION {function_name}() RETURNS trigger LANGUAGE plpgsql "
            "AS $$ BEGIN RAISE EXCEPTION 'sensitive user update detail'; END $$"
        )
        connection.exec_driver_sql(
            f"CREATE TRIGGER {trigger_name} BEFORE UPDATE ON users "
            f"FOR EACH ROW EXECUTE FUNCTION {function_name}()"
        )

    try:
        response = _complete_reset(
            client,
            reset_token,
            csrf_token=csrf_token,
        )
        assert response.status_code == 503
        assert response.json() == {
            "error": {"code": "ACCOUNT_LIFECYCLE_UNAVAILABLE"}
        }
        assert "sensitive user update detail" not in response.text
        assert reset_token not in caplog.text
        assert "secure-test-account-password" not in caplog.text
        assert "sensitive user update detail" not in caplog.text
        with Session(engine) as session:
            action_token = session.scalars(
                select(AccountActionTokenRecord).where(
                    AccountActionTokenRecord.purpose == "reset_password"
                )
            ).one()
            stored_user = session.scalars(select(UserRecord)).one()
            sessions = session.scalars(select(UserSessionRecord)).all()
        assert action_token.consumed_at is None
        assert action_token.activated_at is not None
        assert stored_user.email_verified_at is not None
        assert Argon2PasswordHasher().verify(
            stored_user.password_hash,
            "secure-test-account-password",
        )
        assert all(item.revoked_at is None for item in sessions)
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                f"DROP TRIGGER IF EXISTS {trigger_name} ON users"
            )
            connection.exec_driver_sql(f"DROP FUNCTION IF EXISTS {function_name}()")

    assert _login(client).status_code == 200


def test_database_failure_does_not_send_unpersisted_action_token(account_api):
    client, engine, mailer, _, _ = account_api
    suffix = uuid4().hex
    function_name = f"reject_account_token_{suffix}"
    trigger_name = f"reject_account_token_{suffix}"
    with engine.begin() as connection:
        connection.exec_driver_sql(
            f"CREATE FUNCTION {function_name}() RETURNS trigger LANGUAGE plpgsql "
            "AS $$ BEGIN RAISE EXCEPTION 'sensitive token persistence detail'; END $$"
        )
        connection.exec_driver_sql(
            f"CREATE TRIGGER {trigger_name} BEFORE INSERT ON account_action_tokens "
            f"FOR EACH ROW EXECUTE FUNCTION {function_name}()"
        )

    try:
        response = _register(client)
        assert response.status_code == 503
        assert response.json() == {
            "error": {"code": "ACCOUNT_LIFECYCLE_UNAVAILABLE"}
        }
        assert "sensitive token persistence detail" not in response.text
        assert mailer.messages == []
        with Session(engine) as session:
            assert session.scalars(select(UserRecord)).all() == []
            assert session.scalars(select(AccountActionTokenRecord)).all() == []
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                f"DROP TRIGGER IF EXISTS {trigger_name} ON account_action_tokens"
            )
            connection.exec_driver_sql(f"DROP FUNCTION IF EXISTS {function_name}()")


def test_email_addresses_are_not_logged_or_returned_during_recovery(account_api):
    client, _, _, _, _ = account_api
    response = _request_reset(client, "secret@example.test")

    assert response.status_code == 202
    assert "secret@example.test" not in response.text
