from functools import lru_cache
import secrets

from sqlalchemy.engine import Engine

from app.application.account_lifecycle import AccountLifecycle, EmailDelivery
from app.application.authenticate_user import AuthenticateUser
from app.application.create_user import CreateUser
from app.application.join_company import JoinCompany
from app.application.list_my_companies import ListMyCompanies
from app.application.list_company_employees import ListCompanyEmployees
from app.application.manage_availability import ManageAvailability
from app.application.manage_shifts import ManageShifts
from app.application.register_company import RegisterCompany
from app.infrastructure.email_delivery import PostmarkEmailDelivery
from app.infrastructure.joining_code_generator import SecureJoiningCodeGenerator
from app.infrastructure.password_hasher import Argon2PasswordHasher
from app.infrastructure.rate_limiter import PostgresRateLimiter
from app.infrastructure.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory


@lru_cache(maxsize=1)
def _dummy_password_hash() -> str:
    return Argon2PasswordHasher().hash(secrets.token_urlsafe(32))


def build_authenticate_user(engine: Engine) -> AuthenticateUser:
    return AuthenticateUser(
        unit_of_work_factory=SqlAlchemyUnitOfWorkFactory(engine),
        password_hasher=Argon2PasswordHasher(),
        dummy_password_hash=_dummy_password_hash(),
    )


def build_account_lifecycle(
    engine: Engine,
    email_delivery: EmailDelivery,
) -> AccountLifecycle:
    return AccountLifecycle(
        unit_of_work_factory=SqlAlchemyUnitOfWorkFactory(engine),
        password_hasher=Argon2PasswordHasher(),
        email_delivery=email_delivery,
    )


def build_postmark_email_delivery(
    *,
    server_token: str,
    from_address: str,
) -> PostmarkEmailDelivery:
    return PostmarkEmailDelivery(
        server_token=server_token,
        from_address=from_address,
    )


def build_postgres_rate_limiter(
    engine: Engine,
    *,
    hmac_secret: str,
) -> PostgresRateLimiter:
    return PostgresRateLimiter(engine, hmac_secret=hmac_secret)


def build_join_company(engine: Engine) -> JoinCompany:
    return JoinCompany(SqlAlchemyUnitOfWorkFactory(engine))


def build_list_my_companies(engine: Engine) -> ListMyCompanies:
    return ListMyCompanies(SqlAlchemyUnitOfWorkFactory(engine))


def build_list_company_employees(engine: Engine) -> ListCompanyEmployees:
    return ListCompanyEmployees(SqlAlchemyUnitOfWorkFactory(engine))


def build_create_user(engine: Engine) -> CreateUser:
    return CreateUser(
        SqlAlchemyUnitOfWorkFactory(engine),
        Argon2PasswordHasher(),
    )


def build_manage_availability(engine: Engine) -> ManageAvailability:
    return ManageAvailability(SqlAlchemyUnitOfWorkFactory(engine))


def build_manage_shifts(engine: Engine) -> ManageShifts:
    return ManageShifts(SqlAlchemyUnitOfWorkFactory(engine))


def build_register_company(engine: Engine) -> RegisterCompany:
    return RegisterCompany(
        unit_of_work_factory=SqlAlchemyUnitOfWorkFactory(engine),
        joining_code_generator=SecureJoiningCodeGenerator(),
    )
