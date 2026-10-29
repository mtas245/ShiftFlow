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

The current checkout implements the scoped core browser MVP and includes:

- Domain entities `Company`, `CompanyMembership`, `Availability`, `Shift`, and
  `ShiftAssignment`, plus the `User` account identity.
- Application use cases `RegisterCompany`, `JoinCompany`,
  `ManageAvailability`, `ManageShifts`, account creation via `CreateUser`, and
  login/session management via `AuthenticateUser`, plus email verification and
  password recovery via `AccountLifecycle`.
- SQLAlchemy persistence models and repositories for registration, availability,
  employee joining, shifts, and assignments, with a Unit of Work that provides
  PostgreSQL transaction-scoped employee advisory locks. Account creation uses
  an additive users table and does not rewrite company memberships.
- Argon2id password hashing through an infrastructure adapter. The account
  service returns only a User identity, not a credential hash.
- A cryptographically secure joining-code generator wired into company
  registration through the infrastructure composition boundary.
- An initial Alembic migration for the current five-entity PostgreSQL schema,
  followed by additive account, durable-session, and digest-only account
  action-token and shared rate-limit bucket migrations, with schema-parity and
  repository integration coverage.
- FastAPI endpoints for login/logout, company registration/joining,
  email verification, password recovery, availability, and shift scheduling,
  with Pydantic request/response models and safe error envelopes.
- A FastAPI-served Jinja2 browser shell with responsive navigation, accessible
  light/dark design tokens, first-party CSS/JavaScript, and selected-company
  weekly availability and scheduling workflows. Employees can create and
  update their availability and view only their assigned shifts.
  Administrators can create shifts and assign linked employees through the
  existing authorized APIs. Browser account access, company onboarding,
  membership discovery, and company switching also use the existing APIs.
  Authenticated pages use the validated session middleware and bootstrap a
  session-bound CSRF token without persisting it in browser storage. Joining
  codes are shown only after authorized company creation, remain in page
  memory, and are cleared when leaving the page. The availability page keeps
  local-time values offset-aware for API writes, clears data on company/week
  changes, and displays overnight intervals. The schedule supports bounded
  weekly reads, administrator roster pagination and membership-UUID
  assignments, and explicit handling of repeated daylight-saving local times.
  A Playwright test exercises the onboarding, availability, and scheduling
  journey against the real FastAPI app and an isolated PostgreSQL test
  database.
- A Postmark HTTPS email-delivery adapter and an atomic PostgreSQL rate limiter
  are composed from environment configuration. Rate-limit keys are stored as
  HMAC digests, and forwarded client-IP headers are used only when the immediate
  peer belongs to a configured trusted-proxy network.
- PostgreSQL-backed opaque sessions, Secure/HttpOnly host-only cookies,
  server-side principal construction, 30-minute idle / 12-hour absolute expiry,
  and session-bound CSRF plus configured-Origin checks for state changes.
- Unit and in-memory/PostgreSQL integration tests for the registration,
  availability, and shift-assignment slices.

This milestone adds a non-root Docker application image, an isolated local
PostgreSQL 16 Compose runtime, explicit migration instructions, and
PostgreSQL-backed GitHub Actions jobs. Cloud deployment remains unauthorized.
Account-access browser checks use mocked API responses; onboarding,
availability, and scheduling have real-app E2E coverage using disposable
PostgreSQL. See
the [prioritized backlog](TASKS.md). The test runs described there are recorded
project evidence; rerun the relevant commands as part of the capstone
validation.

## Architecture

The backend follows Clean Architecture; dependencies point inward:

`domain <- application <- interfaces <- infrastructure`

- `domain/` contains business entities and rules, independent of frameworks
  and storage.
- `application/` contains use-case services and coordinates domain behavior
  through abstractions.
- `interfaces/` contains the single FastAPI application boundary and
  request/response schemas for registration, joining, availability, and shifts.
- `infrastructure/` implements persistence and external integrations.
- The approved FastAPI-served browser UI architecture uses Jinja2 templates,
  semantic HTML, CSS, and small first-party JavaScript modules against the
  existing same-origin JSON API. The shell, account-access workflows, company
  onboarding/discovery/switching, employee availability, and scheduling
  workflows are implemented. See
  [ADR-004](adr/ADR-004-fastapi-served-browser-ui.md).
