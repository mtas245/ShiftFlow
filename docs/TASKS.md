# TASKS.md

PHASE: 3
STATUS: in-progress

(0=Bootstrap | 1=Specify | 2=Design | 3=Develop | 4=Validate | 5=Deploy)

## Capstone objective

Deliver a secure, demonstrable ShiftFlow MVP for the FHNW capstone presentation
on 29 October 2026. Keep work on the current feature branch until the MVP has
passed review and validation. Do not merge incomplete functionality into `main`.
Preserve the existing UC-001 source, tests, ADRs, and documentation while
establishing a clean, reviewed Git baseline before parallel implementation.

### MVP scope

1. Company registration; its creator becomes company administrator.
2. Employee joins a company using its secure joining code.
3. Employee submits basic availability.
4. Administrator creates a shift and assigns an employee.
5. Authorized users can view the relevant company membership and scheduling
   information, with cross-company access denied.

Use at least five meaningful domain entities: `Company`,
`CompanyMembership`, `Availability`, `Shift`, and `ShiftAssignment`. Implement
at least two application use cases, including registration and employee
joining. A minimal browser UI served by FastAPI is preferred over a separate
frontend, subject to the ADR below. Development-only identity/impersonation
must fail closed and must never be available in a public deployment.

## Prioritized backlog

Owners are intentionally **unassigned**. Assign owners only after confirming
availability and agreeing shared interfaces. Dependencies refer to backlog IDs.

| Priority / ID | Status | Owner | Work and dependencies | Acceptance criteria |
|---|---|---|---|---|
| **P0 — B0: Establish reviewed Git baseline** | Not started | Unassigned | Inspect and preserve all current UC-001 source, tests, ADRs, and docs; record them on `feature/uc-001-company-registration` through the agreed reviewed workflow. No merge to `main`. | Existing local work is included without loss; baseline tests and current limitations are recorded; a teammate reviews the baseline PR/change; branch has a clean working tree afterwards; `main` is unchanged. Complete before parallel code work. |
| **P0 — S1: Specify MVP use cases** | Not started | Unassigned | Depends on B0. Add specs for employee joining, availability submission, and shift creation/assignment. Clarify actors, authorization, validation, failure cases, and testable acceptance. | Each new use case has Given/When/Then acceptance and unit/integration/E2E expectations; membership and tenant authorization are explicit; scheduling boundaries and time-zone assumptions are stated; specs are reviewed before implementation. |
| **P0 — D1: Resolve implementation decisions** | Not started | Unassigned | Depends on S1. Prepare and obtain human approval for the ADRs listed under “Design decisions required before implementation”. | Approved ADRs describe the FastAPI-served browser UI, authentication/deployment boundary, and MVP deployment/runtime approach; scheduling semantics not already fixed by specs are decided. No implementation starts against unresolved shared contracts. |
| **P1 — I1: Make the app locally runnable and deployment-ready early** | Not started | Unassigned | Depends on B0 and D1. Add Docker-based local execution, PostgreSQL configuration/migrations, documented environment variables, and safe startup configuration. | A clean checkout can start the application and PostgreSQL with the documented Docker command; migrations create the schema from scratch; health check works; secrets are not committed; public/production configuration rejects development-only impersonation and fails closed. |
| **P1 — C1: Enforce application checks in CI** | Not started | Unassigned | Depends on B0; coordinate with I1 for PostgreSQL integration execution. | GitHub Actions on PRs runs formatting/static checks selected by the team, unit tests, integration tests against a dedicated disposable database, and a build/startup check; a failing check blocks successful validation; no production credentials are required. |
| **P1 — U1: Complete registration and employee joining** | Not started | Unassigned | Depends on S1, D1, I1, and C1. Build small vertical slices using existing UC-001 foundations and ADR-003. | Registration persists company and administrator membership atomically. Joining codes use cryptographically secure randomness, are unique in PostgreSQL, and follow approved retry/error behavior. Joining validates the code and prevents duplicate membership. Unauthorized and cross-company reads are denied. Unit and integration tests demonstrate behavior and rollback. |
| **P1 — U2: Implement availability and shift assignment** | Not started | Unassigned | Depends on S1, D1, I1, and C1; coordinate data contracts with U1. | Employee availability is stored for the authorized company and validated against agreed time rules. An administrator can create a shift and assign an eligible member. Invalid or unauthorized operations are rejected. Unit and integration tests cover rules, persistence, and tenant isolation. |
| **P1 — U3: Build the demonstrable browser path** | Not started | Unassigned | Depends on D1, U1, and U2. Keep the UI served by FastAPI; do not create a separate frontend application unless a new decision is approved. | A browser user can complete the scoped MVP flow end-to-end: create company, join as employee, view membership, submit availability, and view an assigned shift. Forms show useful validation errors; UI does not permit selecting an acting identity in public deployments. |
| **P2 — V1: Verify TDD, E2E, security boundary, and peer review** | Not started | Unassigned | Depends on U1, U2, U3, and C1. | Each vertical slice retains RED → GREEN → REFACTOR evidence in its PR or linked test record. Automated E2E covers the core happy path and at least one authorization/invalid-input case. A different teammate reviews each implementation PR; fixes and required CI checks pass. |
| **P2 — D2: Deploy and rehearse presentation** | Not started | Unassigned | Depends on I1, C1, and V1. | A production-like cloud deployment is reachable over HTTPS, uses production-safe authentication/configuration, and passes post-deployment smoke tests. The team rehearses the full demo and confirms a local Docker fallback. Deployment evidence and known limitations are documented before presentation. |
| **P2 — R1: Complete presentation documentation and evidence** | Not started | Unassigned | Depends on D1 and progresses alongside implementation; final status depends on D2 and V1. | README gives working local Docker/test/demo instructions; architecture diagram matches the implementation; approved ADRs and specs are linked; presentation evidence identifies actual AI agent/model/tools and MCP use, test/CI/PR/review evidence, deployment URL, and any limitations. |

