import os
import re

import pytest
from sqlalchemy import create_engine, func, insert, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app.application.register_company import (
    AuthenticatedUser,
    JoiningCodeUnavailable,
    RegisterCompany,
    RegisterCompanyCommand,
)
from app.domain.company import Company
from app.domain.company_membership import CompanyMembership, CompanyRole
from app.infrastructure.composition import build_register_company
from app.infrastructure.persistence.models import (
    Base,
    CompanyMembershipRecord,
    CompanyRecord,
    UserRecord,
)
from app.infrastructure.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory


pytestmark = pytest.mark.postgres


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


@pytest.fixture
def postgres_engine():
    engine = create_engine(_test_database_url(), pool_pre_ping=True)
    safe_database = False
    try:
        with engine.connect() as connection:
            database, username = connection.execute(
                text("SELECT current_database(), current_user")
            ).one()
        if database != "shiftflow_test" or username != "shiftflow_test_user":
            pytest.fail("Refusing connection not authenticated to the dedicated test database")

        safe_database = True
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        yield engine
    finally:
        if safe_database:
            Base.metadata.drop_all(engine)
        engine.dispose()


class SequenceCodeGenerator:
    def __init__(self, codes):
        self.codes = iter(codes)
        self.attempts = 0

    def generate(self):
        self.attempts += 1
        return next(self.codes)


def valid_command(**overrides):
    values = {
        "company_name": "North Star Cafe",
        "business_email": "  SCHEDULE@EXAMPLE.COM  ",
        "business_phone": "+41791234567",
        "business_address": "1 Main Street",
        "industry": "  Food   and\tHospitality  ",
        "display_name": "Company Owner",
    }
    values.update(overrides)
    return RegisterCompanyCommand(**values)


def _register(engine, codes):
    return RegisterCompany(
        SqlAlchemyUnitOfWorkFactory(engine), SequenceCodeGenerator(codes)
    )


def _ensure_account(engine, user_id):
    with engine.begin() as connection:
        if connection.scalar(
            select(UserRecord.id).where(UserRecord.id == user_id)
        ) is None:
            email_local_part = re.sub(r"[^a-z0-9]+", "-", user_id.lower()).strip("-")
            connection.execute(
                UserRecord.__table__.insert().values(
                    id=user_id,
                    email=f"{email_local_part or 'test-user'}@example.test",
                    password_hash="not-used-by-registration-tests",
                    email_verified_at=None,
                )
            )


def test_postgres_tests_do_not_fall_back_to_database_url(monkeypatch):
    monkeypatch.delenv("SHIFTFLOW_TEST_DATABASE_URL", raising=False)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://production:secret@127.0.0.1/production",
    )

    with pytest.raises(RuntimeError, match="SHIFTFLOW_TEST_DATABASE_URL"):
        _test_database_url()


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://shiftflow_test_user:pw@example.com/shiftflow_test",
        "postgresql+psycopg://shiftflow_test_user:pw@127.0.0.1/development",
        "postgresql+psycopg://other_user:pw@127.0.0.1/shiftflow_test",
        "sqlite:///shiftflow_test.db",
    ],
)
def test_postgres_tests_reject_nonisolated_database_urls(monkeypatch, url):
    monkeypatch.setenv("SHIFTFLOW_TEST_DATABASE_URL", url)

    with pytest.raises(RuntimeError, match="Refusing PostgreSQL URL"):
        _test_database_url()


def test_company_and_administrator_membership_are_committed_together(postgres_engine):
    _ensure_account(postgres_engine, "user-1")
    company = _register(postgres_engine, ["AB12CD34"]).execute(
        AuthenticatedUser(user_id="user-1"), valid_command()
    )

    with postgres_engine.connect() as connection:
        stored_code = connection.scalar(
            select(CompanyRecord.joining_code).where(
                CompanyRecord.id == company.id
            )
        )
        membership = connection.execute(
            select(
                CompanyMembershipRecord.user_id,
                CompanyMembershipRecord.role,
                CompanyMembershipRecord.account_user_id,
                CompanyMembershipRecord.display_name,
                CompanyMembershipRecord.public_id,
            ).where(CompanyMembershipRecord.company_id == company.id)
        ).one()

    assert stored_code == "AB12CD34"
    assert membership.user_id == "user-1"
    assert membership.role == "administrator"
    assert membership.account_user_id == "user-1"
    assert membership.display_name == "Company Owner"
    assert membership.public_id


