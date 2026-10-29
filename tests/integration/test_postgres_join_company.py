import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url

from app.application.join_company import (
    AlreadyAMember,
    DuplicateMembershipConflict,
    InvalidJoiningCode,
    JoinCompany,
    JoinCompanyCommand,
    MembershipCreationFailed,
)
from app.application.register_company import AuthenticatedUser
from app.domain.company import Company
from app.domain.company_membership import CompanyMembership, CompanyRole
from app.infrastructure.persistence.models import (
    Base,
    CompanyMembershipRecord,
    UserRecord,
)
from app.infrastructure.persistence.repositories import (
    SqlAlchemyCompanyMembershipRepository,
)
from app.infrastructure.persistence.unit_of_work import (
    SqlAlchemyUnitOfWorkFactory,
)


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
            pytest.fail(
                "Refusing connection not authenticated to the dedicated test database"
            )

        safe_database = True
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        yield engine
    finally:
        if safe_database:
            Base.metadata.drop_all(engine)
        engine.dispose()


def _company(company_id, joining_code):
    return Company.create(
        company_name="Test Cafe",
        business_email=f"{company_id}@example.test",
        business_phone="+41791234567",
        business_address="1 Test Street",
        industry="Hospitality",
        joining_code=joining_code,
    )


def _create_company(factory, company):
    with factory() as unit_of_work:
        unit_of_work.companies.add(company)


def _service(engine):
    service = JoinCompany(SqlAlchemyUnitOfWorkFactory(engine))

    class RegisteredAccountJoinService:
        def execute(self, principal, command):
            _ensure_account(engine, principal.user_id)
            return service.execute(principal, command)

    return RegisteredAccountJoinService()


def _ensure_account(engine, user_id):
    with engine.begin() as connection:
        if connection.scalar(
            select(UserRecord.id).where(UserRecord.id == user_id)
        ) is None:
            connection.execute(
                UserRecord.__table__.insert().values(
                    id=user_id,
                    email=f"{user_id}@example.test",
                    password_hash="not-used-by-joining-tests",
                    email_verified_at=None,
                )
            )


def _membership_count(engine, company_id=None, user_id=None):
    query = select(func.count()).select_from(CompanyMembershipRecord)
    if company_id is not None:
        query = query.where(CompanyMembershipRecord.company_id == company_id)
    if user_id is not None:
        query = query.where(CompanyMembershipRecord.user_id == user_id)
    with engine.connect() as connection:
        return connection.scalar(query)


def test_join_company_persists_employee_membership(postgres_engine):
    factory = SqlAlchemyUnitOfWorkFactory(postgres_engine)
    target = _company("target", "AB12CD34")
    _create_company(factory, target)

    joined = _service(postgres_engine).execute(
        AuthenticatedUser("employee-1"),
        JoinCompanyCommand(joining_code="AB12CD34", display_name="Employee One"),
    )

    assert joined.company_id == target.id
    assert joined.user_id == "employee-1"
    assert joined.role is CompanyRole.EMPLOYEE
    assert _membership_count(postgres_engine, target.id, "employee-1") == 1


def test_unknown_joining_code_does_not_disclose_company(postgres_engine):
    with pytest.raises(InvalidJoiningCode) as error:
        _service(postgres_engine).execute(
            AuthenticatedUser("employee-1"),
            JoinCompanyCommand(joining_code="AB12CD34", display_name="Employee One"),
        )

    assert error.value.code == "INVALID_JOINING_CODE"
    assert _membership_count(postgres_engine) == 0


def test_existing_membership_returns_conflict_and_preserves_role(
    postgres_engine,
):
    factory = SqlAlchemyUnitOfWorkFactory(postgres_engine)
    target = _company("target", "AB12CD34")
    _create_company(factory, target)
    existing = CompanyMembership.create(
        company_id=target.id,
        user_id="employee-1",
        role=CompanyRole.ADMINISTRATOR,
    )
    with factory() as unit_of_work:
        unit_of_work.memberships.add(existing)

    with pytest.raises(AlreadyAMember) as error:
        _service(postgres_engine).execute(
            AuthenticatedUser("employee-1"),
            JoinCompanyCommand(joining_code="AB12CD34", display_name="Employee One"),
        )

    assert error.value.code == "ALREADY_A_MEMBER"
    with postgres_engine.connect() as connection:
        records = connection.scalars(
            select(CompanyMembershipRecord.role).where(
                CompanyMembershipRecord.company_id == target.id,
                CompanyMembershipRecord.user_id == "employee-1",
            )
        ).all()
    assert len(records) == 1
    assert records == ["administrator"]


