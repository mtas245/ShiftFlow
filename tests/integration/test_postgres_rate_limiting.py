import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys
from threading import Lock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.infrastructure.persistence.models import (
    Base,
    RateLimitBucketRecord,
)
from app.infrastructure.rate_limiter import (
    PostgresRateLimiter,
    RATE_LIMIT_POLICIES,
)
from app.interfaces.main import create_app


pytestmark = pytest.mark.postgres

_RATE_LIMIT_SECRET = "unit-test-rate-limit-key-not-for-production-123"
_ORIGIN = "https://testserver"


class RecordingMailer:
    def __init__(self):
        self.messages: list[tuple[str, str, str]] = []
        self._lock = Lock()

    def send_verification(self, email: str, token: str) -> None:
        with self._lock:
            self.messages.append(("verification", email, token))

    def send_password_reset(self, email: str, token: str) -> None:
        with self._lock:
            self.messages.append(("password_reset", email, token))


def _database_url():
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


@pytest.fixture
def rate_limit_api(monkeypatch):
    database_url = _database_url()
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
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=_repository_root(),
            check=True,
            capture_output=True,
            text=True,
        )
        mailer = RecordingMailer()
        limiter = PostgresRateLimiter(
            engine,
            hmac_secret=_RATE_LIMIT_SECRET,
        )
        app = create_app(
            engine=engine,
            public_origin=_ORIGIN,
            email_delivery=mailer,
            rate_limiter=limiter,
        )
        client = TestClient(
            app,
            base_url=_ORIGIN,
            raise_server_exceptions=False,
        )
        yield client, engine, limiter, mailer
        client.close()
    finally:
        if safe_database:
            Base.metadata.drop_all(engine)
            with engine.begin() as connection:
                connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
        engine.dispose()


def _headers():
    return {"Origin": _ORIGIN}


def _register(client, email="worker@example.test"):
    return client.post(
        "/auth/register",
        json={"email": email, "password": "secure-test-account-password"},
        headers=_headers(),
    )


def _login(client, email="worker@example.test"):
    return client.post(
        "/auth/login",
        json={"email": email, "password": "wrong-test-account-password"},
        headers=_headers(),
    )


def test_postgres_limiter_enforces_threshold_across_concurrent_requests(
    rate_limit_api,
):
    _, engine, limiter, _ = rate_limit_api
    second_instance = PostgresRateLimiter(
        engine,
        hmac_secret=_RATE_LIMIT_SECRET,
    )

    def consume(index):
        selected_limiter = limiter if index % 2 == 0 else second_instance
        return selected_limiter.allow("register:email", "worker@example.test")

    with ThreadPoolExecutor(max_workers=12) as executor:
        decisions = list(executor.map(consume, range(24)))

    assert sum(decisions) == 3


@pytest.mark.parametrize(
    ("scope", "policy"),
    tuple(RATE_LIMIT_POLICIES.items()),
)
def test_postgres_limiter_enforces_each_approved_bucket_threshold(
    rate_limit_api,
    scope,
    policy,
):
    _, _, limiter, _ = rate_limit_api

    decisions = [
        limiter.allow(scope, "bucket-threshold-test")
        for _ in range(policy.limit + 2)
    ]

    assert sum(decisions) == policy.limit


def test_rate_limit_storage_is_pseudonymous_and_expired_rows_are_cleaned(
    rate_limit_api,
):
    _, engine, limiter, _ = rate_limit_api
    now = datetime(2026, 10, 29, 2, tzinfo=timezone.utc)
    limiter = PostgresRateLimiter(
        engine,
        hmac_secret=_RATE_LIMIT_SECRET,
        clock=lambda: now,
    )

    assert limiter.allow("reset_password:email", "worker@example.test")
    with Session(engine) as session:
        bucket = session.scalars(select(RateLimitBucketRecord)).one()
    assert bucket.key_digest != "worker@example.test"
    assert "worker@example.test" not in repr(bucket)

    limiter = PostgresRateLimiter(
        engine,
        hmac_secret=_RATE_LIMIT_SECRET,
        clock=lambda: now + timedelta(days=2),
    )
    assert limiter.cleanup_expired() == 1
    with Session(engine) as session:
        assert session.scalars(select(RateLimitBucketRecord)).all() == []


def test_registration_limit_is_generic_and_applies_across_requests(rate_limit_api):
    client, _, _, mailer = rate_limit_api

    responses = [_register(client) for _ in range(4)]

    assert [response.status_code for response in responses] == [202] * 4
    assert all(
        response.json() == {"message": "If eligible, an email will be sent."}
        for response in responses
    )
    assert len(mailer.messages) == 1


@pytest.mark.parametrize(
    "path",
    [
        "/auth/email-verification/request",
        "/auth/password-reset/request",
    ],
)
def test_recovery_request_limits_keep_generic_responses(rate_limit_api, path):
    client, _, _, mailer = rate_limit_api

    responses = [
        client.post(
            path,
            json={"email": "unknown@example.test"},
            headers=_headers(),
        )
        for _ in range(4)
    ]

    assert [response.status_code for response in responses] == [202] * 4
    assert len({response.text for response in responses}) == 1
    assert "unknown@example.test" not in responses[-1].text
    assert mailer.messages == []


def test_login_is_rate_limited_before_password_verification(rate_limit_api):
    client, _, _, _ = rate_limit_api

    responses = [_login(client) for _ in range(6)]

    assert [response.status_code for response in responses] == [
        401,
        401,
        401,
        401,
        401,
        429,
    ]
    assert responses[-1].json() == {"error": {"code": "RATE_LIMITED"}}
    assert "worker@example.test" not in responses[-1].text


def test_limiter_database_outage_fails_closed_without_revealing_email(
    rate_limit_api,
):
    client, engine, _, mailer = rate_limit_api
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE rate_limit_buckets"))

    known = client.post(
        "/auth/password-reset/request",
        json={"email": "worker@example.test"},
        headers=_headers(),
    )
    unknown = client.post(
        "/auth/password-reset/request",
        json={"email": "unknown@example.test"},
        headers=_headers(),
    )
    login = _login(client)

    assert known.status_code == unknown.status_code == login.status_code == 503
    assert known.json() == unknown.json() == {
        "error": {"code": "RATE_LIMITING_UNAVAILABLE"}
    }
    assert login.json() == {"error": {"code": "RATE_LIMITING_UNAVAILABLE"}}
    assert "worker@example.test" not in known.text
    assert "unknown@example.test" not in unknown.text
    assert mailer.messages == []
