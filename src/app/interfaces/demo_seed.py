"""Development-only demo data seeding for local presentations.

The seed runs only against the Docker Compose development database, uses the
application services (registration, email verification, company registration,
joining, availability and scheduling), and never bypasses their rules.
"""

import argparse
import json
import os
import secrets
import sys
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import ArgumentError

from app.application.account_lifecycle import AccountLifecycle
from app.application.join_company import JoinCompany, JoinCompanyCommand
from app.application.list_company_employees import ListCompanyEmployees
from app.application.list_my_companies import ListMyCompanies
from app.application.manage_availability import (
    AvailabilityOverlap,
    ManageAvailability,
)
from app.application.manage_shifts import (
    ManageShifts,
    ShiftAssignmentConflict,
)
from app.application.register_company import (
    AuthenticatedUser,
    RegisterCompany,
    RegisterCompanyCommand,
)
from app.infrastructure.composition import (
    build_account_lifecycle,
    build_join_company,
    build_list_company_employees,
    build_list_my_companies,
    build_manage_availability,
    build_manage_shifts,
    build_register_company,
)
from app.infrastructure.persistence.unit_of_work import (
    SqlAlchemyUnitOfWorkFactory,
)


OPT_IN_VARIABLE = "SHIFTFLOW_DEMO_SEED"
OPT_IN_VALUE = "local-development"
CREDENTIALS_FILE_VARIABLE = "SHIFTFLOW_DEMO_CREDENTIALS_FILE"
DEFAULT_CREDENTIALS_FILE = "/demo/demo-credentials.json"

DEMO_ADMIN_EMAIL = "demo.admin@demo.shiftflow.example"
DEMO_EMPLOYEE_EMAIL = "demo.employee@demo.shiftflow.example"
DEMO_COMPANY_NAME = "Alpenblick Cafe Demo AG"
DEMO_ADMIN_NAME = "Alex Admin (Demo)"
DEMO_EMPLOYEE_NAME = "Emma Employee (Demo)"

_LOCAL_DATABASE_HOSTS = {"db", "localhost", "127.0.0.1", "::1"}
_LOCAL_ORIGIN_HOSTS = {"localhost", "127.0.0.1"}
_LOCAL_DATABASE = "shiftflow_dev"
_LOCAL_DATABASE_USER = "shiftflow_dev_user"
_TIME_ZONE = ZoneInfo("Europe/Zurich")
_WEEKS = 2


class DemoSeedRefused(RuntimeError):
    pass


class DemoSeedFailed(RuntimeError):
    pass


def validate_local_demo_environment(
    environ: Mapping[str, str], *, confirmed: bool
) -> str:
    if not confirmed:
        raise DemoSeedRefused("the --confirm-local-demo flag is required")
    if environ.get(OPT_IN_VARIABLE) != OPT_IN_VALUE:
        raise DemoSeedRefused(
            f"{OPT_IN_VARIABLE}={OPT_IN_VALUE} must be set explicitly"
        )

    database_url = environ.get("DATABASE_URL")
    try:
        url = make_url(database_url) if database_url else None
    except ArgumentError:
        url = None
    if (
        url is None
        or url.drivername != "postgresql+psycopg"
        or url.host not in _LOCAL_DATABASE_HOSTS
        or url.database != _LOCAL_DATABASE
        or url.username != _LOCAL_DATABASE_USER
    ):
        raise DemoSeedRefused(
            "DATABASE_URL is not the local Docker Compose development database"
        )

    origin = urlparse(environ.get("PUBLIC_ORIGIN") or "")
    if (
        origin.scheme != "https"
        or origin.hostname not in _LOCAL_ORIGIN_HOSTS
        or origin.path not in {"", "/"}
    ):
        raise DemoSeedRefused("PUBLIC_ORIGIN is not a local HTTPS origin")
    return database_url


class _CapturingEmailDelivery:
    """Hands tokens to the seed in-process; nothing is ever sent."""

    def __init__(self) -> None:
        self.tokens: dict[str, str] = {}

    def send_verification(self, email: str, token: str) -> None:
        self.tokens[email] = token

    def send_password_reset(self, email: str, token: str) -> None:
        self.tokens[email] = token

    def take(self, email: str) -> str | None:
        return self.tokens.pop(email, None)


def _read_state(path: Path) -> dict:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        raise DemoSeedFailed(f"cannot read {path}: {error}") from None
    return state if isinstance(state, dict) else {}