- A company is the tenant boundary. Tenant-owned operations must authorize
  membership and scope access to the authorized company; a client-supplied
  company identifier is not authorization.
- Availability and shift-assignment overlap/eligibility writes share a
  PostgreSQL transaction-scoped advisory lock keyed by the employee's global
  identity. The lock is acquired before checks and writes and held through
  commit/rollback; all such write paths must follow this convention. See
  [ADR-007](adr/ADR-007-employee-scheduling-advisory-locks.md) for its scope
  and limitations.

The MVP domain target is at least five meaningful entities:
`Company`, `CompanyMembership`, `Availability`, `Shift`, and
`ShiftAssignment`. The project target is at least two implemented application
use cases, including company registration and employee joining.

## Security and authentication boundary

Accepted decisions are recorded in
[ADR-001](adr/ADR-001-development-authentication-boundary.md),
[ADR-002](adr/ADR-002-tenant-isolation.md), and
[ADR-003](adr/ADR-003-employee-joining-code.md).
[ADR-005](adr/ADR-005-authentication-and-public-deployment-boundary.md) is
approved. Its login, server-managed session, trusted-principal, logout, CSRF,
email-verification, and password-recovery workflows are implemented. The
approved Postmark email-delivery and PostgreSQL shared rate-limiting adapters
are implemented, but public release controls and regional/privacy review are
not. The proposed
[ADR-006](adr/ADR-006-mvp-runtime-and-deployment.md) records deployment
constraints and still needs review. The development identity adapter must
never be exposed by a public deployment. Startup/configuration
must fail closed if a development-only impersonation mechanism is selected in
public or production mode. The existing HTTP boundary now installs
`AuthenticatedUser` only from a valid server-side session and otherwise
returns `AUTHENTICATION_REQUIRED`. No development identity header or
request-supplied user ID is supported.

The approved design uses first-party email/password accounts, Argon2id password
hashes, revocable PostgreSQL-backed sessions, secure host-only cookies, and
CSRF defenses for same-origin browser requests. Authentication constructs the
trusted principal; application services continue to authorize company roles
and tenant scope separately. The current membership schema has no account
foreign key; the additive account and session migrations preserve its opaque
identity values. Any future account linkage requires a lossless inventory and
mapping before adding a foreign key. Login rejects accounts without a verified
email marker. Verification and password-reset tokens are cryptographically
random, single-use, time-limited, and stored only as digests; tokens remain
inactive until email delivery succeeds. Password reset updates the Argon2id
hash and revokes sessions in one transaction. Account lifecycle email uses
Postmark's HTTPS API, configured through `POSTMARK_SERVER_TOKEN` and
`EMAIL_FROM_ADDRESS`. Shared account-abuse limits use PostgreSQL atomic bucket
upserts keyed by HMAC-SHA-256 digests with `RATE_LIMIT_HMAC_SECRET` (at least
32 bytes). Registration, login, verification resend, and password-reset
requests use separate email and client-IP buckets. The limits are 3/email/day
and 10/IP/hour for registration; 5/email/15 minutes and 20/IP/15 minutes for
login; and 3/email/hour and 10/IP/hour for verification resend and reset
requests. Denied registration/resend/reset requests return the generic 202;
denied login returns generic 429. Limiter outages fail closed with generic
503. `TRUSTED_PROXY_CIDRS` must contain only verified proxy networks;
otherwise forwarded client-IP headers are ignored and the socket peer address
is used. Missing limiter configuration fails closed. Do not expose
self-service registration or public access until secrets, trusted proxy/IP
attribution, provider terms/region, data-protection review, and remaining
release controls are verified. The
approved session lifetime is 30 minutes idle
/ 12 hours absolute. Use synthetic accounts and fictional company/employee
data until FHNW data-protection requirements are confirmed.
Prefer an appropriate European Render/PostgreSQL region after verifying
availability and obligations; European hosting alone does not establish GDPR
compliance. Keep public access restricted until authentication, security, and
data-protection requirements are satisfied. Render's managed HTTPS terminates
at its edge; production cookies must be Secure and proxy headers must not be
trusted from arbitrary clients.

