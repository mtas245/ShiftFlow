# ADR-005 — Authentication and Public Deployment Boundary

## Status

Accepted — the user approved first-party email/password, email verification and
password recovery as public-demo requirements, a 30-minute idle / 12-hour
absolute session lifetime, and preservation of existing membership identities.
Login, durable sessions, trusted-principal integration, logout, CSRF
protection, and the application/HTTP workflows for email verification and
password recovery are implemented. The user also approved Postmark HTTPS
delivery and PostgreSQL-backed shared rate limiting; both adapters and their
HTTP/persistence wiring are implemented. Public access must remain restricted
until provider and proxy configuration, security, and data-protection
requirements are verified.

## Context

UC-001 through UC-004 require an authenticated principal. FastAPI accepts only
an `AuthenticatedUser` installed from a validated PostgreSQL session and
otherwise fails closed. Application services separately authorize company
membership, role, and tenant scope as required by ADR-002.

The schema stores accounts, sessions, account-action tokens, and shared
rate-limit buckets. Memberships continue to store an opaque text `user_id`;
company business email is separate contact information and is not an account
credential. The application is intended to serve a same-origin browser UI and
deploy as a FastAPI web service on Render with PostgreSQL.

The project artifacts establish no additional FHNW-specific authentication
vendor, protocol, or MFA requirement. The user has clarified that FHNW
requirements for data classification, hosting regions, and personal-data
processing are not yet formally confirmed. Use synthetic accounts and
fictional company/employee data for development and university demonstration.
Confirm FHNW and applicable data-protection requirements before processing real
personal data. Prefer an appropriate European Render/PostgreSQL region subject
to availability and verification; European hosting alone does not establish
GDPR compliance.

## Alternatives

1. **First-party email/password with server-managed PostgreSQL sessions.**
   Fits the current FastAPI boundary and same-origin browser UI, but ShiftFlow
   assumes responsibility for credential handling, account lifecycle, and
   abuse controls.
2. **External OpenID Connect identity provider.** Delegates credential storage,
   login and recovery to a specialist provider, but introduces a provider,
   configuration, account-linking and availability dependency not selected
   elsewhere in the project.
3. **Third-party hosted authentication service.** Reduces custom authentication
   work but adds vendor, cost, data-processing, integration and lock-in
   considerations. No such service is currently approved.
4. **Development identity or client-supplied user IDs.** Rejected for public
   and production use because it allows impersonation and violates the trusted
   principal contract in ADR-001 and the use-case specifications.

## Decision

Use first-party email/password accounts for the MVP, with the following
architecture:

- Add a durable account identity with a stable opaque ID. Normalize and enforce
  unique account email independently of each company's business contact email.
  The authenticated account ID is the only source for `AuthenticatedUser`.
- Store passwords only as Argon2id adaptive hashes using a maintained
  implementation and current OWASP minimum guidance, calibrated to the
  deployed service capacity. Never store or log plaintext passwords.
- Use opaque, cryptographically random browser session tokens. Persist only a
  one-way digest of each token in PostgreSQL, alongside its account, expiry,
  revocation and CSRF state. Do not use process-local session storage or treat
  a signed client token alone as a revocable session.
- Set a host-only `__Host-` session cookie with `Secure`, `HttpOnly`,
  `SameSite=Lax`, and `Path=/`; do not set a `Domain` attribute. Require HTTPS
  for public traffic. The Render edge provides HTTPS/TLS and forwards requests
  to the service over HTTP, so production cookie security must be configured
  explicitly and must not depend on untrusted forwarded headers.
- Protect every state-changing cookie-authenticated request with a
  session-bound synchronizer CSRF token and validate the expected `Origin`.
  SameSite is defense in depth, not the only CSRF control. Keep the UI and API
  same-origin; do not enable permissive cross-origin credentialed requests.
- On each request, validate the presented session against PostgreSQL, reject
  expired or revoked sessions, load the associated account, and construct the
  existing `AuthenticatedUser` principal server-side. The session is identity
  only: it carries no company roles or tenant permissions. Existing application
  services remain responsible for membership, role, and tenant authorization.
- Rotate the session on successful login and invalidate it on logout. Password
  change/reset and account-security events revoke all sessions for that
  account. Use the approved lifetime: 30 minutes idle and 12 hours absolute,
  with no persistent "remember me" option for the MVP.
