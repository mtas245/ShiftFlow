import pytest

from app.application.list_my_companies import (
    InvalidMembershipQuery,
    ListMyCompanies,
    MembershipCompany,
)
from app.application.join_company import AuthenticationRequired
from app.application.register_company import AuthenticatedUser


class MembershipRepository:
    def __init__(self, rows):
        self.rows = rows
        self.query = None

    def list_for_account(
        self,
        account_user_id,
        *,
        after_company_id,
        after_public_id,
        limit,
    ):
        self.query = (
            account_user_id,
            after_company_id,
            after_public_id,
            limit,
        )
        return self.rows


class UnitOfWork:
    def __init__(self, repository):
        self.memberships = repository

    def __enter__(self):
        return self

    def __exit__(self, _error_type, _error, _traceback):
        return False


def test_company_discovery_uses_trusted_account_and_bounded_page():
    rows = [
        MembershipCompany(
            membership_id="public-membership-id",
            company_id="company-1",
            company_name="Test Cafe",
            role="employee",
        )
    ]
    repository = MembershipRepository(rows)
    service = ListMyCompanies(lambda: UnitOfWork(repository))

    result = service.execute(
        AuthenticatedUser("trusted-account"),
        limit=20,
        after_company_id="company-0",
        after_public_id="previous-membership",
    )

    assert result == rows
    assert repository.query == (
        "trusted-account",
        "company-0",
        "previous-membership",
        20,
    )


@pytest.mark.parametrize("limit", [0, -1, 201])
def test_company_discovery_rejects_out_of_range_page_size(limit):
    service = ListMyCompanies(lambda: UnitOfWork(MembershipRepository([])))

    with pytest.raises(InvalidMembershipQuery):
        service.execute(AuthenticatedUser("account-1"), limit=limit)


def test_company_discovery_requires_a_trusted_principal():
    service = ListMyCompanies(lambda: UnitOfWork(MembershipRepository([])))

    with pytest.raises(AuthenticationRequired):
        service.execute(None)