The account foundation implements normalized unique account emails, the
application `CreateUser` service and Argon2id password hashing (64 MiB memory,
3 iterations, 4 lanes). Account passwords must contain 12–1024 characters;
production capacity must be validated before public use. The additive session
migration stores SHA-256 digests of opaque session and CSRF tokens, a user
foreign key, activity/absolute expiry timestamps and revocation state. The
`__Host-shiftflow_session` cookie is always Secure, HttpOnly, SameSite=Lax,
host-only, and Path=/; `PUBLIC_ORIGIN` must be configured for login and unsafe
cookie-authenticated requests. Missing or invalid Origin/CSRF fails closed.
New users remain unverified until they complete the one-time email-verification
flow. Password recovery uses one-time tokens and revokes existing sessions
after a successful password update. HTTP tests create and verify synthetic
accounts through the public-boundary workflows; they do not manually mark
accounts verified in SQL. Anonymous account-lifecycle POST routes require the
configured Origin, and valid cookie-authenticated requests additionally
require the session-bound CSRF token. Do not enable public access until the
provider/limiter configuration, trusted proxy/IP boundary, data-protection
review, and remaining ADR-005 release requirements are verified.

The joining-code contract is eight uppercase alphanumeric characters, generated
with cryptographically secure randomness and protected by a database uniqueness
constraint; collision retry and safe failure behavior are defined in ADR-003
and UC-001. The infrastructure generator uses Python's `secrets` module, and the production
registration composition wires it to the SQLAlchemy Unit of Work.

## Selected stack and local execution

- Python 3.12, FastAPI/Uvicorn, Pydantic, PostgreSQL, SQLAlchemy 2.x,
  Alembic, argon2-cffi, pytest, and HTTPX.
- PostgreSQL is the relational database. Do not commit credentials or secrets.
- Runtime dependencies are pinned in
  `environments/python/requirements-runtime.lock`; CI/test dependencies are
  pinned in `environments/python/requirements-ci.lock`. The optional
  Playwright profile is `environments/python/requirements-e2e.txt`.
- Runtime version: `environments/python/.python-version`.
- `Dockerfile` builds a Python 3.12.10 slim image and runs the app as a
  non-root user. `compose.yml` provides a loopback-only PostgreSQL 16
  development service and HTTPS app endpoint, using local-only credentials
  from `.env.example`. The Compose TLS initializer creates a self-signed
  localhost certificate under ignored `.local/tls`; it is not trusted or
  suitable for production.
- Copy `.env.example` to `.env`, build the app with `docker compose build app`,
  start the database/TLS helper with `docker compose up -d db dev-tls`, then
  run `docker compose run --rm app python -m alembic upgrade head` and
  `docker compose up -d app`. Migrations are not implicitly run by app
  startup. The readiness endpoint performs a database `SELECT 1`; failures
  expose only a generic 503 response.
- Local email delivery is off by default. Account lifecycle routes that
  require email delivery fail closed; this setup does not bypass verification.
  Application startup requires `DATABASE_URL` and a valid HTTPS
  `PUBLIC_ORIGIN`.
- Deployment platform, secrets/configuration, and post-deployment checks must be
  decided in ADR-006 before deployment implementation.

## Commands and validation status

| Activity | Command / status |
|---|---|
| Install locked CI/test dependencies | `python -m pip install -r environments/python/requirements-ci.lock` |
| Install optional Playwright E2E dependencies | `python -m pip install -r environments/python/requirements-e2e.txt`; then `python -m playwright install chromium` |
| Unit tests | `python -m pytest tests\unit -q` |
| In-memory integration tests | `python -m pytest -m "not postgres" tests\integration -q` |
| PostgreSQL integration tests | `python -m pytest -m postgres tests\integration -q`; requires the dedicated disposable local `SHIFTFLOW_TEST_DATABASE_URL` described in the tests |
| Full current backend test set | `python -m pytest -m "not e2e" tests\unit tests\integration -q`; requires the dedicated disposable PostgreSQL test database for PostgreSQL integration tests |
| Browser onboarding and scheduling E2E | `python -m pytest -m e2e tests\integration\test_company_onboarding_e2e.py -q`; requires Playwright/Chromium and the dedicated disposable PostgreSQL test database, which this test empties and migrates |
| Apply database migrations | Set `DATABASE_URL` through the local secret-safe environment configuration, then run `.\.venv\Scripts\python.exe -m alembic upgrade head` from the repository root |
| Run application | `DATABASE_URL` is required; configure `PUBLIC_ORIGIN` as the HTTPS origin; run `.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir src` |
| Start local Docker services | Copy `.env.example` to `.env`; start `db` and `dev-tls`, run the explicit Alembic migration, then start `app` |
| Docker readiness | `https://localhost:8443/health` (local self-signed certificate) |
| GitHub Actions | Python 3.12.10 unit/PostgreSQL integration, separate Playwright E2E, and Docker runtime smoke jobs against PostgreSQL 16 |
| Deployed smoke tests | Not authorized; ADR-006 remains proposed |

