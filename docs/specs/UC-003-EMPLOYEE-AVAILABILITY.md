# UC-003 — Employee Availability

## Intent
Allow employees to record when they are available for work so company administrators can plan shifts manually.

## Actors

Primary: Authenticated employee.
Secondary: Company administrator; ShiftFlow.

## Preconditions

- The employee is authenticated through the accepted authentication boundary.
- The employee has an Employee membership in the company for which availability is submitted.
- An administrator viewing availability has an Administrator membership in that company.

## Flow

1. The employee selects a company and submits one availability interval with a start and end date-time.
2. ShiftFlow authorizes the operation using the authenticated principal's membership in that company; the submitted company identifier is not authorization.
3. ShiftFlow validates the interval and associates it with the company and the employee's membership.
4. The employee may update one of their existing intervals. An update changes that interval in place and is revalidated against the employee's other intervals.
5. The employee can view their own intervals within a requested date range. An administrator can view availability for employees in that administrator's company. Employees cannot view other employees' availability.
6. ShiftFlow exposes availability as information for manual planning only; it does not automatically assign shifts.

## Errors

- Unauthenticated request: return HTTP 401 `AUTHENTICATION_REQUIRED`; do not read or change availability.
- Invalid date-time, missing timezone offset, or interval whose end is not after its start: return HTTP 400 `INVALID_AVAILABILITY`.
- Employee without membership in the selected company, or access to another company's availability: return HTTP 404 `RESOURCE_NOT_FOUND` without disclosing whether the company or availability exists.
- Attempt to read another employee's availability without administrator membership in that company: return HTTP 404 `RESOURCE_NOT_FOUND`.
- Overlapping interval for the same employee and company: return HTTP 409 `AVAILABILITY_OVERLAP`.
- Unexpected persistence failure: roll back the create or update and return HTTP 500 `AVAILABILITY_SAVE_FAILED` without internal persistence details.
- Missing or malformed read range, invalid pagination limit, or invalid/context-mismatched cursor: return HTTP 400 `INVALID_REQUEST`.

## Acceptance

- Given an authenticated employee with membership in a company, when they submit a valid interval for that company, then one availability record is associated with that company and employee membership.
- Given an employee submits a company identifier for a company they do not belong to, when they submit or read availability, then the request returns HTTP 404 `RESOURCE_NOT_FOUND` and no data is disclosed or changed.
- Given an employee submits a start and end with explicit RFC 3339 UTC offsets and the end is after the start, when the interval is saved, then it represents the same instants after storage in UTC.
- Given an availability interval crosses midnight, when its end instant is strictly later than its start instant, then it is accepted as one date-specific interval.
- Given an interval has a missing timezone offset, invalid date-time, or end at or before its start, when it is submitted, then it is rejected with HTTP 400 `INVALID_AVAILABILITY` and no record is created or changed.
- Given an employee submits multiple intervals for the same company, when the intervals do not overlap, then each is stored as a separate interval.
- Given an employee submits an interval that overlaps another of their intervals in the same company, when it is saved, then the request returns HTTP 409 `AVAILABILITY_OVERLAP` and no interval is created.
- Given two of an employee's intervals meet exactly at an endpoint, when both are saved, then they are accepted as non-overlapping.
- Given an employee updates one of their existing intervals, when the new interval is valid and does not overlap any other interval excluding the record being updated, then that record is updated in place and unrelated intervals remain unchanged.
- Given an employee updates another employee's interval, when the request is processed, then it is denied without changing the interval.
- Given an administrator with membership in a company, when they view availability for that company, then they can view its employees' intervals but not intervals belonging to another company.
- Given an authorized employee or administrator requests availability with explicit-offset RFC 3339 `from` and `to` instants, when the range is positive and at most 31 days, then results include intervals overlapping the half-open range `[from, to)` and serialize timestamps in UTC.
- Given an availability interval ends exactly at `from` or starts exactly at `to`, when the requested half-open range is read, then that interval is excluded.
- Given a read returns more rows than the requested page limit, when the caller follows `next_cursor` with the same principal, company, endpoint, range, and limit, then the next page continues in deterministic `(start_at, id)` order without gaps or duplicates. A cursor used with a different context is rejected as `INVALID_REQUEST`.
- Given an availability read omits its range or supplies a malformed range, a non-positive range, a range longer than 31 days, a malformed cursor, or a limit outside 1–200, when the request is processed, then it returns HTTP 400 `INVALID_REQUEST`.
- Given an employee requests another employee's availability, when they lack administrator membership in that company, then the request returns HTTP 404 `RESOURCE_NOT_FOUND`.
- Given a request is unauthenticated, when availability is submitted or read, then it returns HTTP 401 `AUTHENTICATION_REQUIRED`.
- Given an unexpected persistence failure occurs during creation or update, when the operation ends, then it returns HTTP 500 `AVAILABILITY_SAVE_FAILED`, exposes no internal persistence details, and leaves no partial change.
- Given availability has been submitted, when shifts are planned, then ShiftFlow does not automatically assign a shift based on that availability.