## Specifications and ADRs required before implementation

Existing specification: [UC-001 — Company Registration](specs/UC-001-COMPANY-REGISTRATION.md).

Prepare and review these specifications before coding their slices:

- **UC-002 — Employee Joining:** join by company code, role/membership behavior,
  invalid/unknown code, duplicate membership, and company isolation.
- **UC-003 — Employee Availability:** submission and viewing of basic
  availability, validation, edit/duplicate behavior, and time-zone rules.
- **UC-004 — Shift Creation and Assignment:** administrator creates a shift,
  assigns an eligible employee, and views the result; define overlap and
  availability-conflict behavior for the MVP.

Existing accepted decisions: [ADR-001 — Development Authentication Boundary](adr/ADR-001-development-authentication-boundary.md),
[ADR-002 — Tenant Isolation](adr/ADR-002-tenant-isolation.md), and
[ADR-003 — Employee Joining Code](adr/ADR-003-employee-joining-code.md).

Before implementation, prepare and obtain human approval for:

- **ADR-004 — FastAPI-Served Browser UI:** select the minimal rendering/form
  approach and define its interface with application use cases.
- **ADR-005 — Authentication and Public Deployment Boundary:** specify the
  development identity mechanism, configuration defaults, production guard,
  and how a public demo authenticates users without impersonation.
- **ADR-006 — MVP Runtime and Deployment:** select Docker service boundaries,
  migration/startup behavior, cloud target, configuration/secrets, and
  health/smoke checks.
- **Scheduling design decision (ADR if needed):** record any unresolved
  availability/shift temporal, overlap, or assignment rule that materially
  affects schema or behavior and is not settled in UC-003/UC-004.

Do not silently expand the MVP or implement against unresolved decisions.

## Definition of Done

A backlog item is done only when:

- Its acceptance criteria pass and are linked to the relevant specification or
  ADR.
- Changes follow RED → GREEN → REFACTOR; test and CI evidence is recorded.
- A teammate has reviewed the change and required checks pass.
- Tenant authorization and production-safe authentication behavior are tested
  wherever the item touches access or deployment.
- Related docs are current; no unrelated local work is lost.

The project remains **PHASE 3 / in-progress** until the development work and
required validation are complete. Do not mark the phase done based only on
documentation, a running local demo, or a successful deployment.

## Presentation-readiness checklist — 29 October 2026

- [ ] Fresh checkout starts locally using documented Docker instructions.
- [ ] The complete registration → joining → membership → availability → shift
  assignment path works with synthetic demo data.
- [ ] At least five meaningful domain entities and two implemented application
  use cases are visible in code and covered by automated tests.
- [ ] Unit, integration, and E2E tests pass; CI runs application checks on PRs.
- [ ] Cross-company access is denied; production/public configuration cannot
  enable development-only impersonation.
- [ ] A deployed cloud instance and post-deployment smoke-test evidence are
  available; local Docker is rehearsed as fallback.
- [ ] Each implementation change has a human teammate review; PRs and CI
  evidence can be shown.
- [ ] RED → GREEN → REFACTOR evidence, agent/model/tool choice, and MCP context
  integration/use are documented accurately.
- [ ] README, current architecture diagram, approved ADRs, and use-case specs
  match the implemented product.
- [ ] Presentation walkthrough is rehearsed; known gaps are disclosed.

## Current implementation evidence and limitations

The current checkout contains `Company`, `CompanyMembership`, one application
use case (`RegisterCompany`), SQLAlchemy persistence adapters, unit tests, and
in-memory/PostgreSQL integration tests. `docs/PROJECT.md` records previous RED
and GREEN test runs; those results are historical project evidence and must not
be treated as a fresh run for this capstone plan.

### Previously recorded UC-001 TDD evidence

The earlier milestone record reported:

- Initial unit and in-memory integration tests were RED because the
  application/domain modules did not exist. GREEN: `python -m pytest
  tests\unit -q` (27 passed) and `python -m pytest tests\integration -q`
  (5 passed using in-memory collaborators).
- PostgreSQL integration tests were RED because SQLAlchemy persistence
  adapters were missing. GREEN:
  `python -m pytest -m postgres
  tests\integration\test_postgres_company_registration.py -q` (10 passed
  against a disposable PostgreSQL 16 container); full suite
  `python -m pytest tests\unit tests\integration -q` (42 passed).
- PostgreSQL tests require `SHIFTFLOW_TEST_DATABASE_URL`, validate loopback
  host/database/user, reject other targets, and do not read `DATABASE_URL`.
  The recorded container used a random password and was removed after
  verification.

These are retained as prior project records, not independently verified in
this documentation update. Continue recording RED, GREEN, REFACTOR, and test
results for each new vertical slice.

The HTTP/interface layer, browser UI, secure production joining-code generator,
Alembic migration setup, availability/shift model, E2E tests, Docker execution,
app-aware CI, and active cloud deployment are not yet implemented. The current
local UC-001 files must be preserved and included in the reviewed baseline
before parallel code changes.

## Working agreement

- Keep one active vertical slice small enough to review and demonstrate.
- Agree shared contracts before parallel work; avoid concurrent edits to the
  same schema, migration, or API boundary.
- Keep this file current as work starts, becomes blocked, or meets its verified
  acceptance criteria. Record blockers and link evidence.
- Human-approved decisions belong in ADRs; current architecture/status belongs
  in [PROJECT.md](PROJECT.md).
