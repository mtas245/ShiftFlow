import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.infrastructure.persistence.models import (
    Base,
    CompanyMembershipRecord,
    CompanyRecord,
    UserRecord,
    UserSessionRecord,
)
from app.application.authenticate_user import token_digest
from app.interfaces.main import create_app


pytestmark = pytest.mark.postgres

_USER_REVISION = "91d4e6a8c3f2"


class RecordingEmailDelivery:
    def __init__(self):
        self.messages: list[tuple[str, str, str]] = []

    def send_verification(self, email: str, token: str) -> None:
        self.messages.append(("verification", email, token))

    def send_password_reset(self, email: str, token: str) -> None:
        self.messages.append(("password_reset", email, token))


class AllowAllRateLimiter:
    def allow(self, _scope: str, _key: str) -> bool:
        return True


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


def _upgrade(revision):
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=_repository_root(),
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def authentication_api(monkeypatch):
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
        _upgrade(_USER_REVISION)
        _upgrade("head")
        mailer = RecordingEmailDelivery()
        app = create_app(
            engine=engine,
            public_origin="https://testserver",
            email_delivery=mailer,
            rate_limiter=AllowAllRateLimiter(),
        )
        client = TestClient(
            app,
            base_url="https://testserver",
            raise_server_exceptions=False,
        )
        yield client, engine, app, mailer
        client.close()
    finally:
        if safe_database:
            Base.metadata.drop_all(engine)
            with engine.begin() as connection:
                connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
        engine.dispose()


def _create_verified_user(client, engine, *, email="worker@example.test"):
    registration = client.post(
        "/auth/register",
        json={"email": email, "password": "secure-test-account-password"},
        headers={"Origin": "https://testserver"},
    )
    assert registration.status_code == 202
    mailer = client.app.state.email_delivery
    token = mailer.messages[-1][2]
    verified = client.post(
        "/auth/email-verification/confirm",
        json={"token": token},
        headers={"Origin": "https://testserver"},
    )
    assert verified.status_code == 204
    with engine.connect() as connection:
        user = connection.execute(
            select(UserRecord.id, UserRecord.email).where(
                UserRecord.email == email.lower()
            )
        ).one()
    return user


def _register_unverified_user(client, *, email):
    return client.post(
        "/auth/register",
        json={"email": email, "password": "secure-test-account-password"},
        headers={"Origin": "https://testserver"},
    )


def _login(client, email="worker@example.test", csrf_token=None):
    headers = {"Origin": "https://testserver"}
    if csrf_token is not None:
        headers["X-CSRF-Token"] = csrf_token
    return client.post(
        "/auth/login",
        json={"email": email, "password": "secure-test-account-password"},
        headers=headers,
    )


def test_login_uses_generic_failure_for_unknown_wrong_and_unverified_accounts(
    authentication_api,
):
    client, engine, _, _ = authentication_api
    _create_verified_user(client, engine)
    _register_unverified_user(client, email="unverified@example.test")

    wrong_password = client.post(
        "/auth/login",
        json={"email": "worker@example.test", "password": "wrong-password"},
        headers={"Origin": "https://testserver"},
    )
    unknown_account = client.post(
        "/auth/login",
        json={"email": "missing@example.test", "password": "wrong-password"},
        headers={"Origin": "https://testserver"},
    )
    unverified_account = client.post(
        "/auth/login",
        json={
            "email": "unverified@example.test",
            "password": "secure-test-account-password",
        },
        headers={"Origin": "https://testserver"},
    )

    assert {
        response.status_code for response in (
            wrong_password,
            unknown_account,
            unverified_account,
        )
    } == {401}
    assert {
        response.text for response in (
            wrong_password,
            unknown_account,
            unverified_account,
        )
    } == {'{"error":{"code":"AUTHENTICATION_FAILED"}}'}
    assert "missing@example.test" not in unknown_account.text
    assert "unverified@example.test" not in unverified_account.text


