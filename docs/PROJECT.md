# ShiftFlow

## Purpose

- Project name: ShiftFlow
- User or organisational problem: Hospitality businesses need a consistent way to collect employee availability, manually plan shifts, and communicate schedule changes.
- Intended users: Company administrators and employees.
- In scope: Monthly availability submissions; staffing requirements; manual shift assignment; schedule publication; notifications for subsequent changes; employee acknowledgement of changes; separate data access for each company.
- Out of scope: Automated shift assignment and non-scheduling HR or business-management features.

## Architecture

ShiftFlow is a full-stack application. The browser frontend serves employees using the calendar and company administrators using the dashboard; it communicates with the backend through the HTTP API. The frontend framework is an open architecture decision to resolve during Design, before adding frontend-specific dependencies.

Use Clean Architecture for the backend with dependencies pointing inward:

`domain <- application <- interfaces <- infrastructure`

- `domain` contains business entities and rules, independent of frameworks and storage.
- `application` contains use-case services and coordinates domain behavior.
- `interfaces` contains the backend HTTP/API boundary and request/response schemas.
- `infrastructure` implements persistence and external notification integrations.
- `frontend/` contains the browser client and consumes the backend API; its framework remains undecided.
- Company is the tenant boundary: every tenant-owned operation must be scoped to the authorized company.

Selected backend stack: Python 3.12, FastAPI with Uvicorn, Pydantic, PostgreSQL, SQLAlchemy 2.x ORM, Alembic migrations, and pytest/httpx. Validate detailed domain entities and use cases during Specify/Design; the implementation must include at least five domain entities and two application use-case services, with TDD.

## Structure

```text
frontend/
src/app/
  domain/
  application/
  interfaces/
  infrastructure/
tests/
  unit/
  integration/
  e2e/
docs/
  specs/
environments/python/
```

The application and test directories are Bootstrap structure only; no application entry point, use cases, or tests have been implemented yet.

## Commands

| Activity | Command |
|---|---|
| Install | `python -m pip install -r environments/python/requirements.txt` |
| Unit tests | `python -m pytest tests/unit` |
| Integration tests | `python -m pytest tests/integration` |
| Run backend locally | `python -m uvicorn app.main:app --reload --app-dir src` |
| Run frontend locally | TBD after the frontend framework decision |
| Build/release | GitHub Actions release workflow on a `v*` tag; no application build artifact is configured |

The backend run and test commands become runnable as the application entry point and corresponding tests are added. The frontend command will be documented after its framework is selected. The CD workflow is an inactive template and is not configured for deployment.

## Dependencies

- Runtime: Python 3.12, recorded in `environments/python/.python-version`.
- Dependency profile: `environments/python/requirements.txt`; `scripts/setup-python.sh` copies it to a root `requirements.txt` when setting up a local environment.
- Frontend framework, runtime, and package manager: open architecture decisions; do not add frontend dependencies until decided.
- Database: PostgreSQL, configured through `DATABASE_URL` when persistence is implemented. Do not commit credentials or secrets.
