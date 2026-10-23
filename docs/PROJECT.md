# ShiftFlow

## Purpose

- **Problem:** Hospitality businesses need a consistent way to collect employee
  availability, manually plan shifts, and communicate schedules.
- **Users:** Company administrators and employees.
- **Capstone MVP (29 October 2026):** Company registration, employee joining
  with a secure company code, basic employee availability, shift creation, and
  shift assignment.
- **Out of scope for this MVP:** Automated shift assignment and non-scheduling
  HR or business-management features.

## Current implementation status

This is the intended architecture and target MVP, not a claim that the complete
application is already implemented. The current checkout contains:

- Domain entities `Company` and `CompanyMembership`.
- Application use case `RegisterCompany`.
- SQLAlchemy persistence models, repositories, and unit of work.
- Unit tests and in-memory/PostgreSQL integration tests for the registration
  slice.

The HTTP interface and application entry point, browser UI, production secure
joining-code generator, employee-joining use case, availability and shift
entities/use cases, Alembic configuration/migrations, E2E tests, Docker
execution, app-aware CI, and cloud deployment remain pending. See the
[prioritized backlog](TASKS.md). The test runs described there are recorded
project evidence; rerun the relevant commands as part of the capstone
validation.

## Architecture

The backend follows Clean Architecture; dependencies point inward:

`domain <- application <- interfaces <- infrastructure`

- `domain/` contains business entities and rules, independent of frameworks
  and storage.
- `application/` contains use-case services and coordinates domain behavior
  through abstractions.
- `interfaces/` will contain the FastAPI HTTP boundary and request/response
  schemas.
- `infrastructure/` implements persistence and external integrations.
- A minimal browser UI served by FastAPI is preferred for the MVP, subject to
  approval of ADR-004. A separate frontend application is not currently in
  scope.
- A company is the tenant boundary. Tenant-owned operations must authorize
  membership and scope access to the authorized company; a client-supplied
  company identifier is not authorization.

The MVP domain target is at least five meaningful entities:
`Company`, `CompanyMembership`, `Availability`, `Shift`, and
`ShiftAssignment`. The project target is at least two implemented application
use cases, including company registration and employee joining.

## Security and authentication boundary

Accepted decisions are recorded in
[ADR-001](adr/ADR-001-development-authentication-boundary.md),
[ADR-002](adr/ADR-002-tenant-isolation.md), and
[ADR-003](adr/ADR-003-employee-joining-code.md). The development identity
adapter must never be exposed by a public deployment. Startup/configuration
must fail closed if a development-only impersonation mechanism is selected in
public or production mode. The mechanism for authenticating demo users on a
public deployment must be resolved in ADR-005 before implementation.

The joining-code contract is eight uppercase alphanumeric characters, generated
with cryptographically secure randomness and protected by a database uniqueness
constraint; collision retry and safe failure behavior are defined in ADR-003
and UC-001. This contract is accepted, but its production implementation is
pending.

## Selected stack and local execution

- Python 3.12, FastAPI/Uvicorn, Pydantic, PostgreSQL, SQLAlchemy 2.x,
  Alembic, pytest, and HTTPX.
- PostgreSQL is the relational database. Do not commit credentials or secrets.
- Dependency profile: `environments/python/requirements.txt`.
- Runtime version: `environments/python/.python-version`.
- Docker-based local execution and database migrations are required backlog
  items; no Docker configuration or migration setup is currently available.
- Deployment platform, secrets/configuration, and post-deployment checks must be
  decided in ADR-006 before deployment implementation.

## Commands and validation status

| Activity | Command / status |
|---|---|
| Install Python dependencies | `python -m pip install -r environments/python/requirements.txt` |
| Unit tests | `python -m pytest tests\unit -q` |
| In-memory integration tests | `python -m pytest tests\integration -q` |
| PostgreSQL integration tests | `python -m pytest -m postgres tests\integration\test_postgres_company_registration.py -q`; requires the dedicated disposable local `SHIFTFLOW_TEST_DATABASE_URL` described in the tests |
| Full current backend test set | `python -m pytest tests\unit tests\integration -q` |
| Run application | Not runnable yet: the FastAPI entry point and HTTP interface have not been implemented |
| Docker / E2E / deployed smoke tests | Not available yet; see [TASKS.md](TASKS.md) |

The PostgreSQL tests validate their target and do not fall back to
`DATABASE_URL`. Use only a dedicated disposable test database; never point
destructive integration tests at a development or production database.

## AI-assisted development and evidence

The repository includes [AGENTS.md](../AGENTS.md), GitHub Copilot
instructions, and AI-SDLC phase skills. The workflow requires specification-led
development, RED → GREEN → REFACTOR, human review, and validation before
deployment. Record the actual coding agent, model and tools used, MCP context
integration and evidence of use, test runs, and teammate reviews in PRs or
presentation evidence. Do not infer team usage from the presence of workflow
files.

## Documentation

- [Prioritized backlog, Definition of Done, and presentation checklist](TASKS.md)
- [UC-001 — Company Registration](specs/UC-001-COMPANY-REGISTRATION.md)
- [ADR-001 — Development Authentication Boundary](adr/ADR-001-development-authentication-boundary.md)
- [ADR-002 — Tenant Isolation](adr/ADR-002-tenant-isolation.md)
- [ADR-003 — Employee Joining Code](adr/ADR-003-employee-joining-code.md)

UC-002 through UC-004 and ADR-004 through ADR-006 are identified in
[TASKS.md](TASKS.md) and must be prepared/approved before implementation of
their respective shared contracts. Update this document when those decisions
are accepted and the implementation status changes.
