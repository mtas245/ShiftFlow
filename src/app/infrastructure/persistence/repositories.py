from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application.register_company import (
    CompanyMembershipRepository,
    CompanyRepository,
    JoiningCodeConflict,
)
from app.domain.company import Company
from app.domain.company_membership import CompanyMembership
from app.infrastructure.persistence.models import (
    CompanyMembershipRecord,
    CompanyRecord,
)


class SqlAlchemyCompanyRepository(CompanyRepository):
    def __init__(self, session: Session):
        self._session = session

    def add(self, company: Company) -> None:
        self._session.add(
            CompanyRecord(
                id=company.id,
                company_name=company.company_name,
                business_email=company.business_email,
                business_phone=company.business_phone,
                business_address=company.business_address,
                industry=company.industry,
                joining_code=company.joining_code,
            )
        )
        try:
            self._session.flush()
        except IntegrityError as error:
            constraint_name = getattr(
                getattr(error.orig, "diag", None), "constraint_name", None
            )
            if constraint_name == "uq_companies_joining_code":
                raise JoiningCodeConflict from error
            raise


class SqlAlchemyCompanyMembershipRepository(CompanyMembershipRepository):
    def __init__(self, session: Session):
        self._session = session

    def add(self, membership: CompanyMembership) -> None:
        self._session.add(
            CompanyMembershipRecord(
                company_id=membership.company_id,
                user_id=membership.user_id,
                role=membership.role.value,
            )
        )
        self._session.flush()
