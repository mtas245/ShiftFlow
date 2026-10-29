import pytest

from app.application.join_company import (
    AlreadyAMember,
    AuthenticationRequired,
    InvalidJoiningCode,
    JoinCompany,
    JoinCompanyCommand,
    MembershipCreationFailed,
)
from app.application.register_company import AuthenticatedUser
from app.domain.company import Company
from app.domain.company_membership import CompanyMembership, CompanyRole


class InMemoryDatabase:
    def __init__(self):
        self.companies = []
        self.memberships = []
        self.fail_membership_add = False

    def unit_of_work(self):
        return InMemoryUnitOfWork(self)


class InMemoryUnitOfWork:
    def __init__(self, database):
        self.database = database
        self.pending_memberships = []

    def __enter__(self):
        self.companies = CompanyRepository(self.database)
        self.memberships = MembershipRepository(self)
        return self

    def __exit__(self, error_type, _error, _traceback):
        if error_type is None:
            self.database.memberships.extend(self.pending_memberships)
        return False


class CompanyRepository:
    def __init__(self, database):
        self.database = database

    def find_by_joining_code(self, joining_code):
        return next(
            (
                company
                for company in self.database.companies
                if company.joining_code == joining_code
            ),
            None,
        )


class MembershipRepository:
    def __init__(self, unit_of_work):
        self.unit_of_work = unit_of_work

    def get(self, company_id, user_id):
        return next(
            (
                membership
                for membership in self.unit_of_work.database.memberships
                if membership.company_id == company_id
                and membership.user_id == user_id
            ),
            None,
        )

    def add(self, membership):
        if self.unit_of_work.database.fail_membership_add:
            self.unit_of_work.pending_memberships.append(membership)
            raise RuntimeError("simulated membership persistence failure")
        self.unit_of_work.pending_memberships.append(membership)


def company(company_id, joining_code):
    return Company(
        id=company_id,
        company_name="Test Cafe",
        business_email=f"{company_id}@example.test",
        business_phone="+41791234567",
        business_address="1 Test Street",
        industry="Hospitality",
        joining_code=joining_code,
    )


def member(company_id, user_id, role):
    return CompanyMembership.create(
        company_id=company_id,
        user_id=user_id,
        role=role,
    )


def test_join_creates_employee_membership_from_trusted_principal_only():
    database = InMemoryDatabase()
    database.companies.append(company("company-1", "AB12CD34"))
    service = JoinCompany(database.unit_of_work)

    joined = service.execute(
        AuthenticatedUser("authenticated-user"),
        JoinCompanyCommand(
            joining_code="AB12CD34",
            display_name="  Employee One ",
        ),
    )

    assert joined.company_id == "company-1"
    assert joined.user_id == "authenticated-user"
    assert joined.account_user_id == "authenticated-user"
    assert joined.display_name == "Employee One"
    assert joined.public_id
    assert joined.role is CompanyRole.EMPLOYEE
    assert database.memberships == [joined]
    assert "user_id" not in JoinCompanyCommand.__dataclass_fields__


@pytest.mark.parametrize(
    "joining_code",
    ["ab12cd34", " AB12CD3", "AB12CD3", "AB12CD3!", "ABCDEFGH "],
)
def test_join_rejects_malformed_codes_without_normalizing(joining_code):
    database = InMemoryDatabase()
    database.companies.append(company("company-1", "AB12CD34"))
    service = JoinCompany(database.unit_of_work)

    with pytest.raises(InvalidJoiningCode) as error:
        service.execute(
            AuthenticatedUser("user-1"),
            JoinCompanyCommand(
                joining_code=joining_code,
                display_name="Employee One",
            ),
        )

    assert error.value.code == "INVALID_JOINING_CODE"
    assert database.memberships == []


def test_unknown_and_malformed_codes_have_the_same_safe_error():
    database = InMemoryDatabase()
    service = JoinCompany(database.unit_of_work)
    outcomes = []
    for joining_code in ("AB12CD34", "bad-code"):
        try:
            service.execute(
                AuthenticatedUser("user-1"),
                JoinCompanyCommand(
                    joining_code=joining_code,
                    display_name="Employee One",
                ),
            )
        except InvalidJoiningCode as error:
            outcomes.append(error.code)

    assert outcomes == ["INVALID_JOINING_CODE", "INVALID_JOINING_CODE"]
    assert database.memberships == []


def test_joining_preserves_other_company_memberships():
    database = InMemoryDatabase()
    database.companies.extend(
        [
            company("company-1", "AB12CD34"),
            company("company-2", "EF56GH78"),
        ]
    )
    existing = member(
        "company-1", "user-1", CompanyRole.ADMINISTRATOR
    )
    database.memberships.append(existing)

    joined = JoinCompany(database.unit_of_work).execute(
        AuthenticatedUser("user-1"),
        JoinCompanyCommand(
            joining_code="EF56GH78",
            display_name="Employee One",
        ),
    )

    assert database.memberships == [existing, joined]
    assert existing.role is CompanyRole.ADMINISTRATOR
    assert joined.company_id == "company-2"
    assert joined.role is CompanyRole.EMPLOYEE


def test_join_rejects_existing_membership_without_changing_role():
    database = InMemoryDatabase()
    database.companies.append(company("company-1", "AB12CD34"))
    existing = member(
        "company-1", "user-1", CompanyRole.ADMINISTRATOR
    )
    database.memberships.append(existing)

    with pytest.raises(AlreadyAMember) as error:
        JoinCompany(database.unit_of_work).execute(
            AuthenticatedUser("user-1"),
            JoinCompanyCommand(
                joining_code="AB12CD34",
                display_name="Different Name",
            ),
        )

    assert error.value.code == "ALREADY_A_MEMBER"
    assert database.memberships == [existing]
    assert database.memberships[0].role is CompanyRole.ADMINISTRATOR


def test_join_rejects_missing_authenticated_principal():
    database = InMemoryDatabase()

    with pytest.raises(AuthenticationRequired) as error:
        JoinCompany(database.unit_of_work).execute(
            None,
            JoinCompanyCommand(
                joining_code="AB12CD34",
                display_name="Employee One",
            ),
        )

    assert error.value.code == "AUTHENTICATION_REQUIRED"
    assert database.memberships == []


def test_unexpected_membership_failure_rolls_back_without_details():
    database = InMemoryDatabase()
    database.companies.append(company("company-1", "AB12CD34"))
    database.fail_membership_add = True

    with pytest.raises(MembershipCreationFailed) as error:
        JoinCompany(database.unit_of_work).execute(
            AuthenticatedUser("user-1"),
            JoinCompanyCommand(
                joining_code="AB12CD34",
                display_name="Employee One",
            ),
        )

    assert error.value.code == "MEMBERSHIP_CREATION_FAILED"
    assert str(error.value) == ""
    assert database.memberships == []
