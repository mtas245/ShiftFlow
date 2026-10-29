# ShiftFlow

ShiftFlow is an FHNW university project (AI-assisted software development) for
hospitality **employee availability and shift scheduling**. Employees state
when they can work; administrators create shifts and assign only employees who
are available. Each company's data is isolated from every other company.

**Status:** Phase 3 (Develop), in progress. ShiftFlow is a locally tested MVP;
it is not deployed and not approved for production use (see
[Limitations](#limitations)).

## Use cases

| UC | Summary | Specification |
|----|---------|---------------|
| UC-001 | **Company registration:** a signed-in user registers a company and becomes its administrator; a joining code is generated. | [UC-001](docs/specs/UC-001-COMPANY-REGISTRATION.md) |
| UC-002 | **Employee joining:** a signed-in user joins a company with its joining code and becomes an employee. | [UC-002](docs/specs/UC-002-EMPLOYEE-JOINING.md) |
| UC-003 | **Employee availability:** employees submit and update their own date-specific availability intervals. | [UC-003](docs/specs/UC-003-EMPLOYEE-AVAILABILITY.md) |
| UC-004 | **Shift creation and assignment:** administrators create shifts and assign available employees; employees see their own shifts. | [UC-004](docs/specs/UC-004-SHIFT-CREATION-AND-ASSIGNMENT.md) |

Supporting features: account registration, email verification, login with
server-side sessions, CSRF protection and password recovery. Intervals are
stored in UTC with half-open `[start, end)` semantics, so overnight and
adjacent intervals work and overlaps are rejected. PostgreSQL advisory locks
prevent concurrent double-booking
([ADR-007](docs/adr/ADR-007-employee-scheduling-advisory-locks.md)).

## Technology stack

Python 3.12.10, FastAPI, PostgreSQL 16, SQLAlchemy, Alembic, Jinja2-rendered
pages with first-party JavaScript and CSS (no SPA framework), Docker / Docker
Compose, GitHub Actions and pytest (with Playwright for E2E).

## Architecture overview

Clean Architecture, with dependencies pointing inwards:
`domain ← application ← interfaces ← infrastructure`.

- `src/app/domain` - entities and business rules (users, companies,
  memberships, availability, shifts).
- `src/app/application` - use-case services and ports (unit-of-work protocols).
- `src/app/interfaces` - FastAPI routes, authentication/CSRF/Origin
  enforcement, browser pages, and the local demo-seed command.
- `src/app/infrastructure` - SQLAlchemy persistence, Alembic-managed schema,
  Argon2 hashing, rate limiting, email delivery adapter.
- `alembic/` migrations, `frontend/` browser assets, `tests/` unit and
  integration tests.

Details: [docs/PROJECT.md](docs/PROJECT.md).

## Local Docker development

Requirements: Windows with Docker Desktop (Docker Compose v2) and PowerShell,
run from the repository root. This setup uses a local PostgreSQL 16 database,
development-only credentials, and a self-signed HTTPS certificate for
`localhost`. The certificate is untrusted by browsers and is for local
development only.

```powershell
Copy-Item .env.example .env
docker compose build app
docker compose up -d db dev-tls
docker compose run --rm app python -m alembic upgrade head
docker compose up -d app
```

Open <https://localhost:8443>. Accept the browser's self-signed-certificate
warning for this local instance only. Check readiness at
<https://localhost:8443/health>. `docker compose down` stops the services but
preserves the development database volume. Do not use the example credentials
or local TLS certificate outside this isolated environment. Email delivery is
unconfigured by default, so account lifecycle routes that require email
delivery fail closed; this setup does not bypass verification.

`DATABASE_URL` and `PUBLIC_ORIGIN` are required for application startup.
`RATE_LIMIT_HMAC_SECRET` enables shared rate limiting. To enable account email
delivery, set both `POSTMARK_SERVER_TOKEN` and `EMAIL_FROM_ADDRESS` to
appropriate local/test values; never commit them. Production operation,
public access, and deployment are not authorized by this development setup.
ADR-006 remains proposed.

## Local demo data (development only)

Email delivery is not configured by default, so you cannot register and verify
an account by email locally. The opt-in `demo-seed` Compose service creates two
synthetic, verified accounts instead. It is never started by `docker compose
up`, is idempotent, and refuses to run unless **all** of these hold:
`SHIFTFLOW_DEMO_SEED=local-development` is set, the `--confirm-local-demo`
flag is passed (the Compose service passes it), `DATABASE_URL` is the Compose
development database (`shiftflow_dev` as `shiftflow_dev_user` on a local
host), and `PUBLIC_ORIGIN` is a local HTTPS origin. It does not disable email
verification, authentication, CSRF or tenant isolation: accounts are
registered and verified through the real account lifecycle service (the
verification token is handled in-process; no email is sent), and the company,
membership, availability and shifts are created through the normal
application services.

After the migration step above (the `db` service must be running):

```powershell
New-Item -ItemType Directory -Force .local\demo | Out-Null
$env:SHIFTFLOW_DEMO_SEED = "local-development"
docker compose run --rm demo-seed
Remove-Item Env:SHIFTFLOW_DEMO_SEED
```

Without the environment variable the command exits with an error and changes
nothing. Result:

| Item | Value |
|------|-------|
| Administrator | `demo.admin@demo.shiftflow.example` |
| Employee | `demo.employee@demo.shiftflow.example` |
| Company | "Alpenblick Cafe Demo AG" |
| Credentials file | `.local\demo\demo-credentials.json` |

Read the generated random passwords locally with
`Get-Content .local\demo\demo-credentials.json`, then sign in at
<https://localhost:8443/login>. The schedule covers the current and next week
in `Europe/Zurich`, including an overnight shift, adjacent shifts and some
unassigned shifts.

> **Warning:** never publish, commit, screenshot or share the demo passwords or
> any local secret (`.env`, `.local\`). The `.local/` folder is git-ignored;
> keep it that way. Re-running the seed keeps the stored passwords; if the
> credentials file is deleted, a re-run resets only the two demo accounts'
> passwords through the password-recovery service. No other account is
> touched. Use the seed only against the local development database.

## Python and PostgreSQL tests

Python 3.12.10 and PostgreSQL 16 are used by CI. Install the pinned test
dependencies with:

```powershell
python -m pip install -r environments/python/requirements-ci.lock
```

The PostgreSQL suite destructively resets its target. It refuses to run unless
`SHIFTFLOW_TEST_DATABASE_URL` identifies the dedicated loopback database
`shiftflow_test` authenticated as `shiftflow_test_user`. Never point this
variable at development or production data. Start a disposable test-only
PostgreSQL container (if port 5432 is occupied, choose another local port and
update the URL below):

```powershell
docker run --name shiftflow-test-db --rm -d `
  -e POSTGRES_DB=shiftflow_test `
  -e POSTGRES_USER=shiftflow_test_user `
  -e POSTGRES_PASSWORD=shiftflow_test_only_password `
  -p 127.0.0.1:5432:5432 `
  --health-cmd "pg_isready -U shiftflow_test_user -d shiftflow_test" `
  --health-interval 2s --health-timeout 3s --health-retries 30 `
  postgres:16-alpine@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea
$attempt = 0
do {
  Start-Sleep -Seconds 2
  $status = docker inspect --format '{{.State.Health.Status}}' shiftflow-test-db
  $attempt++
} while ($status -ne "healthy" -and $attempt -lt 30)
if ($status -ne "healthy") { throw "Disposable PostgreSQL test service did not become healthy" }
$env:SHIFTFLOW_TEST_DATABASE_URL = "postgresql+psycopg://shiftflow_test_user:shiftflow_test_only_password@127.0.0.1:5432/shiftflow_test"
$env:DATABASE_URL = $env:SHIFTFLOW_TEST_DATABASE_URL
$env:PUBLIC_ORIGIN = "https://localhost"
python -m pytest -m "not e2e" tests\unit tests\integration -q
```

Install the optional E2E dependencies and browser, then run the separately
identified real-app Playwright test:

```powershell
python -m pip install -r environments/python/requirements-e2e.txt
python -m playwright install chromium
python -m pytest -m e2e tests\integration\test_company_onboarding_e2e.py -q
docker stop shiftflow-test-db
```

## Continuous integration

`.github/workflows/ci.yml` defines four jobs: `structure` (artefact and
whitespace checks), `tests` (unit and PostgreSQL integration tests on
Python 3.12 with a PostgreSQL 16 service), `e2e` (real-app Playwright, kept
separate) and `container-smoke` (image build, explicit Alembic migration and
HTTPS readiness). The test jobs run on `ubuntu-24.04`, and each uses its own
disposable PostgreSQL service with synthetic-only credentials.

## Verification evidence

- **Local unit and integration tests:** **376 passed**, 1 deselected (the E2E
  test), against disposable PostgreSQL 16 after the demo-seed milestone, as
  recorded in [TASKS.md](docs/TASKS.md).
- **Playwright E2E:** 1 test passed against real FastAPI and disposable
  PostgreSQL 16, **before** the demo-seed changes. It has not been rerun since.
- **Docker runtime:** the image, PostgreSQL Compose runtime, migrations
  (head `c8a4f0e2b761`) and HTTPS `/health` were verified locally; the demo
  seed was run via Compose and both demo accounts signed in over HTTPS.
- **Hosted CI:** GitHub Actions
  [run 40900107660](https://github.com/mtas245/ShiftFlow/actions/runs/40900107660)
  passed all four jobs for the original MVP before the demo-seed extension.
  The demo-seed changes still require their own hosted CI verification.
  Hosted test counts are not claimed.

## Limitations

- Broader accessibility (WCAG) and security validation, and a data-protection
  review, are still pending; account-access browser tests use mocked API
  responses.
- [ADR-006](docs/adr/ADR-006-mvp-runtime-and-deployment.md) (runtime and
  deployment) is still **proposed**. There is no production deployment, no
  public access and no public registration.
- Email delivery is unconfigured by default; account emails fail closed.
- The development credentials and self-signed certificate are for local use
  only.

## Project resources

- [AI-SDLC router and shared project rules](AGENTS.md)
- [Project architecture, API contracts, and security boundary](docs/PROJECT.md)
- [Lifecycle state, backlog, and test evidence](docs/TASKS.md)
- [Approved/proposed architecture decisions](docs/adr/)
- [Use-case specifications](docs/specs/)
