import pytest

from app.domain.company_membership import (
    CompanyMembership,
    CompanyRole,
    MembershipValidationError,
)


def test_membership_can_assign_administrator_role():
    membership = CompanyMembership.create(
        company_id="company-1",
        user_id="user-1",
        role=CompanyRole.ADMINISTRATOR,
    )

    assert membership.company_id == "company-1"
    assert membership.user_id == "user-1"
    assert membership.role is CompanyRole.ADMINISTRATOR


@pytest.mark.parametrize(
    ("company_id", "user_id"),
    [
        ("", "user-1"),
        ("  ", "user-1"),
        ("company-1", ""),
        ("company-1", " \t"),
    ],
)
def test_membership_requires_company_and_user_identifiers(company_id, user_id):
    with pytest.raises(MembershipValidationError):
        CompanyMembership.create(
            company_id=company_id,
            user_id=user_id,
            role=CompanyRole.ADMINISTRATOR,
        )