def test_membership_failure_rolls_back_company(postgres_engine):
    factory = SqlAlchemyUnitOfWorkFactory(postgres_engine)
    company = Company.create(
        company_name="North Star Cafe",
        business_email="schedule@example.com",
        business_phone="+41791234567",
        business_address="1 Main Street",
        industry="Hospitality",
        joining_code="AB12CD34",
    )
    invalid_membership = CompanyMembership.create(
        company_id="missing-company",
        user_id="user-1",
        role=CompanyRole.ADMINISTRATOR,
    )

    with pytest.raises(IntegrityError):
        with factory() as unit_of_work:
            unit_of_work.companies.add(company)
            unit_of_work.memberships.add(invalid_membership)

    with postgres_engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(CompanyRecord)) == 0
        assert (
            connection.scalar(
                select(func.count()).select_from(CompanyMembershipRecord)
            )
            == 0
        )


def test_database_enforces_joining_code_uniqueness_across_companies(postgres_engine):
    _ensure_account(postgres_engine, "user-1")
    _register(postgres_engine, ["AB12CD34"]).execute(
        AuthenticatedUser("user-1"), valid_command()
    )

    with pytest.raises(IntegrityError):
        with postgres_engine.begin() as connection:
            connection.execute(
                insert(CompanyRecord).values(
                    id="company-duplicate",
                    company_name="Duplicate",
                    business_email="duplicate@example.com",
                    business_phone="+41791234567",
                    business_address="1 Main Street",
                    industry="Hospitality",
                    joining_code="AB12CD34",
                )
            )


def test_duplicate_joining_code_is_retried_and_registration_succeeds(postgres_engine):
    factory = SqlAlchemyUnitOfWorkFactory(postgres_engine)
    _ensure_account(postgres_engine, "existing-user")
    _ensure_account(postgres_engine, "new-user")
    _register(postgres_engine, ["AB12CD34"]).execute(
        AuthenticatedUser("existing-user"), valid_command()
    )
    generator = SequenceCodeGenerator(["AB12CD34", "EF56GH78"])
    service = RegisterCompany(factory, generator)

    company = service.execute(AuthenticatedUser("new-user"), valid_command())

    assert company.joining_code == "EF56GH78"
    assert generator.attempts == 2
    with postgres_engine.connect() as connection:
        codes = set(connection.scalars(select(CompanyRecord.joining_code)).all())
    assert codes == {"AB12CD34", "EF56GH78"}


def test_five_database_collisions_leave_no_partial_records(postgres_engine):
    _ensure_account(postgres_engine, "existing-user")
    _ensure_account(postgres_engine, "new-user")
    _register(postgres_engine, ["AB12CD34"]).execute(
        AuthenticatedUser("existing-user"), valid_command()
    )
    generator = SequenceCodeGenerator(["AB12CD34"] * 5)

    with pytest.raises(JoiningCodeUnavailable) as error:
        RegisterCompany(SqlAlchemyUnitOfWorkFactory(postgres_engine), generator).execute(
            AuthenticatedUser("new-user"), valid_command()
        )

    assert error.value.code == "JOINING_CODE_UNAVAILABLE"
    assert generator.attempts == 5
    with postgres_engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(CompanyRecord)) == 1
        assert (
            connection.scalar(
                select(func.count()).select_from(CompanyMembershipRecord)
            )
            == 1
        )


def test_production_composition_registers_company_with_secure_joining_code(
    postgres_engine,
):
    _ensure_account(postgres_engine, "production-user")
    company = build_register_company(postgres_engine).execute(
        AuthenticatedUser("production-user"), valid_command()
    )

    assert re.fullmatch(r"[A-Z0-9]{8}", company.joining_code)
    with postgres_engine.connect() as connection:
        stored_code = connection.scalar(
            select(CompanyRecord.joining_code).where(
                CompanyRecord.id == company.id
            )
        )
        membership_count = connection.scalar(
            select(func.count())
            .select_from(CompanyMembershipRecord)
            .where(CompanyMembershipRecord.company_id == company.id)
        )
    assert stored_code == company.joining_code
    assert membership_count == 1


def test_unexpected_database_constraint_failure_is_not_retried_as_code_collision(
    postgres_engine,
):
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE companies ADD CONSTRAINT "
                "ck_test_reject_company_name "
                "CHECK (company_name <> 'Database rejects')"
            )
        )

    generator = SequenceCodeGenerator(["AB12CD34"] * 5)
    service = RegisterCompany(
        SqlAlchemyUnitOfWorkFactory(postgres_engine), generator
    )
    _ensure_account(postgres_engine, "unexpected-error-user")

    with pytest.raises(IntegrityError):
        service.execute(
            AuthenticatedUser("unexpected-error-user"),
            valid_command(company_name="Database rejects"),
        )

    assert generator.attempts == 1
    with postgres_engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(CompanyRecord)) == 0
        assert (
            connection.scalar(
                select(func.count()).select_from(CompanyMembershipRecord)
            )
            == 0
        )
