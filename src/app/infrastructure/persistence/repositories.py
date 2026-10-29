from datetime import datetime

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.application.account_lifecycle import (
    AccountActionTokenRepository,
    AccountActionPurpose,
    AccountActionTokenToStore,
    AccountIdentity,
    AccountLifecycleSessionRepository,
    AccountLifecyclePersistenceError,
    AccountLifecycleUserRepository,
)
from app.application.create_user import DuplicateEmailConflict, UserRepository
from app.application.authenticate_user import (
    AccountCredentials,
    AuthenticationPersistenceError,
    SessionRepository,
    SessionToStore,
    StoredSession,
    UserAuthenticationRepository,
)
from app.application.join_company import DuplicateMembershipConflict
from app.application.list_my_companies import (
    MembershipCompany,
    MembershipDiscoveryUnavailable,
)
from app.application.list_company_employees import MembershipEmployee
from app.application.manage_availability import (
    AvailabilityRepository,
    MembershipRepository,
)
from app.application.manage_shifts import (
    ShiftAssignmentConflict,
    ShiftAssignmentRepository,
    ShiftRepository,
)
from app.application.register_company import (
    CompanyMembershipRepository,
    CompanyRepository,
    JoiningCodeConflict,
)
from app.domain.availability import Availability
from app.domain.company import Company
from app.domain.company_membership import CompanyMembership, CompanyRole
from app.domain.shift import Shift, ShiftAssignment
from app.domain.user import User
from app.infrastructure.persistence.models import (
    AvailabilityRecord,
    CompanyMembershipRecord,
    CompanyRecord,
    ShiftAssignmentRecord,
    ShiftRecord,
    AccountActionTokenRecord,
    UserRecord,
    UserSessionRecord,
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

    def find_by_joining_code(self, joining_code: str) -> Company | None:
        record = self._session.scalar(
            select(CompanyRecord).where(
                CompanyRecord.joining_code == joining_code
            )
        )
        if record is None:
            return None
        return Company(
            id=record.id,
            company_name=record.company_name,
            business_email=record.business_email,
            business_phone=record.business_phone,
            business_address=record.business_address,
            industry=record.industry,
            joining_code=record.joining_code,
        )


class SqlAlchemyUserRepository(
    UserRepository,
    UserAuthenticationRepository,
    AccountLifecycleUserRepository,
):
    def __init__(self, session: Session):
        self._session = session

    def add(self, user: User, password_hash: str) -> None:
        self._session.add(
            UserRecord(
                id=user.id,
                email=user.email,
                password_hash=password_hash,
            )
        )
        try:
            self._session.flush()
        except IntegrityError as error:
            constraint_name = getattr(
                getattr(error.orig, "diag", None), "constraint_name", None
            )
            if constraint_name == "uq_users_email":
                raise DuplicateEmailConflict from error
            raise

    def find_credentials_by_email(
        self, email: str
    ) -> AccountCredentials | None:
        self._session.info["authentication_operation"] = True
        try:
            record = self._session.scalar(
                select(UserRecord).where(UserRecord.email == email)
            )
        except SQLAlchemyError:
            raise AuthenticationPersistenceError from None
        if record is None:
            return None
        return AccountCredentials(
            user_id=record.id,
            password_hash=record.password_hash,
            email_verified=record.email_verified_at is not None,
        )

    def find_account_by_email(self, email: str) -> AccountIdentity | None:
        self._session.info["account_lifecycle_operation"] = True
        try:
            record = self._session.scalar(
                select(UserRecord).where(UserRecord.email == email)
            )
        except SQLAlchemyError:
            raise AccountLifecyclePersistenceError from None
        if record is None:
            return None
        return AccountIdentity(
            id=record.id,
            email=record.email,
            email_verified=record.email_verified_at is not None,
        )

    def mark_email_verified(self, user_id: str, verified_at: datetime) -> None:
        self._session.info["account_lifecycle_operation"] = True
        try:
            result = self._session.execute(
                update(UserRecord)
                .where(
                    UserRecord.id == user_id,
                    UserRecord.email_verified_at.is_(None),
                )
                .values(email_verified_at=verified_at)
            )
            if result.rowcount == 0:
                raise AccountLifecyclePersistenceError
            self._session.flush()
        except SQLAlchemyError:
            raise AccountLifecyclePersistenceError from None

    def update_password_hash(self, user_id: str, password_hash: str) -> None:
        self._session.info["account_lifecycle_operation"] = True
        try:
            result = self._session.execute(
                update(UserRecord)
                .where(UserRecord.id == user_id)
                .values(password_hash=password_hash)
            )
            if result.rowcount == 0:
                raise AccountLifecyclePersistenceError
            self._session.flush()
        except SQLAlchemyError:
            raise AccountLifecyclePersistenceError from None


class SqlAlchemySessionRepository(
    SessionRepository,
    AccountLifecycleSessionRepository,
):
    def __init__(self, session: Session):
        self._session = session

    def add(self, session: SessionToStore) -> None:
        self._session.info["authentication_operation"] = True
        try:
            self._session.add(
                UserSessionRecord(
                    id=session.id,
                    user_id=session.user_id,
                    token_hash=session.token_hash,
                    csrf_token_hash=session.csrf_token_hash,
                    created_at=session.created_at,
                    last_seen_at=session.last_seen_at,
                    expires_at=session.expires_at,
                )
            )
            self._session.flush()
        except SQLAlchemyError:
            raise AuthenticationPersistenceError from None

    def find_by_token_hash(self, token_hash: str) -> StoredSession | None:
        self._session.info["authentication_operation"] = True
        try:
            row = self._session.execute(
                select(UserSessionRecord, UserRecord.email_verified_at)
                .join(UserRecord, UserRecord.id == UserSessionRecord.user_id)
                .where(UserSessionRecord.token_hash == token_hash)
                .with_for_update(of=UserSessionRecord)
            ).one_or_none()
        except SQLAlchemyError:
            raise AuthenticationPersistenceError from None
        if row is None:
            return None
        record, email_verified_at = row
        return StoredSession(
            id=record.id,
            user_id=record.user_id,
            csrf_token_hash=record.csrf_token_hash,
            created_at=record.created_at,
            last_seen_at=record.last_seen_at,
            expires_at=record.expires_at,
            revoked_at=record.revoked_at,
            email_verified=email_verified_at is not None,
            previous_csrf_token_hash=record.previous_csrf_token_hash,
            previous_csrf_expires_at=record.previous_csrf_expires_at,
        )

    def update_last_seen(self, session_id: str, last_seen_at: datetime) -> None:
        self._session.info["authentication_operation"] = True
        try:
            self._session.execute(
                update(UserSessionRecord)
                .where(
                    UserSessionRecord.id == session_id,
                    UserSessionRecord.revoked_at.is_(None),
                )
                .values(last_seen_at=last_seen_at)
            )
            self._session.flush()
        except SQLAlchemyError:
            raise AuthenticationPersistenceError from None

    def revoke_by_token_hash(self, token_hash: str, revoked_at: datetime) -> None:
        self._session.info["authentication_operation"] = True
        try:
            self._session.execute(
                update(UserSessionRecord)
                .where(
                    UserSessionRecord.token_hash == token_hash,
                    UserSessionRecord.revoked_at.is_(None),
                )
                .values(revoked_at=revoked_at)
            )
            self._session.flush()
        except SQLAlchemyError:
            raise AuthenticationPersistenceError from None

    def rotate_csrf_token(
        self,
        session_id: str,
        csrf_token_hash: str,
        previous_csrf_expires_at: datetime,
    ) -> None:
        self._session.info["authentication_operation"] = True
        try:
            result = self._session.execute(
                update(UserSessionRecord)
                .where(
                    UserSessionRecord.id == session_id,
                    UserSessionRecord.revoked_at.is_(None),
                )
                .values(
                    previous_csrf_token_hash=UserSessionRecord.csrf_token_hash,
                    previous_csrf_expires_at=previous_csrf_expires_at,
                    csrf_token_hash=csrf_token_hash,
                )
            )
            if result.rowcount != 1:
                raise AuthenticationPersistenceError
            self._session.flush()
        except SQLAlchemyError:
            raise AuthenticationPersistenceError from None

    def revoke_all_for_user(self, user_id: str, revoked_at: datetime) -> None:
        self._session.info["account_lifecycle_operation"] = True
        try:
            self._session.execute(
                update(UserSessionRecord)
                .where(
                    UserSessionRecord.user_id == user_id,
                    UserSessionRecord.revoked_at.is_(None),
                )
                .values(revoked_at=revoked_at)
            )
            self._session.flush()
        except SQLAlchemyError:
            raise AccountLifecyclePersistenceError from None


class SqlAlchemyAccountActionTokenRepository(AccountActionTokenRepository):
    def __init__(self, session: Session):
        self._session = session

    def add(self, token: AccountActionTokenToStore) -> None:
        self._session.info["account_lifecycle_operation"] = True
        try:
            self._session.add(
                AccountActionTokenRecord(
                    id=token.id,
                    user_id=token.user_id,
                    purpose=token.purpose.value,
                    token_digest=token.token_digest,
                    created_at=token.created_at,
                    expires_at=token.expires_at,
                )
            )
            self._session.flush()
        except SQLAlchemyError:
            raise AccountLifecyclePersistenceError from None

    def revoke_active_for_user(
        self,
        user_id: str,
        purpose: AccountActionPurpose,
        revoked_at: datetime,
    ) -> None:
        self._session.info["account_lifecycle_operation"] = True
        try:
            self._session.execute(
                update(AccountActionTokenRecord)
                .where(
                    AccountActionTokenRecord.user_id == user_id,
                    AccountActionTokenRecord.purpose == purpose.value,
                    AccountActionTokenRecord.consumed_at.is_(None),
                    AccountActionTokenRecord.revoked_at.is_(None),
                )
                .values(revoked_at=revoked_at)
            )
            self._session.flush()
        except SQLAlchemyError:
            raise AccountLifecyclePersistenceError from None

    def activate(self, token_digest: str, activated_at: datetime) -> bool:
        self._session.info["account_lifecycle_operation"] = True
        try:
            result = self._session.execute(
                update(AccountActionTokenRecord)
                .where(
                    AccountActionTokenRecord.token_digest == token_digest,
                    AccountActionTokenRecord.activated_at.is_(None),
                    AccountActionTokenRecord.consumed_at.is_(None),
                    AccountActionTokenRecord.revoked_at.is_(None),
                )
                .values(activated_at=activated_at)
            )
            self._session.flush()
            return result.rowcount > 0
        except SQLAlchemyError:
            raise AccountLifecyclePersistenceError from None

    def revoke(self, token_digest: str, revoked_at: datetime) -> None:
        self._session.info["account_lifecycle_operation"] = True
        try:
            self._session.execute(
                update(AccountActionTokenRecord)
                .where(
                    AccountActionTokenRecord.token_digest == token_digest,
                    AccountActionTokenRecord.consumed_at.is_(None),
                    AccountActionTokenRecord.revoked_at.is_(None),
                )
                .values(revoked_at=revoked_at)
            )
            self._session.flush()
        except SQLAlchemyError:
            raise AccountLifecyclePersistenceError from None

    def consume(
        self,
        token_digest: str,
        purpose: AccountActionPurpose,
        consumed_at: datetime,
    ) -> str | None:
        self._session.info["account_lifecycle_operation"] = True
        try:
            user_id = self._session.scalar(
                update(AccountActionTokenRecord)
                .where(
                    AccountActionTokenRecord.token_digest == token_digest,
                    AccountActionTokenRecord.purpose == purpose.value,
                    AccountActionTokenRecord.expires_at > consumed_at,
                    AccountActionTokenRecord.activated_at.is_not(None),
                    AccountActionTokenRecord.consumed_at.is_(None),
                    AccountActionTokenRecord.revoked_at.is_(None),
                )
                .values(consumed_at=consumed_at)
                .returning(AccountActionTokenRecord.user_id)
            )
            self._session.flush()
            return user_id
        except SQLAlchemyError:
            raise AccountLifecyclePersistenceError from None


class SqlAlchemyCompanyMembershipRepository(
    CompanyMembershipRepository, MembershipRepository
):
    def __init__(self, session: Session):
        self._session = session

    def add(self, membership: CompanyMembership) -> None:
        self._session.add(
            CompanyMembershipRecord(
                company_id=membership.company_id,
                user_id=membership.user_id,
                role=membership.role.value,
                account_user_id=membership.account_user_id,
                public_id=membership.public_id,
                display_name=membership.display_name,
            )
        )
        try:
            self._session.flush()
        except IntegrityError as error:
            constraint_name = getattr(
                getattr(error.orig, "diag", None), "constraint_name", None
            )
            if constraint_name == "uq_company_memberships_company_user":
                raise DuplicateMembershipConflict from error
            raise

    def get(self, company_id: str, user_id: str) -> CompanyMembership | None:
        record = self._session.scalar(
            select(CompanyMembershipRecord).where(
                CompanyMembershipRecord.company_id == company_id,
                CompanyMembershipRecord.user_id == user_id,
            )
        )
        if record is None:
            return None
        return CompanyMembership.create(
            company_id=record.company_id,
            user_id=record.user_id,
            role=CompanyRole(record.role),
            account_user_id=record.account_user_id,
            public_id=record.public_id,
            display_name=record.display_name,
        )

    def get_for_account(
        self, company_id: str, account_user_id: str
    ) -> CompanyMembership | None:
        try:
            record = self._session.scalar(
                select(CompanyMembershipRecord).where(
                    CompanyMembershipRecord.company_id == company_id,
                    CompanyMembershipRecord.account_user_id == account_user_id,
                )
            )
        except SQLAlchemyError:
            raise MembershipDiscoveryUnavailable from None
        if record is None:
            return None
        return CompanyMembership.create(
            company_id=record.company_id,
            user_id=record.user_id,
            role=CompanyRole(record.role),
            account_user_id=record.account_user_id,
            public_id=record.public_id,
            display_name=record.display_name,
        )

    def get_by_public_id(
        self, company_id: str, public_id: str
    ) -> CompanyMembership | None:
        record = self._session.scalar(
            select(CompanyMembershipRecord).where(
                CompanyMembershipRecord.company_id == company_id,
                CompanyMembershipRecord.public_id == public_id,
                CompanyMembershipRecord.account_user_id.is_not(None),
            )
        )
        if record is None:
            return None
        return CompanyMembership.create(
            company_id=record.company_id,
            user_id=record.user_id,
            role=CompanyRole(record.role),
            account_user_id=record.account_user_id,
            public_id=record.public_id,
            display_name=record.display_name,
        )

    def list_linked_employees(
        self,
        company_id: str,
        *,
        after_public_id: str | None,
        limit: int,
    ) -> list[MembershipEmployee]:
        try:
            statement = select(
                CompanyMembershipRecord.public_id,
                CompanyMembershipRecord.display_name,
            ).where(
                CompanyMembershipRecord.company_id == company_id,
                CompanyMembershipRecord.role == CompanyRole.EMPLOYEE.value,
                CompanyMembershipRecord.account_user_id.is_not(None),
                CompanyMembershipRecord.display_name.is_not(None),
            )
            if after_public_id is not None:
                statement = statement.where(
                    CompanyMembershipRecord.public_id > after_public_id
                )
            rows = self._session.execute(
                statement.order_by(CompanyMembershipRecord.public_id).limit(
                    limit + 1
                )
            ).all()
        except SQLAlchemyError:
            raise MembershipDiscoveryUnavailable from None
        employees: list[MembershipEmployee] = []
        for public_id, display_name in rows:
            if display_name is None:
                raise MembershipDiscoveryUnavailable
            employees.append(
                MembershipEmployee(
                    membership_id=public_id,
                    display_name=display_name,
                )
            )
        return employees

    def list_for_account(
        self,
        account_user_id: str,
        *,
        after_company_id: str | None,
        after_public_id: str | None,
        limit: int,
    ) -> list[MembershipCompany]:
        try:
            statement = (
                select(
                    CompanyMembershipRecord.public_id,
                    CompanyMembershipRecord.company_id,
                    CompanyRecord.company_name,
                    CompanyMembershipRecord.role,
                )
                .join(
                    CompanyRecord,
                    CompanyRecord.id == CompanyMembershipRecord.company_id,
                )
                .where(
                    CompanyMembershipRecord.account_user_id == account_user_id
                )
            )
            if after_company_id is not None and after_public_id is not None:
                statement = statement.where(
                    or_(
                        CompanyMembershipRecord.company_id > after_company_id,
                        and_(
                            CompanyMembershipRecord.company_id == after_company_id,
                            CompanyMembershipRecord.public_id > after_public_id,
                        ),
                    )
                )
            rows = self._session.execute(
                statement.order_by(
                    CompanyMembershipRecord.company_id,
                    CompanyMembershipRecord.public_id,
                ).limit(limit + 1)
            ).all()
        except SQLAlchemyError:
            raise MembershipDiscoveryUnavailable from None
        return [
            MembershipCompany(
                membership_id=public_id,
                company_id=company_id,
                company_name=company_name,
                role=role,
            )
            for public_id, company_id, company_name, role in rows
        ]


class SqlAlchemyAvailabilityRepository(AvailabilityRepository):
    def __init__(self, session: Session):
        self._session = session

    def add(self, availability: Availability) -> None:
        self._session.add(
            AvailabilityRecord(
                id=availability.id,
                company_id=availability.company_id,
                employee_id=availability.employee_id,
                start_at=availability.start_at,
                end_at=availability.end_at,
            )
        )
        self._session.flush()

    def update(self, availability: Availability) -> None:
        record = self._session.scalar(
            select(AvailabilityRecord).where(
                AvailabilityRecord.company_id == availability.company_id,
                AvailabilityRecord.id == availability.id,
            )
        )
        if record is None:
            raise LookupError("Availability record no longer exists")
        record.start_at = availability.start_at
        record.end_at = availability.end_at
        self._session.flush()

    def get(self, company_id: str, availability_id: str) -> Availability | None:
        record = self._session.scalar(
            select(AvailabilityRecord).where(
                AvailabilityRecord.company_id == company_id,
                AvailabilityRecord.id == availability_id,
            )
        )
        return self._to_domain(record) if record is not None else None

    def list_for_employee(
        self, company_id: str, employee_id: str
    ) -> list[Availability]:
        records = self._session.scalars(
            select(AvailabilityRecord)
            .where(
                AvailabilityRecord.company_id == company_id,
                AvailabilityRecord.employee_id == employee_id,
            )
            .order_by(AvailabilityRecord.start_at, AvailabilityRecord.id)
        ).all()
        return [self._to_domain(record) for record in records]

    def list_employee_window(
        self,
        company_id: str,
        employee_id: str,
        *,
        from_at: datetime,
        to_at: datetime,
        after_start: datetime | None,
        after_id: str | None,
        limit: int,
    ) -> list[Availability]:
        statement = select(AvailabilityRecord).where(
            AvailabilityRecord.company_id == company_id,
            AvailabilityRecord.employee_id == employee_id,
            AvailabilityRecord.start_at < to_at,
            AvailabilityRecord.end_at > from_at,
        )
        statement = self._after_cursor(statement, after_start, after_id)
        records = self._session.scalars(
            statement.order_by(
                AvailabilityRecord.start_at,
                AvailabilityRecord.id,
            ).limit(limit + 1)
        ).all()
        return [self._to_domain(record) for record in records]

    def list_company_window(
        self,
        company_id: str,
        *,
        from_at: datetime,
        to_at: datetime,
        after_start: datetime | None,
        after_id: str | None,
        limit: int,
    ) -> list[Availability]:
        statement = select(AvailabilityRecord).where(
            AvailabilityRecord.company_id == company_id,
            AvailabilityRecord.start_at < to_at,
            AvailabilityRecord.end_at > from_at,
        )
        statement = self._after_cursor(statement, after_start, after_id)
        records = self._session.scalars(
            statement.order_by(
                AvailabilityRecord.start_at,
                AvailabilityRecord.id,
            ).limit(limit + 1)
        ).all()
        return [self._to_domain(record) for record in records]

    @staticmethod
    def _after_cursor(statement, after_start, after_id):
        if after_start is not None and after_id is not None:
            statement = statement.where(
                or_(
                    AvailabilityRecord.start_at > after_start,
                    and_(
                        AvailabilityRecord.start_at == after_start,
                        AvailabilityRecord.id > after_id,
                    ),
                )
            )
        return statement

    @staticmethod
    def _to_domain(record: AvailabilityRecord) -> Availability:
        return Availability.create(
            id=record.id,
            company_id=record.company_id,
            employee_id=record.employee_id,
            start_at=record.start_at,
            end_at=record.end_at,
        )


class SqlAlchemyShiftRepository(ShiftRepository):
    def __init__(self, session: Session):
        self._session = session

    def add(self, shift: Shift) -> None:
        self._session.add(
            ShiftRecord(
                id=shift.id,
                company_id=shift.company_id,
                start_at=shift.start_at,
                end_at=shift.end_at,
            )
        )
        self._session.flush()

    def get(self, company_id: str, shift_id: str) -> Shift | None:
        record = self._session.scalar(
            select(ShiftRecord).where(
                ShiftRecord.company_id == company_id,
                ShiftRecord.id == shift_id,
            )
        )
        return self._to_shift(record) if record is not None else None

    def list_company_window(
        self,
        company_id: str,
        *,
        from_at: datetime,
        to_at: datetime,
        after_start: datetime | None,
        after_id: str | None,
        limit: int,
    ) -> list[Shift]:
        statement = (
            select(
                ShiftRecord,
                ShiftAssignmentRecord,
                CompanyMembershipRecord.public_id,
                CompanyMembershipRecord.display_name,
            )
            .outerjoin(
                ShiftAssignmentRecord,
                and_(
                    ShiftAssignmentRecord.shift_id == ShiftRecord.id,
                    ShiftAssignmentRecord.company_id == ShiftRecord.company_id,
                ),
            )
            .outerjoin(
                CompanyMembershipRecord,
                and_(
                    CompanyMembershipRecord.company_id
                    == ShiftAssignmentRecord.company_id,
                    CompanyMembershipRecord.user_id
                    == ShiftAssignmentRecord.employee_id,
                ),
            )
            .where(
                ShiftRecord.company_id == company_id,
                ShiftRecord.start_at < to_at,
                ShiftRecord.end_at > from_at,
            )
        )
        statement = self._after_cursor(statement, after_start, after_id)
        rows = self._session.execute(
            statement.order_by(ShiftRecord.start_at, ShiftRecord.id).limit(
                limit + 1
            )
        ).all()
        return [
            self._to_shift(shift, assignment, membership_id, display_name)
            for shift, assignment, membership_id, display_name in rows
        ]

    def list_employee_window(
        self,
        company_id: str,
        employee_id: str,
        *,
        from_at: datetime,
        to_at: datetime,
        after_start: datetime | None,
        after_id: str | None,
        limit: int,
    ) -> list[Shift]:
        statement = (
            select(
                ShiftRecord,
                ShiftAssignmentRecord,
                CompanyMembershipRecord.public_id,
                CompanyMembershipRecord.display_name,
            )
            .join(
                ShiftAssignmentRecord,
                and_(
                    ShiftAssignmentRecord.shift_id == ShiftRecord.id,
                    ShiftAssignmentRecord.company_id == ShiftRecord.company_id,
                ),
            )
            .join(
                CompanyMembershipRecord,
                and_(
                    CompanyMembershipRecord.company_id
                    == ShiftAssignmentRecord.company_id,
                    CompanyMembershipRecord.user_id
                    == ShiftAssignmentRecord.employee_id,
                ),
            )
            .where(
                ShiftRecord.company_id == company_id,
                ShiftAssignmentRecord.employee_id == employee_id,
                ShiftRecord.start_at < to_at,
                ShiftRecord.end_at > from_at,
            )
        )
        statement = self._after_cursor(statement, after_start, after_id)
        rows = self._session.execute(
            statement.order_by(ShiftRecord.start_at, ShiftRecord.id).limit(
                limit + 1
            )
        ).all()
        return [
            self._to_shift(shift, assignment, membership_id, display_name)
            for shift, assignment, membership_id, display_name in rows
        ]

    @staticmethod
    def _after_cursor(statement, after_start, after_id):
        if after_start is not None and after_id is not None:
            statement = statement.where(
                or_(
                    ShiftRecord.start_at > after_start,
                    and_(
                        ShiftRecord.start_at == after_start,
                        ShiftRecord.id > after_id,
                    ),
                )
            )
        return statement

    @staticmethod
    def _to_shift(
        record: ShiftRecord,
        assignment_record: ShiftAssignmentRecord | None = None,
        membership_id: str | None = None,
        display_name: str | None = None,
    ) -> Shift:
        shift = Shift.create(
            id=record.id,
            company_id=record.company_id,
            start_at=record.start_at,
            end_at=record.end_at,
        )
        if assignment_record is None:
            return shift
        assignment = ShiftAssignment.create(
            id=assignment_record.id,
            shift_id=assignment_record.shift_id,
            company_id=assignment_record.company_id,
            employee_id=assignment_record.employee_id,
            membership_id=membership_id,
            display_name=display_name,
        )
        return shift.with_assignment(assignment)


class SqlAlchemyShiftAssignmentRepository(ShiftAssignmentRepository):
    def __init__(self, session: Session):
        self._session = session

    def add(self, assignment: ShiftAssignment) -> None:
        self._session.add(
            ShiftAssignmentRecord(
                id=assignment.id,
                shift_id=assignment.shift_id,
                company_id=assignment.company_id,
                employee_id=assignment.employee_id,
            )
        )
        try:
            self._session.flush()
        except IntegrityError as error:
            constraint_name = getattr(
                getattr(error.orig, "diag", None), "constraint_name", None
            )
            if constraint_name == "uq_shift_assignments_one_per_shift":
                raise ShiftAssignmentConflict from error
            raise

    def get_for_shift(
        self, company_id: str, shift_id: str
    ) -> ShiftAssignment | None:
        record = self._session.scalar(
            select(ShiftAssignmentRecord).where(
                ShiftAssignmentRecord.company_id == company_id,
                ShiftAssignmentRecord.shift_id == shift_id,
            )
        )
        return self._to_domain(record) if record is not None else None

    def has_overlap(
        self, account_user_id: str, start_at: datetime, end_at: datetime
    ) -> bool:
        matching_shift = self._session.scalar(
            select(ShiftRecord.id)
            .join(
                ShiftAssignmentRecord,
                and_(
                    ShiftAssignmentRecord.shift_id == ShiftRecord.id,
                    ShiftAssignmentRecord.company_id == ShiftRecord.company_id,
                ),
            )
            .join(
                CompanyMembershipRecord,
                and_(
                    CompanyMembershipRecord.company_id
                    == ShiftAssignmentRecord.company_id,
                    CompanyMembershipRecord.user_id
                    == ShiftAssignmentRecord.employee_id,
                ),
            )
            .where(
                CompanyMembershipRecord.account_user_id == account_user_id,
                ShiftRecord.start_at < end_at,
                start_at < ShiftRecord.end_at,
            )
            .limit(1)
        )
        return matching_shift is not None

    @staticmethod
    def _to_domain(record: ShiftAssignmentRecord) -> ShiftAssignment:
        return ShiftAssignment.create(
            id=record.id,
            shift_id=record.shift_id,
            company_id=record.company_id,
            employee_id=record.employee_id,
        )
