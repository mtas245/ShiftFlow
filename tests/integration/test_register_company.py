from types import SimpleNamespace

import pytest

from app.application.register_company import (
    AuthenticatedUser,
    JoiningCodeConflict,
    JoiningCodeUnavailable,
    RegisterCompany,
    RegisterCompanyCommand,
)


class InMemoryDatabase:
    def __init__(self):
        self.companies = []
        self.memberships = []
        self.joining_codes = set()
        self.fail_membership_add = False

    def unit_of_work(self):
        return InMemoryUnitOfWork(self)


class InMemoryUnitOfWork:
    def __init__(self, database):
        self.database = database
        self.pending_companies = []
        self.pending_memberships = []

    def __enter__(self):
        self.companies = InMemoryCompanyRepository(self)
        self.memberships = InMemoryMembershipRepository(self)
        return self

    def __exit__(self, error_type, error, traceback):
        if error_type is None:
            self.database.companies.extend(self.pending_companies)
            self.database.memberships.extend(self.pending_memberships)
            self.database.joining_codes.update(
                company.joining_code for company in self.pending_companies
            )
        return False


class InMemoryCompanyRepository:
    def __init__(self, unit_of_work):
        self.unit_of_work = unit_of_work

    def add(self, company):
        database = self.unit_of_work.database
        pending_codes = {
            existing.joining_code
            for existing in self.unit_of_work.pending_companies
        }
        if company.joining_code in database.joining_codes | pending_codes:
            raise JoiningCodeConflict
        self.unit_of_work.pending_companies.append(company)


class InMemoryMembershipRepository:
    def __init__(self, unit_of_work):
        self.unit_of_work = unit_of_work

    def add(self, membership):
        if self.unit_of_work.database.fail_membership_add:
            raise RuntimeError("simulated persistence failure")
        self.unit_of_work.pending_memberships.append(membership)


class SequenceCodeGenerator:
    def __init__(self, codes):
        self.codes = iter(codes)
        self.attempts = 0

    def generate(self):
        self.attempts += 1
        return next(self.codes)


def valid_command(**overrides):
    values = {
        "company_name": "North Star Cafe",
        "business_email": "  SCHEDULE@EXAMPLE.COM  ",
        "business_phone": "+41791234567",
        "business_address": "1 Main Street",
        "industry": "  Food   and\tHospitality  ",
        "display_name": "  Owner One  ",
    }
    values.update(overrides)
    return RegisterCompanyCommand(**values)


def test_registration_persists_company_and_creator_membership_atomically():
    database = InMemoryDatabase()
    database.memberships.append(
        SimpleNamespace(company_id="another-company", user_id="user-1")
    )
    database.companies.append(
        SimpleNamespace(business_email="schedule@example.com")
    )
    codes = SequenceCodeGenerator(["AB12CD34"])
    service = RegisterCompany(database.unit_of_work, codes)

    company = service.execute(
        AuthenticatedUser(user_id="user-1"), valid_command()
    )

    assert company.business_email == "SCHEDULE@example.com"
    assert company.industry == "Food and Hospitality"
    assert company.joining_code == "AB12CD34"
    assert len(database.companies) == 2
    assert len(database.memberships) == 2
    membership = database.memberships[-1]
    assert membership.company_id == company.id
    assert membership.user_id == "user-1"
    assert membership.role.value == "administrator"
    assert membership.account_user_id == "user-1"
    assert membership.display_name == "Owner One"
    assert membership.public_id


def test_registration_retries_globally_duplicate_joining_code():
    database = InMemoryDatabase()
    database.joining_codes.add("AB12CD34")
    codes = SequenceCodeGenerator(["AB12CD34", "EF56GH78"])
    service = RegisterCompany(database.unit_of_work, codes)

    company = service.execute(
        AuthenticatedUser(user_id="user-1"), valid_command()
    )

    assert company.joining_code == "EF56GH78"
    assert codes.attempts == 2
    assert database.joining_codes == {"AB12CD34", "EF56GH78"}


def test_fifth_joining_code_collision_aborts_without_partial_records():
    database = InMemoryDatabase()
    database.joining_codes.add("AB12CD34")
    codes = SequenceCodeGenerator(["AB12CD34"] * 5)
    service = RegisterCompany(database.unit_of_work, codes)

    with pytest.raises(JoiningCodeUnavailable) as error:
        service.execute(AuthenticatedUser(user_id="user-1"), valid_command())

    assert error.value.code == "JOINING_CODE_UNAVAILABLE"
    assert codes.attempts == 5
    assert database.companies == []
    assert database.memberships == []


def test_membership_persistence_failure_rolls_back_company():
    database = InMemoryDatabase()
    database.fail_membership_add = True
    service = RegisterCompany(
        database.unit_of_work, SequenceCodeGenerator(["AB12CD34"])
    )

    with pytest.raises(RuntimeError, match="simulated persistence failure"):
        service.execute(AuthenticatedUser(user_id="user-1"), valid_command())

    assert database.companies == []
    assert database.memberships == []
    assert database.joining_codes == set()


def test_invalid_company_input_does_not_persist_records():
    database = InMemoryDatabase()
    service = RegisterCompany(
        database.unit_of_work, SequenceCodeGenerator(["AB12CD34"])
    )

    with pytest.raises(ValueError):
        service.execute(
            AuthenticatedUser(user_id="user-1"),
            valid_command(business_email="not-an-email"),
        )

    assert database.companies == []
    assert database.memberships == []
    assert database.joining_codes == set()