def test_join_preserves_membership_in_other_company(postgres_engine):
    factory = SqlAlchemyUnitOfWorkFactory(postgres_engine)
    first = _company("first", "AB12CD34")
    second = _company("second", "EF56GH78")
    _create_company(factory, first)
    _create_company(factory, second)
    existing = CompanyMembership.create(
        company_id=first.id,
        user_id="employee-1",
        role=CompanyRole.ADMINISTRATOR,
    )
    with factory() as unit_of_work:
        unit_of_work.memberships.add(existing)

    joined = _service(postgres_engine).execute(
        AuthenticatedUser("employee-1"),
        JoinCompanyCommand(joining_code="EF56GH78", display_name="Employee One"),
    )

    assert joined.company_id == second.id
    assert _membership_count(postgres_engine, user_id="employee-1") == 2
    with postgres_engine.connect() as connection:
        first_record = connection.scalar(
            select(CompanyMembershipRecord.role).where(
                CompanyMembershipRecord.company_id == first.id
            )
        )
    assert first_record == "administrator"


def test_concurrent_duplicate_joins_create_one_membership_and_conflict(
    postgres_engine, monkeypatch
):
    factory = SqlAlchemyUnitOfWorkFactory(postgres_engine)
    target = _company("target", "AB12CD34")
    _create_company(factory, target)
    original_get = SqlAlchemyCompanyMembershipRepository.get
    both_checked = Barrier(2)

    def synchronized_get(repository, company_id, user_id):
        result = original_get(repository, company_id, user_id)
        both_checked.wait(timeout=10)
        return result

    monkeypatch.setattr(
        SqlAlchemyCompanyMembershipRepository,
        "get",
        synchronized_get,
    )
    service = _service(postgres_engine)

    def join():
        try:
            service.execute(
                AuthenticatedUser("employee-1"),
                JoinCompanyCommand(joining_code="AB12CD34", display_name="Employee One"),
            )
            return "joined"
        except AlreadyAMember:
            return "already-a-member"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(join) for _ in range(2)]
        outcomes = [future.result(timeout=10) for future in futures]

    assert sorted(outcomes) == ["already-a-member", "joined"]
    assert _membership_count(postgres_engine, target.id, "employee-1") == 1


def test_unexpected_membership_failure_rolls_back(postgres_engine, monkeypatch):
    factory = SqlAlchemyUnitOfWorkFactory(postgres_engine)
    target = _company("target", "AB12CD34")
    _create_company(factory, target)
    original_add = SqlAlchemyCompanyMembershipRepository.add

    def add_then_fail(repository, membership):
        original_add(repository, membership)
        raise RuntimeError("simulated persistence failure")

    monkeypatch.setattr(
        SqlAlchemyCompanyMembershipRepository,
        "add",
        add_then_fail,
    )

    with pytest.raises(MembershipCreationFailed) as error:
        _service(postgres_engine).execute(
            AuthenticatedUser("employee-1"),
            JoinCompanyCommand(joining_code="AB12CD34", display_name="Employee One"),
        )

    assert error.value.code == "MEMBERSHIP_CREATION_FAILED"
    assert str(error.value) == ""
    assert _membership_count(postgres_engine, target.id, "employee-1") == 0


def test_membership_adapter_distinguishes_known_unique_conflict(postgres_engine):
    factory = SqlAlchemyUnitOfWorkFactory(postgres_engine)
    target = _company("target", "AB12CD34")
    _create_company(factory, target)
    membership = CompanyMembership.create(
        company_id=target.id,
        user_id="employee-1",
        role=CompanyRole.EMPLOYEE,
    )
    with factory() as unit_of_work:
        unit_of_work.memberships.add(membership)

    with pytest.raises(DuplicateMembershipConflict):
        with factory() as unit_of_work:
            unit_of_work.memberships.add(membership)