def test_login_sets_secure_opaque_cookie_and_returns_session_bound_csrf_token(
    authentication_api,
):
    client, engine, _, _ = authentication_api
    user = _create_verified_user(client, engine)

    response = _login(client)
    cookie = response.headers["set-cookie"]
    body = response.json()

    assert response.status_code == 200
    assert set(body) == {"csrf_token"}
    assert body["csrf_token"]
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/" in cookie
    assert "Domain=" not in cookie
    assert "__Host-shiftflow_session=" in cookie
    assert response.headers["cache-control"] == "no-store"
    assert "password_hash" not in response.text
    with Session(engine) as session_factory:
        session = session_factory.scalars(select(UserSessionRecord)).one()
    with engine.connect() as connection:
        stored_user_id = connection.scalar(
            select(UserSessionRecord.user_id)
        )
    assert stored_user_id == user.id
    assert client.cookies.get("__Host-shiftflow_session") not in session.token_hash
    assert session.token_hash != body["csrf_token"]
    assert session.csrf_token_hash


def test_cookie_authenticated_mutation_requires_csrf_and_matching_origin(
    authentication_api,
):
    client, engine, _, _ = authentication_api
    user = _create_verified_user(client, engine)
    login = _login(client)
    csrf_token = login.json()["csrf_token"]
    payload = {
        "company_name": "Synthetic Cafe",
        "business_email": "contact@example.test",
        "business_phone": "+41791234567",
        "business_address": "1 Test Street",
        "industry": "Hospitality",
        "display_name": "Company Owner",
    }

    missing_token = client.post(
        "/companies",
        json=payload,
        headers={"Origin": "https://testserver"},
    )
    bad_origin = client.post(
        "/companies",
        json=payload,
        headers={
            "Origin": "https://attacker.example",
            "X-CSRF-Token": csrf_token,
        },
    )
    accepted = client.post(
        "/companies",
        json=payload,
        headers={
            "Origin": "https://testserver",
            "X-CSRF-Token": csrf_token,
        },
    )

    assert missing_token.status_code == 403
    assert missing_token.json() == {"error": {"code": "CSRF_FAILED"}}
    assert bad_origin.status_code == 403
    assert bad_origin.json() == {"error": {"code": "CSRF_FAILED"}}
    assert accepted.status_code == 201
    with engine.connect() as connection:
        membership_user_id = connection.scalar(
            select(CompanyMembershipRecord.user_id)
        )
        session_user_id = connection.scalar(select(UserSessionRecord.user_id))
    assert membership_user_id == session_user_id == user.id


def test_login_rotates_prior_session_and_csrf_token_is_session_bound(
    authentication_api,
):
    client, engine, _, _ = authentication_api
    _create_verified_user(client, engine)
    first_login = _login(client)
    first_csrf = first_login.json()["csrf_token"]
    first_token = client.cookies.get("__Host-shiftflow_session")

    second_login = _login(client, csrf_token=first_csrf)
    second_csrf = second_login.json()["csrf_token"]
    second_token = client.cookies.get("__Host-shiftflow_session")
    stale_csrf = client.post(
        "/companies",
        json={
            "company_name": "Stale Token Cafe",
            "business_email": "contact@example.test",
            "business_phone": "+41791234567",
            "business_address": "1 Test Street",
            "industry": "Hospitality",
        },
        headers={"Origin": "https://testserver", "X-CSRF-Token": first_csrf},
    )

    assert first_login.status_code == second_login.status_code == 200
    assert first_token != second_token
    assert first_csrf != second_csrf
    assert stale_csrf.status_code == 403
    with Session(engine) as session_factory:
        sessions = session_factory.scalars(
            select(UserSessionRecord).order_by(UserSessionRecord.created_at)
        ).all()
    assert len(sessions) == 2
    assert sessions[0].revoked_at is not None
    assert sessions[1].revoked_at is None


def test_logout_revokes_session_and_clears_cookie(authentication_api):
    client, engine, _, _ = authentication_api
    _create_verified_user(client, engine)
    login = _login(client)
    csrf_token = login.json()["csrf_token"]

    logout = client.post(
        "/auth/logout",
        headers={
            "Origin": "https://testserver",
            "X-CSRF-Token": csrf_token,
        },
    )
    protected = client.get("/companies/missing-company/shifts")

    assert logout.status_code == 204
    assert "__Host-shiftflow_session=" in logout.headers["set-cookie"]
    assert protected.status_code == 401
    assert protected.json() == {"error": {"code": "AUTHENTICATION_REQUIRED"}}
    with Session(engine) as session_factory:
        session = session_factory.scalars(select(UserSessionRecord)).one()
    assert session.revoked_at is not None