def _write_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)
        handle.write("\n")
    os.replace(temporary, path)


def _stored_password(state: dict, role: str) -> str | None:
    password = (state.get(role) or {}).get("password")
    return password if isinstance(password, str) and len(password) >= 12 else None


def _ensure_account(
    engine: Engine,
    lifecycle: AccountLifecycle,
    delivery: _CapturingEmailDelivery,
    email: str,
    stored_password: str | None,
) -> str:
    with SqlAlchemyUnitOfWorkFactory(engine)() as unit_of_work:
        account = unit_of_work.users.find_account_by_email(email)

    password = stored_password or secrets.token_urlsafe(18)
    if account is None:
        lifecycle.register(email, password)
        token = delivery.take(email)
        if token is None:
            raise DemoSeedFailed(f"registration for {email} did not complete")
        lifecycle.verify_email(token)
        return password

    if not account.email_verified:
        lifecycle.request_email_verification(email)
        token = delivery.take(email)
        if token is None:
            raise DemoSeedFailed(f"verification for {email} did not start")
        lifecycle.verify_email(token)
    if stored_password is None:
        lifecycle.request_password_reset(email)
        token = delivery.take(email)
        if token is None:
            raise DemoSeedFailed(f"password recovery for {email} did not start")
        lifecycle.reset_password(token, password)
    return password


def _user_id(engine: Engine, email: str) -> str:
    with SqlAlchemyUnitOfWorkFactory(engine)() as unit_of_work:
        account = unit_of_work.users.find_account_by_email(email)
    if account is None:
        raise DemoSeedFailed(f"account {email} is missing")
    return account.id


def _find_company_id(
    list_companies: ListMyCompanies, principal: AuthenticatedUser
) -> str | None:
    for membership in list_companies.execute(principal, limit=200):
        if membership.company_name == DEMO_COMPANY_NAME:
            return membership.company_id
    return None


def _local(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=_TIME_ZONE)


def _week_schedule(
    monday: date,
) -> tuple[list[tuple[datetime, datetime]], list[tuple[datetime, datetime, bool]]]:
    day = [monday + timedelta(days=offset) for offset in range(6)]
    availability = [(_local(day[i], 8), _local(day[i], 18)) for i in range(5)]
    availability.append((_local(day[4], 20), _local(day[5], 2)))
    availability.append((_local(day[5], 9), _local(day[5], 15)))
    shifts = [
        (_local(day[0], 8), _local(day[0], 16), True),
        (_local(day[1], 9), _local(day[1], 13), True),
        (_local(day[1], 13), _local(day[1], 17), True),
        (_local(day[2], 10), _local(day[2], 18), False),
        (_local(day[3], 8), _local(day[3], 12), False),
        (_local(day[4], 20), _local(day[5], 2), True),
    ]
    return availability, shifts


def seed_demo_data(
    engine: Engine,
    credentials_path: Path,
    *,
    now: datetime | None = None,
) -> None:
    """Create or complete the demo data. Safe to run repeatedly."""
    now = now or datetime.now(timezone.utc)
    state = _read_state(credentials_path)
    delivery = _CapturingEmailDelivery()
    lifecycle = build_account_lifecycle(engine, delivery)

    accounts = (
        ("administrator", DEMO_ADMIN_EMAIL),
        ("employee", DEMO_EMPLOYEE_EMAIL),
    )
    for role, email in accounts:
        password = _ensure_account(
            engine, lifecycle, delivery, email, _stored_password(state, role)
        )
        state[role] = {"email": email, "password": password}
        _write_state(credentials_path, state)

    administrator = AuthenticatedUser(_user_id(engine, DEMO_ADMIN_EMAIL))
    employee = AuthenticatedUser(_user_id(engine, DEMO_EMPLOYEE_EMAIL))
    list_companies = build_list_my_companies(engine)

    company_id = _find_company_id(list_companies, administrator)
    if company_id is None:
        company = build_register_company(engine).execute(
            administrator,
            RegisterCompanyCommand(
                company_name=DEMO_COMPANY_NAME,
                business_email="hello@demo.shiftflow.example",
                business_phone="+41310000000",
                business_address="Beispielgasse 1, 3000 Bern",
                industry="Hospitality",
                display_name=DEMO_ADMIN_NAME,
            ),
        )
        company_id = company.id
        state["company"] = {
            "name": DEMO_COMPANY_NAME,
            "joining_code": company.joining_code,
        }
        _write_state(credentials_path, state)

    if _find_company_id(list_companies, employee) is None:
        joining_code = (state.get("company") or {}).get("joining_code")
        if not isinstance(joining_code, str):
            raise DemoSeedFailed(
                "the employee is not a member and the joining code is unknown"
            )
        build_join_company(engine).execute(
            employee,
            JoinCompanyCommand(
                joining_code=joining_code, display_name=DEMO_EMPLOYEE_NAME
            ),
        )

    membership_id = next(
        (
            item.membership_id
            for item in build_list_company_employees(engine).execute(
                administrator, company_id, limit=200
            )
            if item.display_name == DEMO_EMPLOYEE_NAME
        ),
        None,
    )
    if membership_id is None:
        raise DemoSeedFailed("the demo employee roster entry is missing")

    local_today = now.astimezone(_TIME_ZONE).date()
    first_monday = local_today - timedelta(days=local_today.weekday())
    window_start = _local(first_monday, 0) - timedelta(days=1)
    window_end = _local(first_monday + timedelta(weeks=_WEEKS), 0) + timedelta(
        days=1
    )
    availability_service = build_manage_availability(engine)
    shift_service = build_manage_shifts(engine)

    for week in range(_WEEKS):
        availability, shifts = _week_schedule(first_monday + timedelta(weeks=week))
        _seed_availability(
            availability_service,
            employee,
            company_id,
            availability,
            window_start,
            window_end,
        )
        _seed_shifts(
            shift_service,
            administrator,
            company_id,
            membership_id,
            shifts,
            window_start,
            window_end,
        )


