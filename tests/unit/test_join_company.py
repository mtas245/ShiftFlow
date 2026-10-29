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


class UnitOfWork:
    def __init__(self, company=None, membership=None, add_error=None):
        self.company = company
        self.membership = membership
        self.add_error = add_error
        self.added = []

    def __enter__(self):
        self.companies = CompanyRepository(self)
        self.memberships = MembershipRepository(self)
        return self

    def __exit__(self, _error_type, _error, _traceback):
        return False


class CompanyRepository:
    def __init__(self, unit_of_work):
        self.unit_of_work = unit_of_work

    def find_by_joining_code(self, joining_code):
        if (
            self.unit_of_work.company is not None
            and self.unit_of_work.company.joining_code == joining_code
        ):
            return self.unit_of_work.company
        return None


class MembershipRepository:
    def __init__(self, unit_of_work):
        self.unit_of_work = unit_of_work

    def get(self, company_id, user_id):
        membership = self.unit_of_work.membership
        if (
            membership is not None
            and membership.company_id == company_id
            and membership.user_id == user_id
        ):
            return membership
        return None

    def add(self, membership):
        if self.unit_of_work.add_error is not None:
            raise self.unit_of_work.add_error
        self.unit_of_work.added.append(membership)


def target_company():
    return Company.create(
        company_name="Test Cafe",
        business_email="cafe@example.test",
        business_phone="+41791234567",
        business_address="1 Test Street",
        industry="Hospitality",
        joining_code="AB12CD34",
    )


def test_join_uses_company_resolved_from_exact_joining_code():
    company = target_company()
    unit_of_work = UnitOfWork(company=company)
    service = JoinCompany(lambda: unit_of_work)

    membership = service.execute(
        AuthenticatedUser("trusted-user"),
        JoinCompanyCommand(joining_code="AB12CD34", display_name="Employee One"),
    )

    assert membership.company_id == company.id
    assert membership.user_id == "trusted-user"
    assert membership.role is CompanyRole.EMPLOYEE
    assert unit_of_work.added == [membership]


@pytest.mark.parametrize(
    "joining_code",
    ["ab12cd34", " AB12CD3", "AB12CD3", "AB12CD3!", "ABCDEFGH "],
)
def test_join_code_format_is_validated_without_normalization(joining_code):
    service = JoinCompany(lambda: UnitOfWork(company=target_company()))

    with pytest.raises(InvalidJoiningCode):
        service.execute(
            AuthenticatedUser("trusted-user"),
            JoinCompanyCommand(
                joining_code=joining_code,
                display_name="Employee One",
            ),
        )


def test_join_does_not_accept_missing_or_blank_authenticated_identity():
    service = JoinCompany(lambda: UnitOfWork(company=target_company()))

    for principal in (None, AuthenticatedUser(" ")):
        with pytest.raises(AuthenticationRequired):
            service.execute(
                principal,
                JoinCompanyCommand(
                    joining_code="AB12CD34",
                    display_name="Employee One",
                ),
            )


def test_unknown_joining_code_is_indistinguishable_from_malformed_code():
    service = JoinCompany(lambda: UnitOfWork())
    with pytest.raises(InvalidJoiningCode) as error:
        service.execute(
            AuthenticatedUser("trusted-user"),
            JoinCompanyCommand(
                joining_code="AB12CD34",
                display_name="Employee One",
            ),
        )

    assert error.value.code == "INVALID_JOINING_CODE"
    assert str(error.value) == ""


def test_existing_membership_maps_to_already_a_member_without_mutation():
    company = target_company()
    existing = CompanyMembership.create(
        company_id=company.id,
        user_id="trusted-user",
        role=CompanyRole.ADMINISTRATOR,
    )
    service = JoinCompany(
        lambda: UnitOfWork(company=company, membership=existing)
    )

    with pytest.raises(AlreadyAMember) as error:
        service.execute(
            AuthenticatedUser("trusted-user"),
            JoinCompanyCommand(
                joining_code="AB12CD34",
                display_name="Employee One",
            ),
        )

    assert error.value.code == "ALREADY_A_MEMBER"
    assert existing.role is CompanyRole.ADMINISTRATOR


def test_unexpected_membership_failure_maps_to_safe_creation_error():
    service = JoinCompany(
        lambda: UnitOfWork(
            company=target_company(),
            add_error=RuntimeError("database internals"),
        )
    )

    with pytest.raises(MembershipCreationFailed) as error:
        service.execute(
            AuthenticatedUser("trusted-user"),
            JoinCompanyCommand(
                joining_code="AB12CD34",
                display_name="Employee One",
            ),
        )

    assert error.value.code == "MEMBERSHIP_CREATION_FAILED"
    assert str(error.value) == ""