def test_login_origin_is_checked_and_client_identity_fields_are_rejected(
    authentication_api,
):
    client, engine, _, _ = authentication_api
    _create_verified_user(client, engine)

    wrong_origin = client.post(
        "/auth/login",
        json={"email": "worker@example.test", "password": "secure-test-account-password"},
        headers={"Origin": "https://attacker.example"},
    )
    missing_origin = client.post(
        "/auth/login",
        json={
            "email": "worker@example.test",
            "password": "secure-test-account-password",
        },
    )
    client_identity = client.post(
        "/auth/login",
        json={
            "email": "worker@example.test",
            "password": "secure-test-account-password",
            "user_id": "victim",
        },
        headers={"Origin": "https://testserver"},
    )

    assert wrong_origin.status_code == 403
    assert wrong_origin.json() == {"error": {"code": "CSRF_FAILED"}}
    assert missing_origin.status_code == 403
    assert missing_origin.json() == {"error": {"code": "CSRF_FAILED"}}
    assert client_identity.status_code == 400
    assert client_identity.json() == {"error": {"code": "INVALID_REQUEST"}}
    with engine.connect() as connection:
        assert connection.scalar(select(UserSessionRecord.id)) is None


def test_malformed_session_cookie_fails_closed(authentication_api):
    client, _, _, _ = authentication_api

    response = client.get(
        "/companies/missing-company/shifts",
        headers={"Cookie": "__Host-shiftflow_session=malformed"},
    )

    assert response.status_code == 401
    assert response.json() == {
        "error": {"code": "AUTHENTICATION_REQUIRED"}
    }


def test_session_database_failure_is_safe_and_does_not_create_session(
    authentication_api,
):
    client, engine, _, _ = authentication_api
    _create_verified_user(client, engine)
    suffix = uuid4().hex
    function_name = f"reject_auth_session_{suffix}"
    trigger_name = f"reject_auth_session_{suffix}"
    with engine.begin() as connection:
        connection.exec_driver_sql(
            f"CREATE FUNCTION {function_name}() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN "
            "RAISE EXCEPTION 'sensitive session persistence detail'; END $$"
        )
        connection.exec_driver_sql(
            f"CREATE TRIGGER {trigger_name} BEFORE INSERT ON user_sessions "
            f"FOR EACH ROW EXECUTE FUNCTION {function_name}()"
        )

    try:
        response = _login(client)

        assert response.status_code == 503
        assert response.json() == {
            "error": {"code": "AUTHENTICATION_UNAVAILABLE"}
        }
        assert "sensitive session persistence detail" not in response.text
        assert "secure-test-account-password" not in response.text
        with engine.connect() as connection:
            assert connection.scalar(select(UserSessionRecord.id)) is None
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                f"DROP TRIGGER IF EXISTS {trigger_name} ON user_sessions"
            )
            connection.exec_driver_sql(f"DROP FUNCTION IF EXISTS {function_name}()")


def test_new_account_does_not_inherit_legacy_membership_or_tenant_access(
    authentication_api,
):
    client, engine, _, _ = authentication_api
    with engine.begin() as connection:
        connection.execute(
            CompanyRecord.__table__.insert().values(
                id="legacy-company",
                company_name="Legacy Test Company",
                business_email="legacy@example.test",
                business_phone="+41791234567",
                business_address="1 Test Street",
                industry="Hospitality",
                joining_code="LEGACY01",
            )
        )
        connection.execute(
            CompanyMembershipRecord.__table__.insert().values(
                company_id="legacy-company",
                user_id="legacy-auth-provider-id",
                role="employee",
            )
        )
    user = _create_verified_user(client, engine)
    with engine.connect() as connection:
        membership = connection.execute(
            select(
                CompanyMembershipRecord.user_id,
                CompanyMembershipRecord.role,
            )
        ).one()
        has_account_membership = connection.scalar(
            select(CompanyMembershipRecord.id).where(
                CompanyMembershipRecord.user_id == user.id
            )
        )

    login = _login(client)
    cross_tenant = client.get(
        "/companies/legacy-company/shifts",
        params={"from": "2026-10-28T00:00:00Z", "to": "2026-10-29T00:00:00Z"},
    )

    assert membership == ("legacy-auth-provider-id", "employee")
    assert has_account_membership is None
    assert login.status_code == 200
    assert cross_tenant.status_code == 404
    assert cross_tenant.json() == {"error": {"code": "RESOURCE_NOT_FOUND"}}


