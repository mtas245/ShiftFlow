# UC-004 — Shift Creation and Assignment

## Intent
Allow a company administrator to create a shift and manually assign an eligible employee.

## Actors

Primary: Company administrator.
Secondary: Employee; ShiftFlow.

## Preconditions

- The administrator is authenticated through the accepted authentication boundary and has an Administrator membership in the target company.
- An employee assigned to a shift is an Employee member of the same company and has availability covering the shift interval.

## Flow

1. The administrator selects a company and submits a shift start and end timestamp.
2. ShiftFlow authorizes shift creation using the authenticated principal's Administrator membership in that company; a client-supplied company identifier is not authorization.
3. ShiftFlow validates the shift interval and creates a company-scoped shift.
4. The administrator selects a linked Employee membership from that company's authorized roster and submits its opaque membership UUID.
5. ShiftFlow verifies the employee membership and that one of the employee's availability intervals covers the entire shift interval.
6. ShiftFlow rejects an assignment that would overlap another shift assignment for that employee.
7. The administrator can view shifts and assignments in their company. An employee can view shifts assigned to them in their company, but not other employees' assignments.
8. ShiftFlow records the shift and assignment for manual scheduling; it does not select employees or create assignments automatically.

## Errors

- Unauthenticated request: return HTTP 401 `AUTHENTICATION_REQUIRED`; do not read or change shifts.
- Authenticated non-administrator attempts to create a shift or assign an employee: return HTTP 403 `FORBIDDEN` when the principal is a member of that company; use HTTP 404 `RESOURCE_NOT_FOUND` for cross-company resources without disclosing whether they exist.
- Invalid timestamp, missing explicit UTC offset, or shift end not strictly after start: return HTTP 400 `INVALID_SHIFT`.
- Employee is not an Employee member of the shift's company: return HTTP 404 `RESOURCE_NOT_FOUND`.
- Assignment references a malformed, unknown, unlinked, or other-company membership: return HTTP 404 `RESOURCE_NOT_FOUND` without disclosing whether the membership exists.
- Employee has no availability interval that covers the full shift: return HTTP 409 `EMPLOYEE_UNAVAILABLE`.
- Assignment overlaps another assignment for the employee: return HTTP 409 `SHIFT_ASSIGNMENT_CONFLICT`.
- Invalid or cross-company resource reference: return HTTP 404 `RESOURCE_NOT_FOUND` without disclosing whether the resource exists.
- Unexpected persistence failure while creating a shift or assignment: roll back the failed operation and return HTTP 500 `SHIFT_SAVE_FAILED` without internal persistence details.
- Missing or malformed schedule range, invalid pagination limit, or invalid/context-mismatched cursor: return HTTP 400 `INVALID_REQUEST`.

Error responses use the approved UC-002 envelope: `{"error": {"code": "<ERROR_CODE>"}}`.

## Acceptance