Latest local verification for this milestone: **350 passed** in the full
non-E2E suite against disposable PostgreSQL 16; the separate real-app
Playwright test passed (**1 passed**). The container build, explicit migration
to `c8a4f0e2b761`, non-root app identity, Docker health check, and HTTPS
`/health` response were verified. GitHub-hosted CI has not yet run.

### HTTP API contracts

The existing FastAPI app factory exposes:

| Method and route | Contract |
|---|---|
| `POST /auth/register` | Register an unverified account; returns generic 202, including when rate-limited. Missing lifecycle/limiter configuration returns a generic 503. |
| `POST /auth/email-verification/request` | Resend verification; returns the same generic 202 for known/unknown addresses and when rate-limited. |
| `POST /auth/email-verification/confirm` | Consume a valid one-time token and mark the account verified; returns 204. |
| `POST /auth/password-reset/request` | Request password recovery; returns the same generic 202 for known/unknown addresses and when rate-limited. |
| `POST /auth/password-reset/confirm` | Consume a valid reset token, update the Argon2id hash, and revoke existing sessions atomically; returns 204. |
| `POST /auth/login` | Verify a verified account and issue a secure session cookie; returns a session-bound `csrf_token`; rate-limited requests return generic 429. |
| `POST /auth/session/bootstrap` | Require a valid session cookie and exact configured Origin; rotate the CSRF token and return only the new token with `Cache-Control: no-store` and `Vary: Cookie`. |
| `POST /auth/logout` | Require the session cookie, matching Origin and CSRF header; revoke the session and clear the cookie; returns 204. |
| `POST /companies` | Register a company with a company-specific membership display name; returns 201. |
| `POST /companies/join` | Join by exact joining code with a company-specific membership display name; returns 201. |
| `GET /companies/mine` | Return only the authenticated account's explicitly linked memberships, with bounded keyset pagination; returns 200. |
| `GET /companies/{company_id}/employees` | Administrator-only, company-scoped roster of linked Employee memberships; returns public membership UUIDs and company-specific display names. |
| `POST /companies/{company_id}/availability` | Employee submits availability; returns 201. |
| `PUT /companies/{company_id}/availability/{availability_id}` | Employee updates their availability in place; returns 200. |
| `GET /companies/{company_id}/availability/mine` | Employee reads their own bounded, paginated availability; requires `from`/`to`, returns `{items, next_cursor}`. |
| `GET /companies/{company_id}/availability` | Administrator reads bounded, paginated company availability; requires `from`/`to`, returns `{items, next_cursor}`. |
| `POST /companies/{company_id}/shifts` | Administrator creates a shift; returns 201. |
| `POST /companies/{company_id}/shifts/{shift_id}/assignments` | Administrator assigns an eligible employee; returns 201. |
| `GET /companies/{company_id}/shifts` | Administrator reads the bounded, paginated company schedule; requires `from`/`to`, returns `{items, next_cursor}`. |
| `GET /companies/{company_id}/shifts/mine` | Employee reads their bounded, paginated assigned shifts; requires `from`/`to`, returns `{items, next_cursor}`. |

### Approved browser-supporting API contracts

The interface contracts were approved for Phase 3. Session bootstrap and
account-linked company discovery, the administrator roster, membership-UUID
assignment, and bounded schedule/availability reads are implemented and
verified as recorded in `TASKS.md`. Browser account access, company
onboarding/discovery, employee availability, and schedule creation/assignment
are implemented.

- `POST /auth/session/bootstrap` requires a valid session cookie and exact
  configured Origin, without requiring the previous CSRF token. It rotates the
  stored CSRF digest atomically, retains only the immediately previous digest
  for five minutes, and returns only `{"csrf_token":"..."}` with
  `Cache-Control: no-store` and `Vary: Cookie`. Invalid or expired sessions
  return 401 and clear the cookie. The endpoint accepts no client identity or
  session token and is the only authenticated unsafe endpoint exempt from the
  CSRF header; Origin validation remains required. Other unsafe
  cookie-authenticated requests accept the current CSRF token or the immediately
  previous token during that five-minute grace period.
