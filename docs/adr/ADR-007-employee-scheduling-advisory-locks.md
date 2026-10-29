# ADR-007 — Employee Scheduling Advisory Locks

## Status
Accepted — explicitly approved by the user for UC-003 and UC-004 design.

## Context
Availability intervals for an employee must not overlap, and assigned shifts must not overlap for the same employee, even across companies. Availability is checked when an assignment is created. Concurrent writes must not both pass an application-level check before either commits.

## Alternatives
- Serialize checks and writes with a PostgreSQL transaction-scoped advisory lock keyed consistently by employee identity.
- Enforce overlap with PostgreSQL exclusion constraints, which require suitable interval/employee data and database-specific schema support.
- Use application-only checks without database serialization, which do not prevent concurrent transactions from violating the rules.

## Decision
All availability create/update operations and shift-assignment operations acquire the same PostgreSQL transaction-scoped advisory lock keyed consistently by the employee's global identity before eligibility/overlap checks and writes. The identity-to-lock-key mapping must be deterministic so the same employee always acquires the same key. Hold the lock through commit or rollback in the same Unit of Work. Assignment overlap checks span all companies for that employee. Use half-open intervals so adjacent intervals are permitted.

Retain database uniqueness constraints for `(company_id, user_id)` membership identity and at most one assignment per shift. Continue application-level membership authorization and company-scoped repository access under ADR-002. Availability is checked at assignment time; later changes to availability do not cancel or modify assignments, and existing assignments remain visible.

## Consequences
- Every write path that changes availability or creates/replaces an assignment must use the same lock-key convention and acquire the lock before checks and writes. Bypassing this convention can violate overlap or eligibility rules.
- A transaction-scoped advisory lock is released automatically on commit or rollback. Lock-key hash collisions may serialize unrelated employees and reduce concurrency, but must not permit invalid overlap.
- The lock does not replace authentication, authorization, tenant scoping, foreign keys, or uniqueness constraints.
- Integration tests must cover overlapping concurrent assignments across companies and concurrent overlapping availability writes.
