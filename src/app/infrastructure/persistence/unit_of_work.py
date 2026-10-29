import hashlib

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.application.account_lifecycle import AccountLifecyclePersistenceError
from app.application.authenticate_user import AuthenticationPersistenceError
from app.infrastructure.persistence.repositories import (
    SqlAlchemyAccountActionTokenRepository,
    SqlAlchemyAvailabilityRepository,
    SqlAlchemyCompanyMembershipRepository,
    SqlAlchemyCompanyRepository,
    SqlAlchemySessionRepository,
    SqlAlchemyShiftAssignmentRepository,
    SqlAlchemyShiftRepository,
    SqlAlchemyUserRepository,
)


class SqlAlchemyUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]):
        self._session_factory = session_factory
        self._session: Session | None = None

    def __enter__(self) -> "SqlAlchemyUnitOfWork":
        self._session = self._session_factory()
        self.users = SqlAlchemyUserRepository(self._session)
        self.sessions = SqlAlchemySessionRepository(self._session)
        self.action_tokens = SqlAlchemyAccountActionTokenRepository(self._session)
        self.companies = SqlAlchemyCompanyRepository(self._session)
        self.memberships = SqlAlchemyCompanyMembershipRepository(self._session)
        self.availabilities = SqlAlchemyAvailabilityRepository(self._session)
        self.shifts = SqlAlchemyShiftRepository(self._session)
        self.assignments = SqlAlchemyShiftAssignmentRepository(self._session)
        return self

    def lock_employee(self, employee_id: str) -> None:
        if self._session is None:
            raise RuntimeError("Unit of Work must be entered before locking")
        if self._session.get_bind().dialect.name != "postgresql":
            raise RuntimeError("Employee advisory locks require PostgreSQL")
        lock_key = int.from_bytes(
            hashlib.sha256(employee_id.encode("utf-8")).digest()[:8],
            byteorder="big",
            signed=True,
        )
        self._session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": lock_key},
        )

    def __exit__(self, error_type, _error, _traceback) -> bool:
        del _error, _traceback
        session = self._session
        if session is None:
            return False

        try:
            if error_type is None:
                session.commit()
            else:
                session.rollback()
        except SQLAlchemyError:
            session.rollback()
            if session.info.get("account_lifecycle_operation"):
                raise AccountLifecyclePersistenceError from None
            if session.info.get("authentication_operation"):
                raise AuthenticationPersistenceError from None
            raise
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
            self._session = None
        return False


class SqlAlchemyUnitOfWorkFactory:
    def __init__(self, engine: Engine):
        self._session_factory = sessionmaker(bind=engine)

    def __call__(self) -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(self._session_factory)