@pytest.mark.parametrize("expiration", ["idle", "absolute"])
def test_expired_session_is_rejected(authentication_api, expiration):
    client, engine, _, _ = authentication_api
    _create_verified_user(client, engine)
    _login(client)
    if expiration == "idle":
        assignment = "last_seen_at = CURRENT_TIMESTAMP - INTERVAL '31 minutes'"
    else:
        assignment = "created_at = CURRENT_TIMESTAMP - INTERVAL '13 hours'"
    with engine.begin() as connection:
        connection.exec_driver_sql(
            f"UPDATE user_sessions SET {assignment}"
        )

    response = client.get("/companies/missing-company/shifts")

    assert response.status_code == 401
    assert response.json() == {"error": {"code": "AUTHENTICATION_REQUIRED"}}


def _bootstrap_session(client, origin="https://testserver"):
    return client.post(
        "/auth/session/bootstrap",
        headers={"Origin": origin},
    )


def _csrf_state(engine):
    with engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT csrf_token_hash, previous_csrf_token_hash, "
                "previous_csrf_expires_at FROM user_sessions"
            )
        ).one()


def test_session_bootstrap_rotates_csrf_without_requiring_old_token(
    authentication_api,
):
    client, engine, _, _ = authentication_api
    user = _create_verified_user(client, engine)
    login = _login(client)
    old_token = login.json()["csrf_token"]
    session_cookie = client.cookies.get("__Host-shiftflow_session")

    response = _bootstrap_session(client)

    assert response.status_code == 200
    assert set(response.json()) == {"csrf_token"}
    assert response.json()["csrf_token"] != old_token
    assert response.headers["cache-control"] == "no-store"
    assert "cookie" in response.headers["vary"].lower()
    assert "__Host-shiftflow_session" not in response.headers.get("set-cookie", "")
    assert client.cookies.get("__Host-shiftflow_session") == session_cookie
    assert old_token not in response.text
    assert session_cookie not in response.text
    assert "worker@example.test" not in response.text
    assert user.id not in response.text

    current_hash, previous_hash, previous_expires_at = _csrf_state(engine)
    assert current_hash == token_digest(response.json()["csrf_token"])
    assert previous_hash == token_digest(old_token)
    assert previous_expires_at is not None


@pytest.mark.parametrize(
    "session_state", ["missing", "malformed", "expired", "revoked"]
)
def test_session_bootstrap_rejects_invalid_expired_and_revoked_sessions(
    authentication_api, session_state
):
    client, engine, _, _ = authentication_api
    if session_state in {"expired", "revoked"}:
        _create_verified_user(client, engine)
        _login(client)
        with engine.begin() as connection:
            if session_state == "expired":
                connection.execute(
                    text(
                        "UPDATE user_sessions SET last_seen_at = "
                        "CURRENT_TIMESTAMP - INTERVAL '31 minutes'"
                    )
                )
            else:
                connection.execute(
                    text(
                        "UPDATE user_sessions SET revoked_at = CURRENT_TIMESTAMP"
                    )
                )

    if session_state == "malformed":
        response = client.post(
            "/auth/session/bootstrap",
            headers={
                "Origin": "https://testserver",
                "Cookie": "__Host-shiftflow_session=malformed",
            },
        )
    else:
        response = _bootstrap_session(client)

    assert response.status_code == 401
    assert response.json() == {
        "error": {"code": "AUTHENTICATION_REQUIRED"}
    }
    if session_state != "missing":
        assert "__Host-shiftflow_session=" in response.headers.get("set-cookie", "")


