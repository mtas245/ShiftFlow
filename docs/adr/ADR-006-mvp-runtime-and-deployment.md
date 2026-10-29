# ADR-006 — MVP Runtime and Deployment

## Status

Proposed — deployment constraints recorded; runtime and release details remain
subject to human review. This proposal does not authorize an unrestricted
public deployment.

## Context

ShiftFlow targets a FastAPI application with PostgreSQL and Alembic. The user
has selected Render as the target platform and prefers a European region for
the Render web service and PostgreSQL where available and appropriate.
Development and the university demonstration must use synthetic test accounts
and fictional company/employee data only.

FHNW requirements for data classification, hosting regions, and personal-data
processing have not been formally confirmed. European hosting alone does not
establish GDPR compliance. ADR-005 requires real-personal-data use and
unrestricted public access to remain gated on authentication, security, and
data-protection review.

## Alternatives

- Run the web service and database on Render, using managed PostgreSQL shared
  by the application instances.
- Use a different cloud provider or self-managed PostgreSQL, which would
  require a separate deployment and operational decision.
- Store sessions or other durable application data in the web process or local
  filesystem; rejected because Render web services can restart, deploy, and
  scale to multiple instances.

## Proposed decision

- Use a Render web service for FastAPI and Render managed PostgreSQL for
  persistent application data and server-managed sessions, subject to final
  environment, region, plan, and release review.
- Prefer a suitable European region for both services, after confirming
  availability and any applicable FHNW/data-protection requirements. Select
  the same region so the service can use Render's private PostgreSQL
  connection. European hosting must not be presented as proof of GDPR
  compliance.
- Keep database credentials, authentication secrets, and other operational
  secrets in Render's secret environment configuration, never in source,
  images, logs, or client responses.
- Use the approved Postmark HTTPS API for account verification and recovery
  email. Keep its server token in secret configuration, verify the sender
  signature and service terms, and do not send real personal data until the
  applicable FHNW/privacy and provider-region/retention review is complete.
- Keep shared rate-limit buckets in the same PostgreSQL database as the
  application. Supply a separate high-entropy HMAC secret through secret
  configuration. Configure `TRUSTED_PROXY_CIDRS` only after verifying the
  actual Render proxy network and `X-Forwarded-For` behavior; forwarded headers
  from arbitrary clients are ignored.
- Use Render-managed HTTPS for public web traffic. Configure secure cookies
  independently of untrusted proxy headers; explicitly trust only the known
  platform proxy boundary if forwarded headers are needed. For internal
  PostgreSQL TLS, follow Render's documented connection requirements; its
  internal certificates do not support `verify-ca` or `verify-full`.
- Do not use the web service's ephemeral filesystem or process memory for
  accounts, sessions, or other durable/shared state. Size database and
  application connection pools for the total number of running instances.
- Run schema migrations as a controlled release operation, not concurrently
  from every application worker at startup. Verify backups and migration
  behavior before applying schema changes to any database with retained data.
- Use only synthetic accounts and fictional business/employee data in
  development and the university demonstration. Keep any public deployment
  access-restricted until the approved authentication, security, and
  data-protection requirements have been implemented and verified. Do not
  process real personal data until the applicable FHNW and data-protection
  requirements are confirmed.

## Consequences

- Render region availability, PostgreSQL plan/retention, backup and recovery
  needs, connection limits, service plan, and cost must be verified before
  deployment.
- Verify Postmark delivery, sender configuration, applicable region/terms and
  retention, and shared PostgreSQL rate-limiter behavior in the chosen
  deployment. The existing implementation does not activate cloud services.
- Deployment still needs decisions and verification for Docker/local runtime,
  build and start commands, Alembic release procedure, health checks, logging,
  access restrictions, secret rotation, backup restoration, and smoke tests.
- Confirm with FHNW/supervisor any institutional constraints on data
  classification, processing, region, and external services. Seek applicable
  data-protection review; do not infer compliance from hosting region or
  platform controls alone.

## Rollout and verification plan

Before any public release:

1. Confirm FHNW and applicable data-protection requirements and the data
   classification permitted for the demo.
2. Verify the selected European Render region and matching PostgreSQL
   availability, terms, plan, retention, backups, and recovery procedures.
3. Keep the environment restricted and synthetic-data-only until ADR-005
   authentication and the deployment security controls pass review.
4. Test migration from an empty disposable PostgreSQL database and a
   representative copy of any retained data; verify restore before production
   changes.
5. Verify HTTPS, cookie behavior, origin and trusted-proxy/IP configuration,
   Postmark delivery, secret handling, authentication and tenant isolation,
   rate limits under concurrent instances and database outage, shared session
   behavior, retention cleanup, health checks, logs, backup restoration, and
   release rollback.

## References

- [ADR-005 — Authentication and Public Deployment Boundary](ADR-005-authentication-and-public-deployment-boundary.md)
- [Render Web Services](https://render.com/docs/web-services)
- [Render PostgreSQL connections](https://render.com/docs/postgresql-creating-connecting)
- [Render TLS](https://render.com/docs/tls)
- [Render Environment Variables](https://render.com/docs/environment-variables)
