# UC-001 — Company Registration

## Intent
Allow any authenticated user to create a company in ShiftFlow and become its administrator.

## Actors
Primary: Authenticated user.
Secondary: ShiftFlow.

## Preconditions
- The user's account exists and is authenticated.

## Flow
1. The user submits the required company information: company name, business email address, international business phone number, nonempty business address, and industry.
2. ShiftFlow validates the submitted information.
3. ShiftFlow trims surrounding business-email whitespace and normalizes its domain to lowercase. It validates email using a practical syntax: one `@`, nonempty local part and domain, no whitespace, and valid domain labels.
4. ShiftFlow validates the business phone in E.164 format: `+` followed by 2–15 digits, with a nonzero first digit. The stored value contains no spaces or hyphens. The address must not be empty.
5. ShiftFlow trims the industry and collapses repeated whitespace, then creates the company and generates a unique employee joining code consisting of exactly eight uppercase alphanumeric characters (`A–Z`, `0–9`) using cryptographically secure randomness. The code does not expire automatically in the MVP.
6. ShiftFlow associates the creating user with the company as an administrator; the user may also belong to other companies.
7. ShiftFlow enforces code uniqueness across all companies in the database. On a collision it generates another code, up to five attempts; if all attempts collide, it aborts registration and returns HTTP 503 with error code `JOINING_CODE_UNAVAILABLE`, without exposing sensitive internals.
8. ShiftFlow completes the operation atomically, leaving no partial company, joining-code, or administrator-association records if it fails.

## Errors
- Invalid or missing company information: reject the request without creating company records.
- Unauthenticated request: reject the request without creating company records.
- Joining-code collision on all five attempts: return a safe error without creating company records.
- Joining-code exhaustion is returned as HTTP 503 with error code `JOINING_CODE_UNAVAILABLE`.
- Creation failure: roll back the company, joining code, and administrator association.

## Acceptance
- Given any authenticated user and valid required company information, when the user submits the information, then the company is created with a unique eight-character uppercase alphanumeric joining code and the user is associated as its administrator.
- Given an international-format business phone number and nonempty business address, when the user submits the company information, then both values are accepted without external address verification.
- Given an industry value containing leading, trailing, or repeated whitespace, when the user submits it, then the stored value has surrounding whitespace trimmed and repeated whitespace collapsed.
- Given a valid business email with surrounding whitespace and uppercase domain letters, when the user submits it, then surrounding whitespace is removed and the domain is stored in lowercase.
- Given a business phone in E.164 format, when the user submits it, then it is accepted and stored without spaces or hyphens; other formats are rejected.
- Given two companies, when joining codes are generated, then their codes are unique across all companies.
- Given five consecutive joining-code collisions, when company creation is attempted, then a safe error is returned and no company, joining code, or administrator membership is persisted.
- Given a business email address with valid format that is already used by another company, when the user submits it, then the email address does not by itself prevent company creation.
- Given a user who already belongs to another company, when that user creates a company, then the user can hold memberships in both companies.
- Given missing or invalid required information, when the user submits it, then the request is rejected and no company records are created.
- Given a failure during creation, when the operation is processed, then no partial company, joining-code, or administrator-association records remain.
- Given two different companies, when a user accesses company data, then data belonging to the other company is not accessible.

## Tests
- Unit: required-information and practical email-syntax validation; email normalization; E.164 phone validation; nonempty address validation; industry whitespace normalization; joining-code format and secure generation contract; administrator association.
- Integration: successful atomic creation; normalized email and industry values; global joining-code uniqueness; retry after collision and HTTP 503 `JOINING_CODE_UNAVAILABLE` after five collisions; rollback on failure; company-data isolation; rejected invalid and unauthenticated requests.
- E2E: company registration success and rejection of invalid submissions without partial records.
