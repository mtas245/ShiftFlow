# UC-002 — Employee Joining

## Intent
Allow an authenticated employee to join the intended company using its joining code.

This use case covers creating an employee membership only. Membership approval,
invitations, role changes, code expiry or rotation, and availability or shift
management are outside its MVP scope; joining codes do not expire automatically
under ADR-003.

## Actors

Primary: Authenticated employee.
Secondary: ShiftFlow.

## Preconditions

- The employee's account exists and is authenticated through the accepted authentication boundary.
- The joining user ID is supplied by the trusted authentication principal, not by client input.
- The target company has a joining code generated under ADR-003.

## Flow

1. The employee submits the company joining code and a company-specific display name.
2. ShiftFlow accepts the code only if it exactly matches eight uppercase alphanumeric characters (`A–Z`, `0–9`); it does not normalize case or whitespace. It trims the display name and requires 1–80 characters with no control characters.
3. ShiftFlow resolves the code to one company. A malformed or unknown code produces the same generic public error.
4. ShiftFlow obtains the user ID only from the trusted authentication principal and associates that user with the resolved company as an employee, storing the submitted display name on this membership and without changing any of the user's other company memberships.
5. ShiftFlow completes membership creation atomically and prevents duplicate membership for the same user and company.
6. ShiftFlow authorizes tenant-owned operations using the user's membership in the requested company. The joining code or a client-supplied company identifier is not authorization for another company's data.

## Errors

- Unauthenticated request: return HTTP 401 with generic error code `AUTHENTICATION_REQUIRED`; create no membership.
- Client-supplied acting user ID: return HTTP 400 with error code `INVALID_REQUEST`; the user identity comes exclusively from the trusted authenticated principal and no membership is created.
- Malformed or unknown code: return HTTP 400 with error code `INVALID_JOINING_CODE` in both cases, without disclosing whether a company exists.
- Invalid display name: return HTTP 400 with error code `INVALID_REQUEST`; do not create a membership or disclose company information.
- Existing membership for the authenticated user and resolved company: return HTTP 409 with error code `ALREADY_A_MEMBER`, without creating another membership or changing the existing role.
- Unexpected membership persistence failure: roll back the operation and return HTTP 500 with error code `MEMBERSHIP_CREATION_FAILED`, without exposing internal database details. Known duplicate-membership conflicts use HTTP 409 `ALREADY_A_MEMBER`.
- Unauthorized access to another company's tenant-scoped resource: return HTTP 404 with error code `RESOURCE_NOT_FOUND`, without revealing whether the resource exists.
- A joining code does not grant administrator privileges or access to any company other than the company it resolves to.

## Acceptance

- Given an authenticated user and a company with a valid joining code, when the user submits that code and a valid display name, then exactly one membership is created for that user in that company with the Employee role and the company-specific display name.
- Given an employee submits a display name with surrounding whitespace, when they join, then the stored membership display name has surrounding whitespace removed.
- Given an empty, over-80-character, or control-character-containing display name, when a join request is processed, then it returns HTTP 400 `INVALID_REQUEST` and creates no membership.
- Given a user who already belongs to another company, when they join using a different company's valid code, then the new Employee membership is added and the existing membership is unchanged.
- Given a submitted code that is not exactly eight uppercase alphanumeric characters, including lowercase or surrounding whitespace, when the user attempts to join, then the request returns HTTP 400 `INVALID_JOINING_CODE` and no membership is created.
- Given a well-formed code that does not belong to a company, when the user attempts to join, then the request returns the same HTTP 400 `INVALID_JOINING_CODE` response as for malformed codes, no membership is created, and no company information is disclosed.
- Given a user who is already a member of the company resolved by the code, when they submit the code again, then the request returns HTTP 409 `ALREADY_A_MEMBER`, exactly one membership remains, and its role is unchanged.
- Given concurrent attempts by the same user to join the same company, when both requests are processed, then at most one membership exists for that user and company and any request finding that membership returns HTTP 409 `ALREADY_A_MEMBER`.
- Given a request without an authenticated user, when a joining code is submitted, then the request returns HTTP 401 `AUTHENTICATION_REQUIRED` and no membership is created.
- Given a client submits an acting user ID, when the join request is processed, then the request returns HTTP 400 `INVALID_REQUEST` and no membership is created; identity is obtained exclusively from the trusted authenticated principal.
- Given a user is not a member of the company that owns a requested tenant-scoped resource, when they request that resource, then the request returns HTTP 404 `RESOURCE_NOT_FOUND` without revealing whether the resource exists; a joining code does not authorize access.
- Given an unexpected persistence failure occurs while creating a membership, when the join operation ends, then the transaction is rolled back, no partial membership remains, and the request returns HTTP 500 `MEMBERSHIP_CREATION_FAILED` without internal database details.
- Given a known membership uniqueness conflict occurs, when the join operation ends, then the request returns HTTP 409 `ALREADY_A_MEMBER` and no duplicate membership exists.

## Tests

