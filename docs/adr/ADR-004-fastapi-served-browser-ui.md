# ADR-004 — FastAPI-Served Browser UI

## Status

Accepted — on 29 October 2026 the user approved the FastAPI/Jinja2 and
first-party JavaScript architecture and authorized follow-on design work for
the browser-supporting API contracts below. The API design was subsequently
approved, including company-specific membership display names and the
legacy-identity migration preflight. This ADR does not authorize implementation
in the current design milestone. The prior checkout referenced ADR-004 but did
not contain its file.

## Context

ShiftFlow is a FastAPI/PostgreSQL application with JSON HTTP boundaries for
account lifecycle, company registration/joining, availability, and shift
scheduling. It uses a same-origin `HttpOnly`, `Secure`, host-only session
cookie, a session-bound CSRF token, and configured-Origin validation.
Application services remain responsible for authentication-dependent role
checks and tenant isolation; the browser is not an authorization boundary.

The capstone browser UI must support employee and administrator workflows on
mobile, tablet, and desktop. It must remain a standards-compliant web
application. Apple's Human Interface Guidelines and SwiftUI documentation are
visual and interaction references only; ShiftFlow is not a native SwiftUI app,
does not use Apple's proprietary assets, and does not claim Apple affiliation
or certification.

## Alternatives

1. **FastAPI templates with progressive enhancement.** Render pages with
   Jinja2, semantic HTML, and CSS; use small first-party JavaScript modules for
   same-origin calls to the existing JSON API and for scheduling interactions.
   This keeps deployment, authentication, and browser traffic in one service
   and avoids a separate build/deployment pipeline.
2. **A separate SPA using React, Vue, or a similar framework.** Provides a
   richer client component ecosystem but adds a toolchain, client-side state,
   dependency and build maintenance, and another deployment/integration
   boundary without a demonstrated MVP need.
3. **HTMX or another server-fragment framework.** Can reduce custom JavaScript,
   but requires HTML-fragment/form endpoints in addition to the current JSON
   API. That would create a second presentation contract to maintain.
4. **Native SwiftUI.** Rejected because the product is required to remain a
   browser application served by FastAPI.

## Proposed decision

### Frontend architecture

- Keep the single FastAPI application and serve Jinja2 page templates and
  first-party static assets from it. Organize the future presentation layer
  under the existing `interfaces` boundary; do not put domain rules in
  templates or browser code.
- Use semantic HTML forms and native browser controls where practical. Add
  small vanilla ES modules for JSON form submission, schedule navigation,
  clipboard actions, and other interactions that materially improve usability.
  Do not add a separate frontend application, client-side router, CSS
  framework, component framework, or third-party script unless a later ADR
  establishes a concrete need.
- This is progressive enhancement of server-rendered pages, not a promise of
  complete no-JavaScript operation: mutations currently use JSON APIs. A
  complete native-form fallback would require additional server-side form
  adapters and should be considered only if no-script operation becomes a
  requirement.
- Treat the existing JSON endpoints as the API contract. Browser requests use
  same-origin credentials, the configured `Origin`, and the session-bound
  `X-CSRF-Token` on every unsafe request. Never read or set the session cookie
  from JavaScript. Never accept user IDs, roles, or tenant access from browser
  state as authority.
- Keep account-enumeration-safe messages and generic error envelopes intact.
  Render `401` as a sign-in state, `403` as insufficient company role,
  tenant-safe `404` as unavailable/not found, and `409` as an actionable
  scheduling conflict without exposing another company's schedule.
- Use deterministic email capture and synthetic accounts for local and E2E
  flows. Public registration and deployment remain gated by ADR-005 and
  ADR-006 release checks. Hiding a registration link in the UI is not a
  security control; server-side configuration or deployment access controls
  must enforce any registration restriction.

### Visual system

Apply Apple's HIG principles of clarity, hierarchy, consistency, adaptability,
and accessibility without reproducing Apple layouts or assets.

- **Typography:** Use the platform system stack
  `-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`; do not bundle
  Apple's font files. Use a readable body size near `1rem`, a restrained
  display title, tabular numerals for schedule times, and relative sizing so
  browser text scaling is honored.
