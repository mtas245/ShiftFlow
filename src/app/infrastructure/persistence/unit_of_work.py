from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure.persistence.repositories import (
    SqlAlchemyCompanyMembershipRepository,
    SqlAlchemyCompanyRepository,
)


class SqlAlchemyUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]):
        self._session_factory = session_factory
        self._session: Session | None = None

    def __enter__(self) -> "SqlAlchemyUnitOfWork":
        self._session = self._session_factory()
        self.companies = SqlAlchemyCompanyRepository(self._session)
        self.memberships = SqlAlchemyCompanyMembershipRepository(self._session)
        return self

    def __exit__(self, error_type, error, traceback) -> bool:
        session = self._session
        if session is None:
            return False

        try:
            if error_type is None:
                session.commit()
            else:
                session.rollback()
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