## Tests

- Unit: interval date-time and offset validation; end-after-start rule; overlap and adjacency rules; company/member association; employee update ownership; UTC instant equivalence.
- Integration: create, read, and update persistence; multiple non-overlapping intervals; overlap rejection; rollback on persistence failure; employee and administrator authorization; cross-company isolation; concurrent overlapping availability writes are serialized and one is rejected; bounded overlapping-range reads, overnight intervals, deterministic keyset pagination, sentinel handling, empty pages, cursor context checks, and UTC response serialization.
- E2E: authenticated employee submits and edits availability; invalid and overlapping intervals are rejected; an employee cannot view a peer's availability; an administrator can view availability only in their company; unauthenticated and cross-company requests are denied.

## Scope

Availability is an employee's willingness to work, not an assignment or confirmation of a shift. Automatic shift assignment, recurring availability, availability approval, deletion, whole-month replacement, and cross-company availability access are outside this MVP use case.

## Design

UC-003 and UC-004 share interval semantics and persistence. Their approved minimum entities, application services, repository boundaries, transaction rules, and concurrency controls are documented here.

### Components

- **Domain:** Add `Availability`, linked to one company and one Employee `CompanyMembership`, with UTC start/end instants. Add `Shift`, linked to a company and with UTC start/end instants, and `ShiftAssignment`, linking one shift to one Employee membership. A shift has zero or one assignment.
- **Application:** Add availability operations for submit, update, and authorized listing, plus shift operations for create, assign, company listing, and employee-assignment listing. Services receive the trusted authenticated principal separately from request data and authorize the requested company and role before accessing tenant data.
- **Persistence boundary:** Add Availability, Shift, and ShiftAssignment repository interfaces and SQLAlchemy adapters; compose them with the existing membership repository in the Unit of Work. Repositories support lookup/listing, overlap checks, and create/update. Create/update and assignment flows are transactional. Failed work rolls back without partial records.
- **Interface:** Use authenticated FastAPI request boundaries with RFC 3339 offset-qualified timestamps, convert validated instants to UTC before application persistence, and return timestamps for display in the viewer's local time. Use the established JSON error envelope and codes in UC-003/UC-004.

Database integrity includes primary keys, foreign keys, `CHECK (end_at > start_at)` on interval records, and a unique assignment `shift_id`. Company/member consistency should use relational constraints where expressible and is always verified in the application service. Overlap is checked by the serialized application transaction rather than a database exclusion constraint.

### Persistence integrity and concurrency

- Availability intervals are associated with the employee identity and company. Reject overlap using half-open intervals `[start, end)` so adjacent intervals are allowed. On update, exclude the interval being updated from overlap detection.
- Each shift has at most one assignment, enforced by a unique constraint on the assignment's shift ID.
- Assigned shifts may not overlap for the same employee, including across companies. Serialize assignment changes using the approved PostgreSQL transaction-scoped advisory lock keyed consistently by the employee's global identity, then check and persist within the same Unit of Work. Keep the lock until commit/rollback; adjacent intervals are allowed.
- Use the same employee-global-identity lock for availability create/update before overlap checks and writes. Updates exclude the row being updated. Sharing the lock serializes availability writes against assignment eligibility checks for that employee.
- Enforce company consistency for availability, shifts, assignments, and membership references through scoped repository operations and relational constraints where expressible. Application authorization and company-scoped queries remain mandatory under ADR-002; a client-supplied company ID is not authority.
- Translate only recognized duplicate/overlap conflicts into the specified safe error codes; unexpected persistence failures roll back and use the corresponding safe 500 error.
- Check availability when an administrator creates an assignment. A later availability change does not automatically cancel or modify an existing assignment; assignments remain visible.

### Approved design decision

Use a PostgreSQL transaction-scoped advisory lock keyed consistently by the employee's global identity for all availability writes and assignment eligibility/overlap checks. Acquire the lock before checks and writes and hold it in the same transaction through commit or rollback. All relevant write paths must follow this convention. The lock is a coordination mechanism, not a substitute for membership authorization, company scoping, database membership uniqueness, or the unique assignment-per-shift constraint.

The lock prevents races only among writers that follow the convention. A bypassing writer can violate overlap/eligibility rules. Hash-key collisions may serialize unrelated employees (reducing concurrency) but must not weaken correctness. See [ADR-007 — Employee Scheduling Advisory Locks](../adr/ADR-007-employee-scheduling-advisory-locks.md).