- `GET /companies/mine` filters strictly by the validated server-side
  principal's `account_user_id`, returns membership public UUID, company
  ID/name, and role, and uses bounded keyset pagination. It does not expose
  account IDs, email addresses, or legacy opaque membership IDs; legacy rows
  without explicit account links are not returned.
- Administrator-only `GET /companies/{company_id}/employees` returns
  only linked Employee memberships in that company with
  company-specific `display_name` and opaque membership UUID. Unlinked legacy
  memberships are excluded without modification. Never return account IDs or
  email addresses. Assignment requests and assignment/schedule results use
  that membership UUID rather than the global account ID. Assignment resolves
  the UUID within the authorized company, then retains existing eligibility,
  global employee lock, and cross-company overlap behavior.
- `POST /companies` and `POST /companies/join` require a
  company-specific `display_name`, trimmed to 1–80 characters and rejecting
  empty values or Unicode control characters. Store it on
  `CompanyMembership`, not the global User account; specifications are
  updated in UC-001 and UC-002.
- Bound own/company availability and shift-list reads by required RFC 3339
  `from`/`to` values (explicit offsets), with positive windows no longer than
  31 days and half-open interval overlap. Results are ordered by
  `(start_at, id)` and keyset-paginated with default `limit=100` and maximum
  200. Responses contain at most one page in `{items, next_cursor}`; the
  persistence query fetches only `limit + 1` rows to determine continuation.
  Cursors are opaque, validated, and bound to the authenticated principal,
  company, endpoint, range, and limit. Missing/malformed ranges, cursors, and
  limits return HTTP 400 `INVALID_REQUEST`. Responses are UTC and
  `Cache-Control: no-store`; no unbounded HTTP list alternative remains.
  Membership and employee-list endpoints use their own bounded keyset
  pagination contract.
- Additive migration `c8a4f0e2b761` adds nullable
  `company_memberships.account_user_id` referencing `users.id`, a unique
  per-membership public UUID, and company-specific `display_name`; it preserves
  all existing opaque `user_id` values and leaves legacy account links null.
  New registration/join write paths derive the account link only from the
  authenticated principal. Do not infer ownership from string equality or
  email. Before linking retained identities, complete the approved
  representative-verified and independently reviewed inventory/mapping
  preflight, with uniqueness/collision checks and a transaction/audit/rollback
  plan. Unmatched rows remain preserved and unlinked.

The authenticated principal is resolved from the opaque session cookie and
server-side PostgreSQL session/account records; protected requests without a
valid session fail closed with 401. Login failures are generic, and login does
not accept client-supplied identity. Every unsafe cookie-authenticated request,
including logout, requires `Origin` equal to configured `PUBLIC_ORIGIN` and
`X-CSRF-Token` matching the session-bound token returned by login/bootstrap,
except bootstrap itself, which requires Origin and rotates the CSRF token.
During bootstrap, only the immediately previous token remains acceptable for
five minutes. Request models reject extra identity fields. Anonymous
account-lifecycle POSTs also
require the configured Origin; when a valid session cookie is present they
require the matching CSRF token. Registration, verification resend, and
password-reset request responses do not disclose address existence. Scheduling
timestamps require RFC 3339
offsets and responses use UTC. Authorization remains in application services:
insufficient company role returns 403, while inaccessible cross-company
resources return tenant-safe 404. Overlap and eligibility conflicts return
409; validation errors return safe 400 envelopes. Rate-limiter failures return generic 503 without performing account lifecycle
or login work. Persistence failures return
generic safe error envelopes without exposing database details. Login and
authenticated responses use `Cache-Control: no-store`.

The PostgreSQL tests validate their target and do not fall back to
`DATABASE_URL`. Use only a dedicated disposable test database; never point
destructive integration tests at a development or production database.

### Database migrations

