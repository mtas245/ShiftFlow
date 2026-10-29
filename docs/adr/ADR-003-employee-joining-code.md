# ADR-003 — Employee Joining Code

## Status
Accepted — explicitly approved by the user for UC-001 design.

## Context
Company registration generates a code employees can use to join a company. The code must be unique and must not be predictable from ordinary sequential identifiers.

## Alternatives
- Generate predictable or sequential codes.
- Generate random codes and rely only on application-level duplicate checks.
- Generate cryptographically secure codes and enforce uniqueness in the database.

## Decision
Generate exactly eight uppercase alphanumeric characters (`A–Z`, `0–9`) using cryptographically secure randomness. Enforce uniqueness across all companies with a database uniqueness constraint. On collision, generate another code, with a maximum of five generation attempts; after five collisions, abort registration and return HTTP 503 with error code `JOINING_CODE_UNAVAILABLE`, without exposing sensitive internals. Codes do not expire automatically in the MVP; rotation may be added later.

## Consequences
- Code generation and persistence must handle uniqueness conflicts without leaving partial company or membership records.
- A failed fifth attempt must not leave a company or administrator membership persisted; the interface maps this exhaustion to HTTP 503 with the approved safe error code.
- Expiry and rotation are outside the MVP behavior.