- **Color tokens:** Use semantic tokens with light/dark values. Proposed light
  base: canvas `#F5F6F7`, surface `#FFFFFF`, grouped surface `#EEF1F2`,
  primary text `#172126`, secondary text `#536168`, separator `#D8E0E2`,
  ShiftFlow teal accent `#006B66`, success `#246B45`, warning `#805000`, and
  error `#B42318`. Proposed dark base: canvas `#101719`, surface `#182124`,
  grouped surface `#202B2E`, primary text `#F2F6F6`, secondary text
  `#B5C1C3`, separator `#354347`, accent `#72D6C9`, success `#75D39A`,
  warning `#F2C36B`, and error `#FF8A80`. Validate every text/control pairing
  against WCAG 2.2 AA in both modes and provide an increased-contrast
  treatment; these values are starting tokens, not a substitute for testing.
- **Spacing and shape:** Use a 4 px base spacing scale
  (`4, 8, 12, 16, 24, 32, 48`), 12 px card radii, 10 px form-control radii,
  thin separators, and subtle shadows only to distinguish overlapping
  surfaces. Prefer whitespace and alignment over decorative gradients.
- **Surfaces and interaction:** Use opaque surfaces for schedule data and
  forms. Reserve translucency for a small navigation/tool surface only where
  contrast remains robust. Provide clear hover, keyboard focus, active,
  selected, loading, disabled, success, and error states. Motion is brief and
  functional; respect `prefers-reduced-motion`.
- **Appearance:** Follow `prefers-color-scheme` by default, with a user-selectable
  light/dark/system appearance. Store only this non-sensitive preference in
  browser storage.

### Responsive navigation

- **Mobile:** Use a compact top bar with page title and contextual back action.
  Use a fixed bottom navigation for the primary role-specific destinations,
  with touch targets at least 44 by 44 CSS px and safe-area insets. Keep
  company selection and account actions in a labeled menu.
- **Tablet:** Use a collapsible navigation rail/sidebar and a two-pane
  schedule/detail layout when space allows. Collapse to a single pane when
  width or zoom makes the split cramped.
- **Desktop:** Use a quiet, persistent sidebar with company context and
  grouped navigation; keep the main content at a readable maximum width and
  allow the weekly schedule to use the available width.
- Navigation labels and active states remain visible; icons are supplemental,
  not the only label. Browser back/forward navigation must work.

### Screens and journeys

1. **Account access:** Register with email/password; show a neutral
   verification-sent confirmation; verify from the email link; sign in; request
   password recovery; set a new password; and sign out. Never indicate whether
   an address is registered. Registration remains inaccessible in any
   environment whose approved release controls are not satisfied.
2. **Company setup:** During authenticated company creation, collect the
   administrator's company-specific display name. Prominently show the
   eight-character joining code with a labeled copy action and a reminder to
   share it only with intended employees. Joining collects the exact code and
   a company-specific employee display name and reports generic
   invalid-code/already-member errors. Do not persist the joining code in
   browser storage.
3. **Company context:** Provide a company switcher showing only companies for
   which the signed-in user has a membership and the user's role in each.
   The current API cannot populate this reliably; the required API gap is
   listed below.
4. **Employee home:** Show the next assigned shifts, a clear no-shifts state,
   a compact availability summary, and direct actions to add/update
   availability. Use only actual assignment and availability data; do not
   suggest that availability is a confirmed shift.
5. **Employee availability:** Present date-specific start/end controls,
   explain that intervals may cross midnight, show the selected local time
   zone, and list editable intervals. Explain overlap errors next to the
   interval without implying recurring availability. There is no delete
   operation in the approved use case.
6. **Administrator home/team:** Show the selected company's schedule and
   availability planning cues, a roster of company-specific employee display
   names, and a clear create-shift action. Roster entries use opaque
   tenant-scoped membership references rather than account IDs or email.
7. **Shift creation and assignment:** Create one shift slot, then optionally
   assign an eligible same-company employee. Show an unassigned shift as
   explicitly unassigned. Explain availability coverage and overlap
   conflicts; the server remains authoritative. Never expose or infer the
   details of a conflict in another company.

Use synthetic hospitality examples only, such as a fictional café shift
`07:00–15:00` or an overnight `22:00–06:00 (+1 day)`.

### Weekly schedule

- Default to a clearly labeled week with previous/next week controls and a
  “Today” action. Show the business-local dates and the viewer's time zone.
- On desktop, use seven day columns with a visible time scale and readable
  shift cards. On tablet, allow a day/agenda split. On mobile, use a selected
  day and a chronological agenda list instead of a compressed seven-column
  grid.
