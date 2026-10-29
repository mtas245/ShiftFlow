from dataclasses import dataclass
from typing import Protocol

from app.domain.company import Company
from app.domain.company_membership import CompanyMembership, CompanyRole


class CompanyRepository(Protocol):
    def add(self, company: Company) -> None: ...

    def find_by_joining_code(self, joining_code: str) -> Company | None: ...


class CompanyMembershipRepository(Protocol):
    def add(self, membership: CompanyMembership) -> None: ...

    def get(self, company_id: str, user_id: str) -> CompanyMembership | None: ...


class UnitOfWork(Protocol):
    companies: CompanyRepository
    memberships: CompanyMembershipRepository

    def __enter__(self) -> "UnitOfWork": ...

    def __exit__(self, error_type, error, traceback) -> bool | None: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> UnitOfWork: ...


class JoiningCodeGenerator(Protocol):
    def generate(self) -> str: ...


class JoiningCodeConflict(Exception):
    """Raised when persistence rejects a globally duplicate joining code."""


class JoiningCodeUnavailable(Exception):
    code = "JOINING_CODE_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    user_id: str


@dataclass(frozen=True, slots=True)
class RegisterCompanyCommand:
    company_name: str
    business_email: str
    business_phone: str
    business_address: str
    industry: str
    display_name: str


class RegisterCompany:
    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        joining_code_generator: JoiningCodeGenerator,
    ):
        self._unit_of_work_factory = unit_of_work_factory
        self._joining_code_generator = joining_code_generator

    def execute(
        self,
        authenticated_user: AuthenticatedUser,
        command: RegisterCompanyCommand,
    ) -> Company:
        for _ in range(5):
            company = Company.create(
                company_name=command.company_name,
                business_email=command.business_email,
                business_phone=command.business_phone,
                business_address=command.business_address,
                industry=command.industry,
                joining_code=self._joining_code_generator.generate(),
            )
            membership = CompanyMembership.create(
                company_id=company.id,
                user_id=authenticated_user.user_id,
                role=CompanyRole.ADMINISTRATOR,
                account_user_id=authenticated_user.user_id,
                display_name=command.display_name,
            )

            try:
                with self._unit_of_work_factory() as unit_of_work:
                    unit_of_work.companies.add(company)
                    unit_of_work.memberships.add(membership)
            except JoiningCodeConflict:
                continue

            return company

        raise JoiningCodeUnavailable
