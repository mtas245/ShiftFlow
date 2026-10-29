# ShiftFlow

ShiftFlow is an FHNW university project for hospitality employee availability
and shift scheduling. It uses a FastAPI-served browser UI, PostgreSQL, and a
Clean Architecture backend. Read [PROJECT.md](docs/PROJECT.md) for architecture,
security decisions, and the complete project context; [TASKS.md](docs/TASKS.md)
records lifecycle status and validation evidence.

## Local Docker development

Requirements: Docker Desktop with Docker Compose. This setup uses a local
PostgreSQL 16 database, development-only credentials, and a self-signed HTTPS
certificate for `localhost`. The certificate is untrusted by browsers and is
for local development only.

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

The GitHub Actions unit/integration and E2E jobs each provide their own
disposable PostgreSQL 16 service and synthetic-only credentials. A separate
container-smoke job builds the image, applies migrations explicitly, and
checks HTTPS readiness. See `.github/workflows/ci.yml`.

## Latest local verification

On Python 3.12.10, the full non-E2E suite passed **350 tests** against
disposable PostgreSQL 16; the separate real-app Playwright E2E passed **1
test** against a fresh disposable PostgreSQL 16 database. The Docker image was
built and started as UID/GID `10001:10001`, migrations reached
`c8a4f0e2b761`, and HTTPS `/health` returned `{"status":"ok"}`. GitHub-hosted
Actions have not yet run; the CI workflow is configured and awaits a hosted
run.

## Project resources

- [AI-SDLC router and shared project rules](AGENTS.md)
- [Project architecture, API contracts, and security boundary](docs/PROJECT.md)
- [Lifecycle state, backlog, and test evidence](docs/TASKS.md)
- [Approved/proposed architecture decisions](docs/adr/)
- [Use-case specifications](docs/specs/)