- Each shift card states date, start/end, whether it is assigned, and the
  employee's company-specific display name where the API supplies one.
  Overnight shifts show the next-day marker and remain one shift.
- Distinguish assignment with both text and shape/icon, not color alone.
  Availability is a separate planning signal and must never be shown as
  assignment confirmation. Unassigned shifts have a text label and a direct
  assignment action for administrators.
- Provide a semantic list/table alternative to the visual calendar with
  keyboard-operable controls and equivalent content. On conflict, show a
  concise generic message and preserve the entered shift data for correction.
- `SHIFT_ASSIGNMENT_CONFLICT` can arise from a cross-company assignment.
  Explain only that the employee has an overlapping assignment; never identify
  another company, shift, or time.
- The current shift endpoint returns the whole company schedule without date
  range or pagination parameters. The approved bounded API contract replaces
  unbounded reads before the browser UI depends on schedule lists.

### Existing HTTP contract mapping

| Screen/action | Existing contract | UI behavior and boundary |
|---|---|---|
| Register account | `POST /auth/register` | Generic 202 response; display the same confirmation for eligible/ineligible email. |
| Resend verification | `POST /auth/email-verification/request` | Generic 202; do not confirm account existence. |
| Verify account | `POST /auth/email-verification/confirm` | Submit one-time email token; show success or safe invalid/expired-token state. |
| Request/reset password | `POST /auth/password-reset/request`, `POST /auth/password-reset/confirm` | Generic request response; never expose account existence or token details. |
| Login/logout | `POST /auth/login`, `POST /auth/logout` | Login receives a CSRF token and sets the HttpOnly cookie; logout requires Origin and CSRF. |
| Create company/join company | `POST /companies`, `POST /companies/join` | Approved request amendments require a company-specific `display_name` for the administrator or employee membership; the existing response shapes remain unchanged. |
| Submit/update availability | `POST /companies/{company_id}/availability`, `PUT /companies/{company_id}/availability/{availability_id}` | Send RFC 3339 offset-qualified values; display returned UTC instants in the selected local zone. |
| Employee/admin availability reads | `GET /companies/{company_id}/availability/mine`, `GET /companies/{company_id}/availability` | The approved bounded contract returns company-scoped membership references and display names where relevant; backend role and tenant checks remain authoritative. |
| Create/assign/list shifts | `POST /companies/{company_id}/shifts`, `POST /companies/{company_id}/shifts/{shift_id}/assignments`, `GET /companies/{company_id}/shifts` | Assignment sends a tenant-scoped membership public UUID, never a global account ID; backend validates same-company Employee membership, full availability coverage, and global overlap. |
| Employee assigned shifts | `GET /companies/{company_id}/shifts/mine` | Shows only the authenticated employee's assignments in the selected company. |

### Approved browser-supporting API contracts

The following interface and additive-schema designs were approved on
29 October 2026. They remain unimplemented and require a separate TDD
milestone.

- **Session/bootstrap and CSRF after login:** add authenticated
  `POST /auth/session/bootstrap`, requiring the valid HttpOnly session cookie
  and exact configured `Origin`. It does not require the prior CSRF header;
  that exemption applies only to this endpoint. Mint a new random
  session-bound CSRF token, store its digest, and retain the prior digest for
  five minutes so recently opened tabs continue to work. Return only
  `{"csrf_token":"..."}`; never expose the session token, user ID, or email.
  Missing, expired, or revoked sessions return 401 `AUTHENTICATION_REQUIRED`
  and clear invalid cookies. Return JSON with `Cache-Control: no-store`,
  `Vary: Cookie`, and no permissive credentialed CORS. This requires an
  additive migration for the previous digest and expiry.
- **Membership discovery/switching:** add authenticated,
  principal-scoped `GET /companies/mine` returning bounded items containing
  only `membership_id`, `company_id`, company `name`, and membership `role`
  for memberships linked to the authenticated account.
  Do not return joining codes or company contact details. Use keyset pagination
  with `limit=100` by default, maximum 200, and an opaque cursor; order by
  `company_id, public_id`.