Alembic reads the PostgreSQL connection string from `DATABASE_URL`; no
connection string or credentials are stored in `alembic.ini`. Apply pending
schema changes from the repository root with:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
```

The initial migration creates the company, membership, availability, shift,
and assignment tables. Additive migrations create `users` with normalized
unique email/password-hash storage, then add nullable `email_verified_at`,
the PostgreSQL-backed `user_sessions` table, account action tokens,
`rate_limit_buckets`, additive session CSRF rotation-grace fields, and
account-linked membership fields in `c8a4f0e2b761`. Session
rows retain only the current and immediately previous CSRF token digests; the
previous digest has a five-minute deadline. Rate-limit bucket identity is a
scope and HMAC digest, with an indexed UTC window start and positive request
count. Session rows cascade only from their account; no migration infers
mappings for legacy `user_id` values. Migration `c8a4f0e2b761` adds a nullable
`company_memberships.account_user_id` foreign key, a unique per-membership
public UUID, and company-specific `display_name`, while preserving existing
`user_id` values. It does not backfill account links. Retained identities may
be linked only through the approved representative-verified and
independently-reviewed preflight; unmatched records remain preserved and
unlinked. Downgrading the account/session migrations deletes account credentials,
verification state, sessions, tokens, and rate-limit history. The initial
downgrade can be run with
`.\.venv\Scripts\python.exe -m alembic downgrade base`, but this drops those
tables and all data in them. Back up the database and verify the backup before
any downgrade; prefer a forward migration to repair a deployed schema. The
migration integration test exercises upgrade, repeated upgrade, downgrade, and
re-upgrade only on its dedicated disposable PostgreSQL database. Rate-limit
windows are fixed from the first request in a bucket, so short bursts can occur
at a window boundary; this control is not a substitute for edge abuse
prevention. Expired bucket rows are purged opportunistically at most hourly
while the application is receiving requests. There is not yet a scheduled
cleanup worker for idle session or account-action-token rows; configure and
verify that retention before public release.

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
- [UC-002 — Employee Joining](specs/UC-002-EMPLOYEE-JOINING.md)
- [UC-003 — Employee Availability](specs/UC-003-EMPLOYEE-AVAILABILITY.md)
- [UC-004 — Shift Creation and Assignment](specs/UC-004-SHIFT-CREATION-AND-ASSIGNMENT.md)
- [ADR-001 — Development Authentication Boundary](adr/ADR-001-development-authentication-boundary.md)
- [ADR-002 — Tenant Isolation](adr/ADR-002-tenant-isolation.md)
- [ADR-003 — Employee Joining Code](adr/ADR-003-employee-joining-code.md)
- [ADR-004 — FastAPI-Served Browser UI](adr/ADR-004-fastapi-served-browser-ui.md)
- [ADR-005 — Authentication and Public Deployment Boundary](adr/ADR-005-authentication-and-public-deployment-boundary.md)
- [ADR-006 — MVP Runtime and Deployment](adr/ADR-006-mvp-runtime-and-deployment.md)
- [ADR-007 — Employee Scheduling Advisory Locks](adr/ADR-007-employee-scheduling-advisory-locks.md)

UC-001 through UC-004 specifications and designs are approved, including the
UC-001/UC-002 company-specific display-name amendments. ADR-005's
account lifecycle flows, Postmark delivery adapter, and PostgreSQL shared
rate-limiter are implemented and covered by tests. Production secrets,
trusted-proxy/IP attribution, Postmark terms/region, data-protection review,
scheduled retention, and other public release controls remain unverified.
ADR-004 and its browser-supporting API contracts are accepted. The initial
Jinja2 browser shell, account-access workflows, company onboarding,
discovery, switching, employee availability editing, and shift scheduling
workflows are implemented. Session
bootstrap, account-linked company discovery, the administrator roster, and
membership-UUID-based assignment are implemented and covered by the
latest full non-E2E regression (**350 passed** against isolated PostgreSQL 16;
company onboarding, availability, and scheduling Playwright E2E results are
recorded in `TASKS.md`). Legacy membership
identity linkage remains gated on the approved preflight mapping process.
Bounded schedule and availability reads are implemented. The employee
availability milestone passed **343 unit/integration tests** and one
Playwright E2E; the scheduling browser milestone passed **347
unit/integration tests** and one Playwright E2E against disposable PostgreSQL
16. The scheduling journey verified administrator creation and assignment,
fall-back time disambiguation, conflict rejection, employee-only visibility,
company switching, and reload persistence. Broader accessibility automation
remains pending; exact evidence and limitations are recorded in `TASKS.md`.
The Docker development runtime and real PostgreSQL-backed CI are the current
Phase 3 milestone; see the newest evidence in `TASKS.md`. Proposed ADR-006
remains pending review and does not authorize public deployment.
Update this document as decisions are accepted and implementation status
changes.