def test_company_discovery_returns_only_account_linked_memberships(
    authentication_api,
):
    client_a, engine, app, _ = authentication_api
    account_a = _create_verified_user(client_a, engine, email="a@example.test")
    login_a = _login(client_a, email="a@example.test")
    csrf_a = login_a.json()["csrf_token"]
    client_b = TestClient(
        app,
        base_url="https://testserver",
        raise_server_exceptions=False,
    )
    try:
        account_b = _create_verified_user(
            client_b, engine, email="b@example.test"
        )
        login_b = _login(client_b, email="b@example.test")

        created = client_a.post(
            "/companies",
            json={
                "company_name": "Principal Cafe",
                "business_email": "contact@example.test",
                "business_phone": "+41791234567",
                "business_address": "1 Test Street",
                "industry": "Hospitality",
                "display_name": "Owner A",
            },
            headers={
                "Origin": "https://testserver",
                "X-CSRF-Token": csrf_a,
            },
        )
        assert created.status_code == 201
        with engine.begin() as connection:
            connection.execute(
                CompanyRecord.__table__.insert().values(
                    id="unlinked-legacy-company",
                    company_name="Unlinked Legacy Cafe",
                    business_email="legacy@example.test",
                    business_phone="+41791234568",
                    business_address="2 Test Street",
                    industry="Hospitality",
                    joining_code="LEGACY02",
                )
            )
            connection.execute(
                text(
                    "INSERT INTO company_memberships "
                    "(company_id, user_id, role, account_user_id, public_id, "
                    "display_name) VALUES "
                    "('unlinked-legacy-company', :legacy_id, 'employee', "
                    "NULL, :public_id, NULL)"
                ),
                {
                    "legacy_id": account_a.id,
                    "public_id": str(uuid4()),
                },
            )

        memberships_a = client_a.get("/companies/mine")
        memberships_b = client_b.get("/companies/mine")
        join_b = client_b.post(
            "/companies/join",
            json={
                "joining_code": created.json()["joining_code"],
                "display_name": "Employee B",
            },
            headers={
                "Origin": "https://testserver",
                "X-CSRF-Token": login_b.json()["csrf_token"],
            },
        )
        memberships_b_after_join = client_b.get("/companies/mine")
        memberships_a_after_join = client_a.get("/companies/mine")

        assert memberships_a.status_code == 200
        assert memberships_b.status_code == 200
        assert memberships_a.json()["items"] == [
            {
                "membership_id": memberships_a.json()["items"][0]["membership_id"],
                "company_id": created.json()["company_id"],
                "name": "Principal Cafe",
                "role": "administrator",
            }
        ]
        assert memberships_b.json() == {"items": [], "next_cursor": None}
        assert join_b.status_code == 201
        assert memberships_b_after_join.json()["items"][0]["role"] == "employee"
        assert memberships_a_after_join.json()["items"][0]["role"] == "administrator"
        for response in (
            memberships_a,
            memberships_b,
            memberships_b_after_join,
            memberships_a_after_join,
        ):
            assert account_a.id not in response.text
            assert account_b.id not in response.text
            assert "a@example.test" not in response.text
            assert "b@example.test" not in response.text
            assert "opaque-legacy" not in response.text
            assert "user_id" not in response.text
        assert "unlinked-legacy-company" not in memberships_a.text
    finally:
        client_b.close()


@pytest.mark.parametrize("origin", [None, "https://attacker.example"])
def test_session_bootstrap_requires_exact_configured_origin(
    authentication_api, origin
):
    client, engine, _, _ = authentication_api
    _create_verified_user(client, engine)
    login = _login(client)
    old_token = login.json()["csrf_token"]

    response = client.post(
        "/auth/session/bootstrap",
        headers={} if origin is None else {"Origin": origin},
    )

    assert response.status_code == 403
    assert response.json() == {"error": {"code": "CSRF_FAILED"}}
    assert response.headers["cache-control"] == "no-store"
    assert "cookie" in response.headers["vary"].lower()
    assert _csrf_state(engine).csrf_token_hash == token_digest(old_token)