- **Employee roster and assignment references:** add
  `GET /companies/{company_id}/employees`, restricted to administrators in
  that company, returning Employee memberships only with an opaque membership
  UUID and company-specific `display_name`, and only for linked memberships.
  Use keyset pagination with
  `limit=100` by default, maximum 200, and an opaque cursor; order by
  `public_id`. Do not return global account IDs, email addresses, other
  tenants' memberships, or assignments. A non-member gets tenant-safe 404; an
  Employee member gets 403. Assignment request body is
  `{"membership_id":"<public UUID>"}` and successful response identifies that
  same membership reference plus its display name. Assignment/schedule
  responses and all availability-list items also use the scoped membership
  reference/name instead of global employee IDs. The application
  resolves the reference within the authorized company before applying
  existing Employee-role, full-availability, and cross-company-overlap checks.
- **Company-specific display names:** amend authenticated `POST /companies`
  and `POST /companies/join` requests to require `display_name` for the newly
  created Administrator or Employee membership. Store the value on
  `CompanyMembership`, not the global User. Trim surrounding whitespace;
  require 1–80 characters after trimming and reject empty strings and Unicode
  control characters. A person may use a different display name in each
  company. The approved amendments are specified in UC-001 and UC-002; the
  existing success response shapes remain unchanged.
- **Bounded scheduling reads:** update the existing own/company availability
  and shift-list endpoints; do not retain unbounded list alternatives. Require
  `from` and `to` RFC 3339 timestamps with explicit offsets, with `from < to`
  and a maximum 31-day window. Return rows whose intervals overlap the
  half-open requested window (`start_at < to AND end_at > from`), with UTC
  timestamps. Order by `start_at, id`; use keyset pagination with default
  `limit=100`, maximum 200, and an opaque `cursor`. Return
  `{"items":[...],"next_cursor":null}` without an unbounded total count.
  Invalid/missing timestamps, ranges, cursors, or limits return HTTP 400
  `INVALID_REQUEST`. Tenant/role predicates remain in the
  application/repository query, and each query fetches at most one page plus
  one row. Availability and shift list response arrays become this paginated
  object before any browser client depends on the existing array shape.
- **Additive membership schema and legacy preflight:** add nullable
  `company_memberships.account_user_id` referencing `users.id`, unique
  constraint `(company_id, account_user_id)`, a unique random public UUID per
  membership, and nullable legacy-compatible `display_name`. Existing
  `user_id` values and dependent foreign-key references remain unchanged. New
  memberships set `account_user_id` only from the trusted principal and
  require a valid display name. Migration must not infer ownership by matching
  text IDs or email addresses. Before an environment with retained memberships
  uses account-backed authorization, inventory memberships and dependent
  availability/assignment rows; require company-representative verification
  and independent review for each proposed one-to-one mapping, collect an
  approved company-specific display name, check same-company
  uniqueness/collisions, and apply approved mappings transactionally with a
  retained audit/export and rollback plan. Unmatched rows remain unlinked and
  inaccessible to account-backed roster operations. Linked legacy schedule
  queries must resolve `account_user_id` through membership without rewriting
  employee identity columns; overlap checks and advisory locks must consistently
  use the resolved global account identity across companies. Environments with
  retained rows must not claim those identities are linked until the preflight
  is approved and completed.

### Approved API contract test scope

The separate TDD milestone must add unit, FastAPI HTTP, and isolated
PostgreSQL tests before implementation. At minimum, cover session bootstrap
after login, refresh, and a new tab; exact-Origin and CSRF handling; invalid
or expired sessions; concurrent CSRF bootstrap; principal-only membership
listing; administrator-only roster access; absence of global IDs/emails in
roster, availability, and assignment/schedule payloads; display-name
validation and storage for UC-001/UC-002; assignment by membership UUID with
existing eligibility/conflict behavior; tenant isolation; malformed or
oversized date ranges and cursors; stable ordering and pagination boundaries;
additive migration parity; and legacy rows remaining unlinked unless covered
by an explicitly reviewed mapping. Rerun the complete regression suite against
the dedicated isolated PostgreSQL database. Record observed RED, GREEN, and
REFACTOR results; do not treat the reported 261-test baseline as a fresh run.

These are approved API/schema designs only. Application code, migrations,
tests, and browser pages remain for separate TDD milestones. No cross-origin
credentialed API or client-controlled identity is permitted.

### Accessibility and responsive acceptance criteria

- Use landmarks, logical headings, descriptive page titles, visible labels,
  semantic buttons/links, and programmatic descriptions for every form field.