- Given an authenticated administrator with membership in a company, when they create a valid shift for that company, then one shift is created and scoped to that company.
- Given an authenticated employee or other non-administrator member, when they attempt to create a shift or assign an employee in that company, then the request returns HTTP 403 `FORBIDDEN` and no unauthorized change occurs.
- Given an administrator without membership in a requested company, when they attempt to create, assign, or view its shift, then the request returns HTTP 404 `RESOURCE_NOT_FOUND` without revealing whether the company or shift exists.
- Given shift timestamps use RFC 3339 with explicit UTC offsets and the end instant is strictly later than the start, when the shift is saved, then the instants are persisted and compared in UTC; a shift may cross midnight.
- Given a shift has invalid timestamps, lacks an explicit offset, or ends at or before its start, when it is created, then the request returns HTTP 400 `INVALID_SHIFT` and no shift is persisted.
- Given an administrator creates multiple shift slots with identical time intervals, when each slot is valid, then each is created as an independent company-scoped shift.
- Given an administrator assigns an Employee member of the same company whose availability interval covers the entire shift, when the assignment is created, then one assignment is associated with that shift and employee membership.
- Given an administrator requests the employee roster for a company they administer, when the request is processed, then only linked Employee memberships from that company are returned with company-specific display names and opaque membership UUIDs; account IDs, email addresses, and legacy opaque membership IDs are not returned.
- Given a company has retained legacy memberships without account links, when its roster is listed or an assignment is attempted using such a membership, then the legacy row remains unchanged, is not listed, and cannot be selected through the account-backed membership UUID API.
- Given an assignment request contains a membership UUID, when the request is processed, then ShiftFlow resolves it only within the authorized shift company and returns assignment/schedule membership UUIDs and display names without exposing internal employee IDs.
- Given an administrator attempts to assign a second employee to a shift that already has an assignment, when the request is processed, then no second assignment is created.
- Given the selected employee is not an Employee member of the shift's company, when the administrator assigns them, then the request returns HTTP 404 `RESOURCE_NOT_FOUND` and no assignment is created.
- Given no single availability interval covers the full shift, when the administrator assigns the employee, then the request returns HTTP 409 `EMPLOYEE_UNAVAILABLE` and no assignment is created.
- Given an employee already has an assignment whose time interval overlaps the proposed shift, when an administrator attempts the new assignment, then the request returns HTTP 409 `SHIFT_ASSIGNMENT_CONFLICT` and no overlapping assignment is created.
- Given concurrent assignment attempts would create overlapping shifts for the same employee, when both are processed, then at most one conflicting assignment is persisted and the other returns HTTP 409 `SHIFT_ASSIGNMENT_CONFLICT`.
- Given two assignments for an employee meet exactly at an endpoint, when both are created, then they are accepted as non-overlapping.
- Given a company administrator views shifts, when the request is processed, then they see shifts and assignments only for their company.
- Given an employee views shifts, when the request is processed, then they see only shifts assigned to them in a company where they are a member.
- Given an authorized administrator or employee requests shifts with explicit-offset RFC 3339 `from` and `to` instants, when the range is positive and at most 31 days, then the schedule includes shifts overlapping the half-open range `[from, to)` in UTC.
- Given a shift ends exactly at `from` or starts exactly at `to`, when the schedule range is read, then that shift is excluded; overnight shifts that overlap the range are included.
- Given a schedule contains more shifts than the requested page limit, when the caller follows `next_cursor` with the same principal, company, endpoint, range, and limit, then pages continue in deterministic `(start_at, id)` order without gaps or duplicates. A cursor used in another context is rejected as `INVALID_REQUEST`.
- Given an authorized schedule read omits its range or supplies a malformed/non-positive range, a range longer than 31 days, a malformed cursor, or a limit outside 1–200, when the request is processed, then it returns HTTP 400 `INVALID_REQUEST`.
- Given an assignment exists and an administrator later updates the employee's availability so it no longer covers that shift, when the schedule is viewed, then the existing assignment remains visible and is not automatically changed.
- Given an employee or administrator requests another company's shift, when they lack the required company membership, then the request returns HTTP 404 `RESOURCE_NOT_FOUND` without disclosing whether that shift exists.
- Given an unauthenticated request attempts a shift operation, when the request is processed, then it returns HTTP 401 `AUTHENTICATION_REQUIRED` and no data is read or changed.
- Given an unexpected persistence failure occurs while creating a shift or assignment, when the operation ends, then the failed operation is rolled back and returns HTTP 500 `SHIFT_SAVE_FAILED` without internal persistence details.
- Given availability and shifts exist, when the schedule is viewed, then ShiftFlow does not automatically assign employees or treat availability as a confirmed assignment.

## Tests

- Unit: shift interval/time-zone validation; Employee membership and company matching; full-interval availability coverage; assignment overlap and adjacency; administrator and employee viewing scope.
- Integration: company-scoped shift creation; assignment persistence and rollback; role/membership authorization; availability eligibility; overlapping assignment rejection, including concurrent attempts across companies for the same employee; company-scoped reads.
- HTTP/PostgreSQL: administrator-only roster authorization and tenant isolation; exclusion and preservation of unlinked legacy rows; malformed, unknown, and cross-company membership UUID rejection; safe roster, assignment, and schedule response fields; rollback and concurrent cross-company overlap protection; bounded overlapping-range schedule reads, overnight shifts, deterministic keyset pagination, limit+1 handling, cursor context checks, empty pages, and UTC serialization. See [TASKS.md](../TASKS.md) for current RED/GREEN/REFACTOR evidence.
- E2E: administrator creates a shift and assigns an eligible employee; invalid shifts, unavailable employees, overlapping assignments, and unauthorized actions are rejected; administrators see only their company's schedule and employees see only their assigned shifts.

## Scope

A Shift is one assignable employee work slot. Administrators may create multiple independent slots with identical times. A shift may start unassigned and have at most one assignment. Automatic assignment, recurring shifts, staffing-requirement engines, minimum team sizes, and shift lifecycle/approval workflows are outside this MVP use case.

## Design

### Components

- **Domain:** Add `Shift` and `ShiftAssignment`; reuse `CompanyMembership` and the `Availability` interval from UC-003. A shift has a company and UTC start/end; an assignment links one shift to one Employee membership.
- **Application:** Provide administrator-authorized shift creation, employee assignment, company schedule listing, and listing of shifts assigned to the authenticated employee. Reuse the Unit of Work and membership authorization boundary.
- **Infrastructure:** Persist shifts and assignments with SQLAlchemy repositories and a database uniqueness constraint for at most one assignment per shift. Shift creation and assignment each run atomically in a Unit of Work. Serialize assignment changes by employee identity with the shared transaction-scoped PostgreSQL advisory lock before checking eligibility/overlap and inserting, within the same Unit of Work.
- **Interface:** Authenticated FastAPI operations map the approved authorization, validation, availability, assignment-conflict, and persistence outcomes to the established error envelope.

The shared entity, service, repository, transaction, and concurrency design for UC-003 and UC-004 is recorded in UC-003's Design section and approved. Availability is evaluated when the assignment is created; later availability changes do not automatically modify assignments. Shift creation and assignment are separate operations: a failed assignment leaves the previously created shift unassigned.