def test_previous_csrf_token_has_five_minute_grace_and_only_one_generation(
    authentication_api,
):
    client, engine, _, _ = authentication_api
    _create_verified_user(client, engine)
    first_login = _login(client)
    csrf_1 = first_login.json()["csrf_token"]

    first_bootstrap = _bootstrap_session(client)
    csrf_2 = first_bootstrap.json()["csrf_token"]
    first_deadline = _csrf_state(engine).previous_csrf_expires_at
    with engine.connect() as connection:
        first_now = connection.scalar(text("SELECT CURRENT_TIMESTAMP"))
    assert first_bootstrap.status_code == 200
    assert first_deadline is not None
    assert timedelta(minutes=4, seconds=59) <= first_deadline - first_now
    assert first_deadline - first_now <= timedelta(minutes=5, seconds=1)

    csrf_1_request = client.post(
        "/auth/register",
        json={"email": "grace-one@example.test", "password": "secure-test-account-password"},
        headers={
            "Origin": "https://testserver",
            "X-CSRF-Token": csrf_1,
        },
    )
    second_bootstrap = _bootstrap_session(client)
    csrf_3 = second_bootstrap.json()["csrf_token"]
    csrf_1_after_rotation = client.post(
        "/auth/register",
        json={"email": "grace-two@example.test", "password": "secure-test-account-password"},
        headers={
            "Origin": "https://testserver",
            "X-CSRF-Token": csrf_1,
        },
    )
    csrf_2_request = client.post(
        "/auth/register",
        json={"email": "grace-three@example.test", "password": "secure-test-account-password"},
        headers={
            "Origin": "https://testserver",
            "X-CSRF-Token": csrf_2,
        },
    )
    current_hash, previous_hash, previous_expires_at = _csrf_state(engine)
    with engine.connect() as connection:
        second_now = connection.scalar(text("SELECT CURRENT_TIMESTAMP"))

    assert csrf_1_request.status_code == 202
    assert second_bootstrap.status_code == 200
    assert csrf_1_after_rotation.status_code == 403
    assert csrf_2_request.status_code == 202
    assert csrf_3 != csrf_2
    assert current_hash == token_digest(csrf_3)
    assert previous_hash == token_digest(csrf_2)
    assert previous_expires_at > first_deadline
    assert timedelta(minutes=4, seconds=59) <= previous_expires_at - second_now
    assert previous_expires_at - second_now <= timedelta(minutes=5, seconds=1)

    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE user_sessions SET previous_csrf_expires_at = "
                "CURRENT_TIMESTAMP - INTERVAL '1 second'"
            )
        )
    expired_grace = client.post(
        "/auth/register",
        json={
            "email": "expired-grace@example.test",
            "password": "secure-test-account-password",
        },
        headers={
            "Origin": "https://testserver",
            "X-CSRF-Token": csrf_2,
        },
    )
    assert expired_grace.status_code == 403


def test_session_bootstrap_database_failure_rolls_back_csrf_rotation(
    authentication_api,
):
    client, engine, _, _ = authentication_api
    _create_verified_user(client, engine)
    login = _login(client)
    old_token = login.json()["csrf_token"]
    suffix = uuid4().hex
    function_name = f"reject_csrf_rotation_{suffix}"
    trigger_name = f"reject_csrf_rotation_{suffix}"
    with engine.begin() as connection:
        connection.exec_driver_sql(
            f"CREATE FUNCTION {function_name}() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN "
            "RAISE EXCEPTION 'sensitive csrf rotation detail'; END $$"
        )
        connection.exec_driver_sql(
            f"CREATE TRIGGER {trigger_name} BEFORE UPDATE OF csrf_token_hash "
            "ON user_sessions "
            f"FOR EACH ROW EXECUTE FUNCTION {function_name}()"
        )

    try:
        response = _bootstrap_session(client)

        assert response.status_code == 503
        assert response.json() == {
            "error": {"code": "AUTHENTICATION_UNAVAILABLE"}
        }
        assert "sensitive csrf rotation detail" not in response.text
        current_hash, previous_hash, previous_expires_at = _csrf_state(engine)
        assert current_hash == token_digest(old_token)
        assert previous_hash is None
        assert previous_expires_at is None
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                f"DROP TRIGGER IF EXISTS {trigger_name} ON user_sessions"
            )
            connection.exec_driver_sql(f"DROP FUNCTION IF EXISTS {function_name}()")


def test_concurrent_session_bootstraps_keep_only_serialized_generations(
    authentication_api,
):
    client, engine, _, _ = authentication_api
    _create_verified_user(client, engine)
    _login(client)

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(
            executor.map(lambda _: _bootstrap_session(client), range(2))
        )

    assert [response.status_code for response in responses] == [200, 200]
    rotated_hashes = {
        token_digest(response.json()["csrf_token"])
        for response in responses
    }
    current_hash, previous_hash, previous_expires_at = _csrf_state(engine)
    assert {current_hash, previous_hash} == rotated_hashes
    assert previous_expires_at is not None
