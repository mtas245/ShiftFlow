from uuid import UUID

import pytest

from app.domain.company_membership import (
    CompanyMembership,
    CompanyRole,
    MembershipValidationError,
)


def test_membership_can_assign_administrator_role():
    membership = CompanyMembership.create(
        company_id="company-1",
        user_id="account-1",
        role=CompanyRole.ADMINISTRATOR,
        account_user_id="account-1",
        display_name="  Cafe Owner  ",
    )

    assert membership.company_id == "company-1"
    assert membership.user_id == "account-1"
    assert membership.role is CompanyRole.ADMINISTRATOR
    assert membership.account_user_id == "account-1"
    assert membership.display_name == "Cafe Owner"
    assert str(UUID(membership.public_id)) == membership.public_id


def test_membership_can_assign_employee_role():
    membership = CompanyMembership.create(
        company_id="company-1",
        user_id="user-1",
        role=CompanyRole.EMPLOYEE,
    )

    assert membership.role is CompanyRole.EMPLOYEE


@pytest.mark.parametrize(
    "display_name",
    ["", "  ", "x" * 81, "Cafe\u0000Owner"],
)
def test_linked_membership_requires_valid_company_display_name(display_name):
    with pytest.raises(MembershipValidationError):
        CompanyMembership.create(
            company_id="company-1",
            user_id="account-1",
            account_user_id="account-1",
            display_name=display_name,
            role=CompanyRole.EMPLOYEE,
        )


def test_legacy_membership_can_remain_unlinked_without_display_name():
    membership = CompanyMembership.create(
        company_id="company-1",
        user_id="legacy-opaque-id",
        role=CompanyRole.EMPLOYEE,
    )

    assert membership.account_user_id is None
    assert membership.display_name is None


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
