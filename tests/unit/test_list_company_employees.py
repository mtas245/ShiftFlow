import pytest

from app.application.list_company_employees import (
    ListCompanyEmployees,
    MembershipEmployee,
)
from app.application.list_my_companies import InvalidMembershipQuery
from app.application.manage_shifts import Forbidden, ResourceNotFound
from app.application.register_company import AuthenticatedUser
from app.domain.company_membership import CompanyMembership, CompanyRole


class MembershipRepository:
    def __init__(self, rows):
        self.rows = rows
        self.list_query = None

    def get(self, company_id, user_id):
        return next(
            (
                row
                for row in self.rows
                if row.company_id == company_id and row.user_id == user_id
            ),
            None,
        )

    def get_for_account(self, company_id, account_user_id):
        return next(
            (
                row
                for row in self.rows
                if row.company_id == company_id
                and row.account_user_id == account_user_id
            ),
            None,
        )

    def list_linked_employees(
        self, company_id, *, after_public_id, limit
    ):
        self.list_query = (company_id, after_public_id, limit)
        return [
            MembershipEmployee("membership-a", "Employee A"),
        ]


class UnitOfWork:
    def __init__(self, repository):
        self.memberships = repository

    def __enter__(self):
        return self

    def __exit__(self, _error_type, _error, _traceback):
        return False


def _membership(company_id, user_id, role):
    return CompanyMembership.create(
        company_id=company_id,
        user_id=user_id,
        role=role,
        account_user_id=user_id,
        display_name=user_id,
    )


def test_company_employee_roster_requires_admin_and_queries_bounded_page():
    rows = [
        _membership("company-a", "admin-a", CompanyRole.ADMINISTRATOR),
        _membership("company-a", "employee-a", CompanyRole.EMPLOYEE),
    ]
    repository = MembershipRepository(rows)
    service = ListCompanyEmployees(lambda: UnitOfWork(repository))

    service.execute(
        AuthenticatedUser("admin-a"),
        "company-a",
        limit=20,
        after_public_id="00000000-0000-0000-0000-000000000001",
    )

    assert repository.list_query == (
        "company-a",
        "00000000-0000-0000-0000-000000000001",
        20,
    )


def test_company_employee_roster_hides_tenant_for_nonmembers():
    service = ListCompanyEmployees(
        lambda: UnitOfWork(
            MembershipRepository(
                [_membership("company-b", "admin-b", CompanyRole.ADMINISTRATOR)]
            )
        )
    )

    with pytest.raises(ResourceNotFound):
        service.execute(AuthenticatedUser("admin-b"), "company-a")


def test_employee_cannot_read_company_employee_roster():
    service = ListCompanyEmployees(
        lambda: UnitOfWork(
            MembershipRepository(
                [_membership("company-a", "employee-a", CompanyRole.EMPLOYEE)]
            )
        )
    )

    with pytest.raises(Forbidden):
        service.execute(AuthenticatedUser("employee-a"), "company-a")


@pytest.mark.parametrize("limit", [0, -1, 201])
def test_company_employee_roster_rejects_out_of_range_page_size(limit):
    service = ListCompanyEmployees(
        lambda: UnitOfWork(
            MembershipRepository(
                [_membership("company-a", "admin-a", CompanyRole.ADMINISTRATOR)]
            )
        )
    )

    with pytest.raises(InvalidMembershipQuery):
        service.execute(AuthenticatedUser("admin-a"), "company-a", limit=limit)


def test_roster_result_contains_only_scoped_membership_reference_and_name():
    item = MembershipEmployee(
        membership_id="public-id",
        display_name="Cafe Employee",
    )

    assert item.membership_id == "public-id"
    assert item.display_name == "Cafe Employee"
    assert not hasattr(item, "account_user_id")
    assert not hasattr(item, "user_id")
