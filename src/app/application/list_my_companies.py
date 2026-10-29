from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.application.join_company import AuthenticationRequired
from app.application.register_company import AuthenticatedUser


class InvalidMembershipQuery(Exception):
    pass


class MembershipDiscoveryUnavailable(Exception):
    pass


@dataclass(frozen=True, slots=True)
class MembershipCompany:
    membership_id: str
    company_id: str
    company_name: str
    role: str


class MembershipRepository(Protocol):
    def list_for_account(
        self,
        account_user_id: str,
        *,
        after_company_id: str | None,
        after_public_id: str | None,
        limit: int,
    ) -> Sequence[MembershipCompany]: ...


class UnitOfWork(Protocol):
    memberships: MembershipRepository

    def __enter__(self) -> "UnitOfWork": ...

    def __exit__(self, error_type, error, traceback) -> bool | None: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> UnitOfWork: ...


@dataclass(frozen=True, slots=True)
class ListMyCompanies:
    _unit_of_work_factory: UnitOfWorkFactory

    def execute(
        self,
        principal: AuthenticatedUser | None,
        *,
        limit: int = 100,
        after_company_id: str | None = None,
        after_public_id: str | None = None,
    ) -> Sequence[MembershipCompany]:
        account_user_id = (
            principal.user_id
            if principal is not None
            else None
        )
        if not isinstance(account_user_id, str) or not account_user_id.strip():
            raise AuthenticationRequired
        if not isinstance(limit, int) or not 1 <= limit <= 200:
            raise InvalidMembershipQuery
        if (after_company_id is None) != (after_public_id is None):
            raise InvalidMembershipQuery

        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.memberships.list_for_account(
                account_user_id,
                after_company_id=after_company_id,
                after_public_id=after_public_id,
                limit=limit,
            )