- Unit: exact joining-code format validation without normalization; display-name validation and normalization; identity taken only from the trusted principal; reject client-supplied acting user IDs; map a resolved code to an Employee membership; distinguish known duplicate conflicts from unexpected persistence failures.
- Integration: successful atomic membership persistence including display name; malformed and unknown codes return the same HTTP 400 `INVALID_JOINING_CODE`; unauthenticated requests return HTTP 401 `AUTHENTICATION_REQUIRED`; client-supplied acting user IDs return HTTP 400 `INVALID_REQUEST`; invalid display names are rejected without membership creation; duplicate and concurrent duplicate attempts return HTTP 409 `ALREADY_A_MEMBER` without duplicate rows; unexpected persistence failures roll back and return HTTP 500 `MEMBERSHIP_CREATION_FAILED` without database details; unauthorized tenant-resource access returns HTTP 404 `RESOURCE_NOT_FOUND`; authorization is scoped to the resolved company.
- E2E: an authenticated employee joins with a company's code and display name and receives the employee membership; malformed and unknown codes have the same generic response; unauthenticated joining returns HTTP 401 `AUTHENTICATION_REQUIRED`; supplied acting identity returns HTTP 400 `INVALID_REQUEST`; invalid display names are rejected; duplicate joining returns HTTP 409 `ALREADY_A_MEMBER` without changing membership state; tenant-scoped access outside the user's memberships returns HTTP 404 `RESOURCE_NOT_FOUND`.

## Design

### Components

- **Domain:** Reuse `Company` and `CompanyMembership`; add the `EMPLOYEE` role to the existing membership role set. Store the required company-specific display name and an opaque public UUID on the membership. No new entity is required.
- **Application:** Add `JoinCompany` and a command containing the joining code and company-specific display name. Receive the existing authenticated-user abstraction as a separate trusted argument. Extend the existing company and membership repository interfaces with lookup by joining code and lookup by `(company_id, user_id)`; reuse the Unit of Work.
- **Infrastructure:** Extend the SQLAlchemy repositories to implement those lookups and membership creation using the Unit of Work's session. The existing database uniqueness constraint `uq_company_memberships_company_user` is the concurrency guard. Add a nullable `account_user_id` foreign key to `users.id` and a unique public membership UUID in an additive migration. New memberships set `account_user_id` only from the trusted principal. Do not infer or rewrite legacy identities; follow the reviewed mapping preflight in ADR-004.
- **Interface:** Add an authenticated FastAPI join endpoint. Its request contains the joining code and display name; it rejects client-supplied acting-user identity fields. The authentication dependency supplies the principal to `JoinCompany`, returns the specified 401 when absent, and follows ADR-001's startup guard so development impersonation fails closed in public or production mode. Roster and assignment contracts use the tenant-scoped public membership UUID and never expose global account IDs or email addresses.

### Application and transaction flow

`JoinCompany` validates the code's exact eight-character uppercase alphanumeric format, then opens one Unit of Work. Within that transaction it finds the company by code, checks for an existing membership for the authenticated principal and resolved company, creates an `EMPLOYEE` membership, and commits on successful Unit of Work exit. The existing membership check provides the normal duplicate path; database uniqueness handles races between concurrent requests.

The SQLAlchemy membership adapter translates only a violation of `uq_company_memberships_company_user` into the application-level duplicate-membership conflict. The Unit of Work rolls back when that conflict or another exception leaves the context. The HTTP boundary maps the known conflict to `ALREADY_A_MEMBER`; unexpected persistence exceptions remain distinguishable and map to the safe server error. It must not turn arbitrary database failures into duplicate conflicts.

### Approved HTTP contract

Human-approved:

- Route: `POST /companies/join`.
- Request body: `{"joining_code": "<code>", "display_name": "<company-specific name>"}`. Display name is trimmed and must contain 1–80 characters after trimming with no control characters. A client-supplied acting-user ID is rejected with HTTP 400 `INVALID_REQUEST`; identity is never accepted from the body.
- Success: HTTP 201 with `{"company_id": "<id>", "role": "employee"}`.
- Error body: `{"error": {"code": "<ERROR_CODE>"}}`.
- Errors: HTTP 401 `AUTHENTICATION_REQUIRED`; HTTP 400 `INVALID_REQUEST` for a supplied acting-user ID; HTTP 400 `INVALID_JOINING_CODE` for both malformed and unknown codes; HTTP 409 `ALREADY_A_MEMBER` for an existing membership or recognized concurrent uniqueness conflict; HTTP 404 `RESOURCE_NOT_FOUND` for unauthorized tenant-scoped resource access; HTTP 500 `MEMBERSHIP_CREATION_FAILED` for an unexpected membership persistence failure.
- Responses never include database details or disclose whether a joining code or tenant resource exists.
- The display name is stored on the company membership, not on the global User account, and may differ between companies.
- A new membership receives an opaque tenant-scoped public UUID. The `employee_id` storage/relational column remains an internal persistence identity and is not returned by roster or assignment HTTP responses.
- Existing opaque memberships are preserved; no automatic equality-based mapping to User accounts is allowed. The explicit mapping preflight and unresolved-row behavior are defined in ADR-004.

The design and original HTTP contract are approved. This display-name amendment
was approved on 29 October 2026 for a future TDD implementation milestone. The
authentication boundary follows ADR-001, and tenant operations remain
membership-authorized and company-scoped as required by ADR-002. Joining code
format and lifecycle follow ADR-003. No additional ADR is required.