- Never enable a development identity adapter in public or production mode.
  Without a valid session, endpoints continue returning the approved generic
  HTTP 401 `AUTHENTICATION_REQUIRED`.
- Apply generic login failures to avoid account enumeration, rate-limit
  registration/login/recovery attempts, and ensure the rate limiting strategy
  is shared across service instances or enforced at a trusted edge. Do not log
  passwords, session values, CSRF tokens, or recovery tokens.
- Send verification and recovery email through Postmark's HTTPS API using the
  replaceable `EmailDelivery` port. Read the server token and sender address
  only from `POSTMARK_SERVER_TOKEN` and `EMAIL_FROM_ADDRESS`; reject partial
  configuration. Use bounded HTTP timeouts, do not follow redirects, do not
  enable open/click tracking, and never log provider response bodies, recipient
  addresses, delivery tokens, or credentials.
- Use atomic PostgreSQL upserts for shared rate-limit buckets, with only
  HMAC-SHA-256 digests of normalized email/IP keys persisted. Keep the
  HMAC secret separate from database/provider credentials and require at
  least 32 bytes. Apply limits: registration 3/email/day and 10/IP/hour;
  login 5/email/15 minutes and 20/IP/15 minutes; verification resend
  3/email/hour and 10/IP/hour; password-reset request 3/email/hour and
  10/IP/hour. Rate-limit denial returns generic 202 for registration/resend/
  reset requests and generic 429 for login. Limiter outage returns generic
  503 for requests checked by the limiter, without performing account
  lifecycle work or password verification.
- Resolve client IP from the socket peer by default. Honor
  `X-Forwarded-For` only when that immediate peer matches an explicitly
  configured `TRUSTED_PROXY_CIDRS`; malformed chains from a trusted peer fail
  closed. Confirm Render's actual proxy network and forwarding behavior before
  configuring this value. Never trust arbitrary forwarded headers.

The user approved Postmark and PostgreSQL for these services. Windows are
anchored on the first request in a bucket rather than wall-clock aligned;
short bursts at a window boundary are possible, so rate limiting is not a
replacement for trusted-edge abuse controls. Expired bucket rows are purged
opportunistically at most hourly while application requests are active. A
scheduled cleanup policy for expired sessions and account-action tokens
remains a release requirement. MFA is deferred for the synthetic-data capstone
MVP and must be reconsidered before production use with sensitive employee or
business data.

## Consequences

- Account, session, action-token, and rate-limit persistence are represented by
  additive Alembic migrations; existing application services continue
  receiving the current `AuthenticatedUser` abstraction.
- Company memberships must resolve to durable account IDs. Before adding a
  membership-to-account foreign key, inventory and preserve every persistent
  membership identity through an explicit mapping. The user explicitly
  rejected discarding existing memberships as a migration strategy. Do not
  invent account emails or credentials for legacy IDs.
- PostgreSQL-backed sessions work across Render instances and support
  immediate logout/revocation. They add database reads and writes to
  authentication and require session cleanup/expiry handling and connection
  capacity planning.
- Render web-service local files and process memory are not the durable shared
  session store. Use the same-region Render PostgreSQL private connection for
  the application, keep credentials in Render environment secrets, and budget
  connection pools across all instances. Render documents that internal
  PostgreSQL connections require the service and database to share a region;
  when requiring TLS on that private connection, Render documents
  `sslmode=require` because its internal certificates do not support
  `verify-ca` or `verify-full`. Select a database plan with retention and backup
  behavior suitable for the data and presentation lifetime.
- Use synthetic test accounts and fictional company/employee data for
  development and the university demonstration. Keep any public deployment
  restricted until authentication, security, and data-protection requirements
  are satisfied. Before using real personal data, confirm applicable FHNW
  requirements and data-protection obligations. Prefer an appropriate
  European Render and PostgreSQL region after verifying availability and
  requirements; European hosting by itself is not a GDPR compliance
  determination.
- Configure the expected public origin and trusted proxy/network boundary
  explicitly. Render terminates TLS before forwarding HTTP to the web service;
  never trust arbitrary client-provided `Forwarded` or `X-Forwarded-*` values.
- Account registration, email verification, password recovery, and login
  have HTTP contracts and implementation coverage, but still require production
  configuration, operational retention, privacy review, and abuse monitoring.
- Do not use company roles as account roles. Company membership remains the
  sole source of company-specific authorization.

## Threat considerations

