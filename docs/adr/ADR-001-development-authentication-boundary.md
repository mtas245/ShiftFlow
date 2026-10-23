# ADR-001 — Development Authentication Boundary

## Status
Accepted — explicitly approved by the user for UC-001 design.

## Context
UC-001 requires an authenticated user, but user registration and production authentication are separate future use cases. A temporary development adapter is needed without coupling the application to a development identity implementation or allowing public impersonation.

## Alternatives
- Put development identity handling in the application or accept the acting user from registration input.
- Use a replaceable authentication adapter at the application boundary.
- Defer authentication integration until production authentication is implemented.

## Decision
The application uses an authenticated-user abstraction supplied at the boundary. The temporary authentication adapter is development-only; startup/configuration fails closed if it is selected for a public or production deployment. A future production adapter supplies the same abstraction.

## Consequences
- Domain and application code remain independent of the adapter and authentication framework.
- The development identity mechanism and production authentication implementation can change without changing `RegisterCompany`.
- Any authenticated user may create a company; no additional company-creation authorization policy applies to UC-001.
- The deployment guard must be enforced in configuration/startup, not by relying on operators to avoid the development adapter.