def _seed_availability(
    service: ManageAvailability,
    employee: AuthenticatedUser,
    company_id: str,
    intervals: Sequence[tuple[datetime, datetime]],
    window_start: datetime,
    window_end: datetime,
) -> None:
    existing = {
        (item.start_at, item.end_at)
        for item in service.list_own(
            employee,
            company_id,
            from_at=window_start,
            to_at=window_end,
            limit=200,
        )
    }
    for start, end in intervals:
        utc_interval = (start.astimezone(timezone.utc), end.astimezone(timezone.utc))
        if utc_interval in existing:
            continue
        try:
            service.submit(employee, company_id, start, end)
        except AvailabilityOverlap:
            continue


def _seed_shifts(
    service: ManageShifts,
    administrator: AuthenticatedUser,
    company_id: str,
    membership_id: str,
    shifts: Sequence[tuple[datetime, datetime, bool]],
    window_start: datetime,
    window_end: datetime,
) -> None:
    existing = {
        (item.start_at, item.end_at): item
        for item in service.list_for_company(
            administrator,
            company_id,
            from_at=window_start,
            to_at=window_end,
            limit=200,
        )
    }
    for start, end, assigned in shifts:
        key = (start.astimezone(timezone.utc), end.astimezone(timezone.utc))
        shift = existing.get(key) or service.create(
            administrator, company_id, start, end
        )
        if assigned and shift.assignment is None:
            try:
                service.assign(administrator, company_id, shift.id, membership_id)
            except ShiftAssignmentConflict:
                continue


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> int:
    environ = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(
        description="Seed synthetic demo data into the local development database."
    )
    parser.add_argument("--confirm-local-demo", action="store_true")
    parser.add_argument("--credentials-file", default=None)
    arguments = parser.parse_args(argv)

    try:
        database_url = validate_local_demo_environment(
            environ, confirmed=arguments.confirm_local_demo
        )
    except DemoSeedRefused as error:
        print(f"Refusing to seed demo data: {error}.", file=sys.stderr)
        return 2

    credentials_path = Path(
        arguments.credentials_file
        or environ.get(CREDENTIALS_FILE_VARIABLE)
        or DEFAULT_CREDENTIALS_FILE
    )
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            database, username = connection.execute(
                text("SELECT current_database(), current_user")
            ).one()
        if database != _LOCAL_DATABASE or username != _LOCAL_DATABASE_USER:
            print(
                "Refusing to seed demo data: connected database is not the "
                "local development database.",
                file=sys.stderr,
            )
            return 2
        if not inspect(engine).has_table("users"):
            print(
                "Database schema is missing. Run the documented Alembic "
                "migration step first.",
                file=sys.stderr,
            )
            return 1
        seed_demo_data(engine, credentials_path)
    except DemoSeedFailed as error:
        print(f"Demo seeding failed: {error}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()

    print(f"Demo data is ready. Credentials were written to {credentials_path}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
