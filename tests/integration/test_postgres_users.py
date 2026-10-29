import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app.application.create_user import (
    CreateUser,
    CreateUserCommand,
    DuplicateEmail,
    UserCreationFailed,
)
from app.infrastructure.password_hasher import Argon2PasswordHasher
from app.infrastructure.persistence.models import (
    Base,
    CompanyMembershipRecord,
    CompanyRecord,
    UserRecord,
)
from app.infrastructure.persistence.unit_of_work import (
    SqlAlchemyUnitOfWorkFactory,
)


pytestmark = pytest.mark.postgres

_INITIAL_REVISION = "4cd72c57db85"


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


def _run_alembic(revision):
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=_repository_root(),
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def migrated_postgres(monkeypatch):
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
        _run_alembic(_INITIAL_REVISION)
        yield engine
    finally:
        if safe_database:
            Base.metadata.drop_all(engine)
            with engine.begin() as connection:
                connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
        engine.dispose()


def test_additive_user_migration_preserves_legacy_membership_identity(
    migrated_postgres,
):
    engine = migrated_postgres
    with engine.begin() as connection:
        connection.execute(
            CompanyRecord.__table__.insert().values(
                id="legacy-company",
                company_name="Legacy Test Company",
                business_email="contact@example.test",
                business_phone="+41791234567",
                business_address="1 Test Street",
                industry="Hospitality",
                joining_code="LEGACY01",
            )
        )
        connection.execute(
            text(
                "INSERT INTO company_memberships (company_id, user_id, role) "
                "VALUES ('legacy-company', 'legacy-auth-provider-id', 'employee')"
            )
        )

    _run_alembic("head")
    _run_alembic("head")

    inspector = inspect(engine)
    foreign_keys = inspector.get_foreign_keys("company_memberships")
    assert not any(
        "user_id" in constraint["constrained_columns"]
        for constraint in foreign_keys
    )
    with engine.connect() as connection:
        legacy = connection.execute(
            text(
                "SELECT company_id, user_id, role FROM company_memberships"
            )
        ).one()
    assert legacy == (
        "legacy-company",
        "legacy-auth-provider-id",
        "employee",
    )

    service = CreateUser(
        SqlAlchemyUnitOfWorkFactory(engine),
        Argon2PasswordHasher(),
    )
    user = service.execute(
        CreateUserCommand(
            email=" Alice@Example.com ",
            password="long-test-account-password",
        )
    )
    with engine.connect() as connection:
        stored_user = connection.execute(
            select(UserRecord.id, UserRecord.email, UserRecord.password_hash)
        ).one()
        still_legacy = connection.scalar(
            select(CompanyMembershipRecord.user_id).where(
                CompanyMembershipRecord.company_id == "legacy-company"
            )
        )

    assert stored_user.id == user.id
    assert stored_user.email == "alice@example.com"
    assert stored_user.password_hash != "long-test-account-password"
    assert Argon2PasswordHasher().verify(
        stored_user.password_hash, "long-test-account-password"
    )
    assert still_legacy == "legacy-auth-provider-id"


def test_postgres_rejects_duplicate_normalized_account_email(migrated_postgres):
    _run_alembic("head")
    service = CreateUser(
        SqlAlchemyUnitOfWorkFactory(migrated_postgres),
        Argon2PasswordHasher(),
    )
    service.execute(
        CreateUserCommand(email="Alice@Example.com", password="first-long-password")
    )

    with pytest.raises(DuplicateEmail):
        service.execute(
            CreateUserCommand(
                email=" alice@example.COM ",
                password="second-long-password",
            )
        )


def test_postgres_rejects_non_normalized_email_at_storage_boundary(
    migrated_postgres,
):
    engine = migrated_postgres
    _run_alembic("head")

    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                UserRecord.__table__.insert().values(
                    id=str(uuid4()),
                    email="MixedCase@example.com",
                    password_hash="not-used-by-login",
                )
            )


def test_postgres_user_creation_failure_rolls_back_without_hash_leak(
    migrated_postgres,
):
    engine = migrated_postgres
    _run_alembic("head")
    suffix = uuid4().hex
    trigger_name = f"reject_user_{suffix}"
    function_name = f"reject_user_{suffix}"
    with engine.begin() as connection:
        connection.exec_driver_sql(
            f"CREATE FUNCTION {function_name}() RETURNS trigger LANGUAGE plpgsql "
            "AS $$ BEGIN RAISE EXCEPTION 'sensitive user persistence detail'; END $$"
        )
        connection.exec_driver_sql(
            f"CREATE TRIGGER {trigger_name} BEFORE INSERT ON users "
            f"FOR EACH ROW EXECUTE FUNCTION {function_name}()"
        )

    try:
        service = CreateUser(
            SqlAlchemyUnitOfWorkFactory(engine),
            Argon2PasswordHasher(),
        )
        password = "do-not-leak-this-password"
        with pytest.raises(UserCreationFailed) as error:
            service.execute(
                CreateUserCommand(
                    email="rollback@example.com",
                    password=password,
                )
            )

        assert password not in str(error.value)
        assert "sensitive user persistence detail" not in str(error.value)
        with engine.connect() as connection:
            assert connection.scalar(
                select(UserRecord.id).where(
                    UserRecord.email == "rollback@example.com"
                )
            ) is None
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                f"DROP TRIGGER IF EXISTS {trigger_name} ON users"
            )
            connection.exec_driver_sql(
                f"DROP FUNCTION IF EXISTS {function_name}()"
            )
