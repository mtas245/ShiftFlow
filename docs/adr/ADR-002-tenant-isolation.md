# ADR-002 — Tenant Isolation

## Status
Accepted — explicitly approved by the user for UC-001 design.

## Context
Company is the tenant boundary. Users may belong to multiple companies, and tenant-owned data must not be accessible through another company's context.

## Alternatives
- Require application authorization and company-scoped repository access.
- Require PostgreSQL row-level security in addition to application authorization.
- Rely on endpoint-level checks without consistent repository scoping.

## Decision
Require application-level membership authorization and company scoping for every tenant-owned operation. Do not trust a client-supplied company identifier as authorization. Defer PostgreSQL row-level security as a possible additional measure.

## Consequences
- Tenant-owned repository operations must receive or derive an authorized company scope.
- Authorization must account for a user's memberships in multiple companies.
- Repository and integration coverage must verify cross-company access is denied.
- Whether to add PostgreSQL row-level security can be revisited as a separate decision.