- **Credential theft, stuffing and brute force:** Argon2id slows offline
  guessing; rate limits and generic failures reduce online abuse and
  enumeration. Tune hashing cost without allowing unbounded login resource
  exhaustion.
- **Database disclosure:** individual salted password hashes and digested
  session/recovery tokens limit direct reuse; protect database access,
  backups, environment secrets and logs.
- **Session theft or fixation:** TLS-only secure cookies, unpredictable opaque
  tokens, session rotation, server-side expiry and revocation reduce exposure.
  XSS can still act as the user; apply normal output encoding and content
  security controls in the browser UI.
- **CSRF:** require session-bound tokens and origin checks on unsafe methods;
  do not rely on `SameSite` alone.
- **Authorization/tenant escalation:** authentication proves account identity
  only. Continue checking membership, company role, and tenant scope on every
  operation; do not trust IDs, roles, or company claims supplied by the
  browser.
- **Account recovery and enumeration:** generic request responses, single-use
  expiring digest-only tokens, and verified-account login are implemented.
  Delivery failures do not expose provider details. Shared rate limits use
  HMAC-digested keys and fail closed on database outage; edge and proxy
  configuration remain release prerequisites.
- **Deployment misconfiguration:** missing secrets, insecure cookies, arbitrary
  proxy-header trust, development impersonation, or process-local sessions
  must fail closed or be caught by deployment validation.

## Test and rollout plan

The design decision itself did not include application code or executable
tests. Implementation verification is recorded in
[TASKS.md](../TASKS.md). The approved account registration, login, logout,
session expiry, email-verification, and password-recovery contracts were
implemented with RED → GREEN → REFACTOR. Verification includes:

- Unit tests for email normalization policy, password hashing/verification,
  session and recovery token generation/digests, expiry, revocation, and
  principal construction.
- Isolated PostgreSQL tests for account uniqueness, session persistence across
  independently composed application instances, expiry/revocation, concurrent
  registration, rollback, migration behavior, and preservation/mapping of any
  existing membership identities.
- HTTP tests for trusted identity, missing/invalid/expired session, login
  rotation, logout, CSRF and Origin enforcement, generic errors, rate limits,
  client-supplied identity rejection, and absence of secrets in responses and
  logs.
- Postmark HTTP request/error behavior, safe provider failures, forwarding
  header spoof resistance, trusted proxy chains, all approved rate thresholds,
  cross-instance concurrent bucket updates, pseudonymous storage, cleanup, and
  migration parity.
- Deployment checks for HTTPS-only secure cookies on Render, expected-origin
  configuration, proxy trust, secret presence, shared sessions across
  instances, PostgreSQL connectivity/connection limits, and fail-closed
  production configuration.
- Roll out through a forward database migration and verified backup. Keep the
  existing fail-closed behavior until account/session infrastructure is
  complete; do not introduce a public impersonation fallback.

## Remaining release decisions and verification

- Before any future membership-linkage migration, inspect the target database
  and design a lossless mapping from all existing membership IDs to accounts.
  The preservation requirement is approved; no identities may be silently
  dropped.
- Configure Postmark and its sender, manage/rotate the rate-limit HMAC secret,
  verify the Render trusted-proxy CIDRs and forwarding behavior, and test
  outage handling in the deployed topology. No service is activated by this
  implementation.
- Define scheduled cleanup and retention for sessions and account-action
  tokens; current rate-limit cleanup is opportunistic and its storage is
  pseudonymous, not anonymous.
- Confirm FHNW data classification, hosting-region, and personal-data
  processing requirements before any real personal data is used. Verify
  Postmark's applicable region, retention, terms, and data-processing
  obligations. Keep public access restricted until these requirements and the
  other security controls are verified.

## References

- [ADR-001 — Development Authentication Boundary](ADR-001-development-authentication-boundary.md)
- [ADR-002 — Tenant Isolation](ADR-002-tenant-isolation.md)
- [OWASP Password Storage Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)
- [OWASP CSRF Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)
- [Render Web Services](https://render.com/docs/web-services)
- [Render Scaling](https://render.com/docs/scaling)
- [Render TLS](https://render.com/docs/tls)
- [Render PostgreSQL connections](https://render.com/docs/postgresql-creating-connecting)
- [Render Environment Variables](https://render.com/docs/environment-variables)
- [ADR-006 — MVP Runtime and Deployment](ADR-006-mvp-runtime-and-deployment.md)