- All interactions work by keyboard with a logical focus order and a clearly
  visible focus indicator; dialogs trap/restore focus and close accessibly.
- Meet WCAG 2.2 AA contrast for normal and large text in both themes; status
  uses text/icon as well as color.
- Announce async loading, success, validation, and error feedback using
  appropriate live regions without moving focus unexpectedly. Associate
  field errors with their inputs and summarize submission errors.
- Support 200% zoom/reflow without loss of task content or horizontal
  page-scrolling (except intentional, keyboard-accessible schedule scrolling).
- Support reduced motion, browser text enlargement, screen readers, and
  coarse-pointer targets of at least 44 by 44 CSS px.
- Date/time controls must have explicit labels, expose the chosen zone, support
  keyboard entry, and provide a textual agenda alternative to the calendar.

### Incremental implementation and E2E plan

Use RED → GREEN → REFACTOR for each vertical slice; do not start implementation
until this ADR and its required API contracts are approved.

1. Add failing browser checks for the shared responsive shell, error envelope,
   dark/light appearance, keyboard navigation, and session/CSRF behavior; add
   only the minimum FastAPI templates, static assets, and composition needed.
2. Add failing account and company onboarding journeys; implement
   registration/verification/login/recovery screens, create-company code
   display/copy, join flow, and company-context read contract.
3. Add failing employee journeys; implement the employee dashboard,
   availability create/update, personal shift agenda, and safe empty/error
   states.
4. Add failing administrator journeys; implement availability review, roster,
   shift creation, assignment, weekly agenda, and server-authoritative conflict
   feedback after the approved roster API exists.
5. Refactor shared components and verify the full backend regression suite,
   WCAG checks, responsive behavior, CSRF/tenant protections, and E2E flows.

Use Playwright against the FastAPI application and an isolated PostgreSQL
database, with deterministic email capture and synthetic fixtures. E2E
scenarios must cover:

- Account registration, generic verification confirmation, login, reload,
  password recovery, and logout; assert HttpOnly/Secure cookie properties,
  CSRF/Origin rejection, and no account-enumeration signal.
- Administrator creates a company and copies its code; a separate employee
  account joins; both see only their authorized company context.
- Employee creates and updates availability, sees assigned shifts only, and
  receives clear invalid/overlap errors.
- Administrator reads only its company's availability, creates an unassigned
  shift, assigns an eligible employee, and sees the assigned state.
- Reject unavailable employees, overlapping assignments, wrong-role actions,
  unauthenticated actions, and cross-company reads; assert safe messages do
  not reveal other-tenant information.
- Exercise keyboard-only navigation, screen-reader labels/status, 200% zoom,
  narrow mobile layout, tablet/desktop layouts, both color schemes, and
  reduced-motion preference.

## Consequences

- The application stays deployable as one FastAPI service, and its cookie,
  Origin, CSRF, and tenant boundaries remain same-origin and server-enforced.
- The small server-rendered approach avoids a separate frontend runtime but
  still requires JavaScript for calls to the existing JSON API and schedule
  interactions. Screen structure and read-only content remain semantic HTML.
- The new API contracts require additive migration and application/interface
  work before the complete browser journeys can be implemented. Existing
  schedule and availability business rules remain unchanged.
- Per-company display names are personal data; collect only the chosen label,
  expose it only to authorized members of that company, and use synthetic
  names until applicable privacy requirements are confirmed.
- No browser UI, endpoint, migration, test, or deployment is implemented by
  this design decision.

## References

- [Apple Human Interface Guidelines](https://developer.apple.com/design/human-interface-guidelines/)
- [Apple HIG: Accessibility](https://developer.apple.com/design/human-interface-guidelines/accessibility)
- [Apple HIG: Layout](https://developer.apple.com/design/human-interface-guidelines/layout)
- [Apple HIG: Color](https://developer.apple.com/design/human-interface-guidelines/color)
- [Apple HIG: Navigation and search](https://developer.apple.com/design/human-interface-guidelines/navigation-and-search)
- [SwiftUI](https://developer.apple.com/swiftui/)
- [ADR-002 — Tenant Isolation](ADR-002-tenant-isolation.md)
- [ADR-005 — Authentication and Public Deployment Boundary](ADR-005-authentication-and-public-deployment-boundary.md)
- [ADR-006 — MVP Runtime and Deployment](ADR-006-mvp-runtime-and-deployment.md)
