# Kenkui: Neon database and API migration

Date: November 2, 2026
Status: user decisions settled: Neon database/API and Managed Better Auth with Google/GitHub; one-time sign-in accepted; $5/month Neon spending-alert threshold; Apple sign-in deferred. The earlier independent builder review covered the database/API design only. Implementation plan is in ../plans/2026-11-02-neon-migration.md. No production changes made by this chat.

## Auth scope amendment — Google and GitHub selected

The user selected replacing WorkOS with Neon Managed Better Auth using Google and GitHub sign-in. Apple sign-in is deferred. This amendment supersedes the original WorkOS-retention design. It does not authorize an immediate production auth cutover before staging validation and account-linking acceptance.

- Preserve Kenkui internal account IDs and all ownership/credit/payment references. Add a provider-qualified identity mapping so a verified Neon identity links to the existing internal account. Do not merge on an unverified email or create replacement accounts that strand existing balances.
- User-facing login methods: Google and GitHub. Do not add email/password, email-code sign-in, or Apple sign-in without a further product decision. Candidate sender `team@kenkui.fm` remains available for verification, account-migration assistance, and existing notifications; using iCloud mail does not enable Sign in with Apple. Inventory existing login methods to preserve access during transition. The connected WorkOS project reports zero production users, unlike existing Kenkui account evidence; reconcile project/client ID with deployed configuration before using an export. Do not assume zero users need migration.
- Neon supports browser authentication and OAuth-provider authorization-code/PKCE flows for native clients. Verify the exact existing native redirect, token refresh and logout behavior in staging; this is not a drop-in replacement for WorkOS sealed sessions.
- Login-provider decision is settled: Google and GitHub. User accepts one-time verified sign-in and selected a $5/month Neon spending-alert threshold, interpreted as combined staging/production database/API charges. Native notifications operate at organization scope, send to organization admins at 80% ($4) and 100% ($5), and check every 15 minutes. They require Launch or Scale; existing projects remain Free until the reviewed billing step. If the organization contains unrelated projects, disclose the broader scope rather than silently claiming a Kenkui-only alert. An alert is not a hard spending cap, automatic suspension, or authorization for arbitrary paid-plan changes. No alert has been configured yet.
- Prepare exact Neon callback URLs for both environments before asking for credential setup. Use an owned Google Web application OAuth client and an owned GitHub OAuth app; keep production/staging credentials separate as an implementation choice. Store Client IDs/Secrets in protected Neon settings. Request only identity/email permissions, never repository permissions. Verify GitHub users with private email and explicit provider linking so Google/GitHub logins do not produce duplicate Kenkui accounts. Do not automatically map by GitHub username or an unverified email.
- Candidate production sender is the user's existing `team@kenkui.fm` through Apple/iCloud. Existing mailer code already supports iCloud SMTP. Neon accepts custom SMTP, but actual sender authorization and delivery remain untested. Use the owning iCloud account's SMTP username and an app-specific password through protected provider settings; never request the primary Apple password or paste secrets into this document/chat.
- Apple-specific human step, if a usable authorized SMTP secret is unavailable: the user generates an app-specific password in their Apple account and enters it into Neon settings. Preserve the existing sender/inbox. Apple documents 1,000 messages and 1,000 recipients daily per account and describes iCloud Mail as primarily personal use; treat a transactional sender as a later option if delivery/volume requires it.
- Conditional human access steps: account sign-in/2FA, correct WorkOS project access if the connection is wrong, scoped Cloudflare DNS/Worker permissions if existing access remains invalid, and provider OAuth app credentials only if social login is selected. Reuse existing access whenever possible.
- Final human acceptance: receive a login code, exercise the real browser/native login, verify existing books/credits, select a short production maintenance window, and approve named database deletion only after tested backup/rollback evidence. No unsolicited migration emails or live purchases are authorized.
- Keep WorkOS temporarily available for verified linking and rollback; do not decommission it before Neon login and account mapping are verified. Auth should have its own staging acceptance gate and restore/rollback strategy, including newly created Neon-only accounts after cutover.

References checked November 2, 2026: https://neon.com/docs/auth/overview ; https://neon.com/docs/auth/guides/plugins/oauth-provider ; https://neon.com/docs/auth/production-checklist ; https://support.apple.com/en-us/102525 ; https://support.apple.com/en-us/102654 ; https://support.apple.com/en-ca/102198 .

## Outcome and scope

Move production and staging Postgres databases and the hosted public API off Render and onto Neon. Optimize for low idle cost and demand-driven scaling. Preserve customer accounts, balances, purchases, source files, jobs, and downloadable books. Keep the current customer pricing unchanged during migration; measure hosting overhead so a later pricing decision has evidence.

Retain Modal for Python book inspection, cover transformation, synthesis, assembly, recovery, and scheduled maintenance. Retain Cloudflare R2 for existing private objects, Stripe for payments, and the existing frontend host. Replace WorkOS with Neon Managed Better Auth using Google/GitHub as specified in the amendment. Keep WorkOS temporarily for verified migration and rollback. Do not migrate files to Neon Object Storage or synthesis to Neon Functions as part of this work.

Neon Functions only runs JS/TS on Node.js 24. Implement the hosted API in TypeScript using Hono, node-postgres, the Neon Auth SDK, the Stripe Node SDK, and the S3-compatible R2 client. Limit WorkOS integration to transitional account verification/rollback. Avoid an ORM/schema rewrite beyond additive provider identity mapping. Python local-server and library usage remain supported. Shared database semantics must work with the existing Python workers.

## Evidence and starting point

- Read-only inspection of the separate “Investigate Render bill for Kenkui” chat found its database-only migration paused on DNS access. That chat is idle. This design changes its final API target from Render Free to Neon Functions; do not resume its Render API cutover independently.
- The migration checkout is `/Users/dizzler/Documents/Codex/2026-11-02/i-m-seeing-a-bill-from/kenkui-server`, branch `cost/idle-neon-hosting`, observed HEAD `2795daf838aac4e04cea28998e741d972f8c2972`.
- That checkout contains idle-aware Python pooling, external recovery signals, daily maintenance, and database-overlay support. Reuse reviewed behavior; do not reimplement worker reliability in TypeScript.
- Prior evidence identifies deployed server `22c6eadcc180fd916b2f121e46c602c39a5b6254`, core `97cba6bdb663c15d2d90d6e4e017be23ecdb9c7c`, and Studio `468e067d02837e3a3ad79f5235c8b8111afc8e37`. Recheck actual deployment revisions before implementation and cutover; these are not asserted to remain current.
- Existing Neon projects verified through the connector: staging `divine-boat-06396016` and production `mute-unit-91304892`, both Postgres 17 in `aws-us-east-1`, currently Free. This region supports Functions. Reuse these projects after checking their current contents. Do not create duplicate projects.
- Earlier migration evidence records a successful staging baseline restore and checksum comparison; production restore/cutover is not complete. Do not confuse that rehearsal with current authoritative production data.
- Existing OpenAPI snapshot contains 16 resource paths. Browser/native authentication routes and the Stripe webhook must be inventoried separately; OpenAPI path count is not complete feature coverage.
- Original hosting evidence: two API instances cost $50/month and two databases cost $86/month. Prior production database size was approximately 12 MB; files are in R2. These are historical observations, not fresh invoice verification.

## Architecture

Browser/native clients -> public TypeScript API on Neon Functions -> Neon Postgres.

Neon API -> authenticated, bounded Modal operations -> existing Python pipeline and private R2 objects.

Modal workers -> the same environment's Neon Postgres and R2. They execute durable work independently of a Neon HTTP invocation and continue if a browser disconnects or a Function is evicted.

Use separate staging and production projects, credentials, worker deployments, recovery prefixes, payment configuration, and bucket configuration. Do not point staging to production data or live payment events. Keep `api.kenkui.fm` and `api.staging.kenkui.fm` as the consumer-facing API names.

All mutable correctness state belongs in Postgres or existing durable storage. Module-level pools and short-lived read caches may improve performance but cannot own balances, locks, leases, quotas, or job state across isolates.

## API parity and ownership

Create a route-by-route compatibility matrix before implementation. Capture request/response shapes, status codes, errors, ownership behavior, headers, cookies, pagination/order, and stream semantics from the agreed baseline. Pin fixtures and baseline source SHAs. Include browser and native clients; compare invalid and unauthorized requests, not just happy paths.

| Surface | Target and requirements |
| --- | --- |
| Health, capabilities, voices | Neon handler; static manifests where possible; basic health must not connect to Postgres. Preserve enabled voices/models and schema version. |
| Browser auth | Neon Managed Better Auth with Google/GitHub; preserve state/PKCE protections, allowed return URLs, protected-route authorization and stable internal user IDs. Existing WorkOS sessions are not Neon sessions. Validate transitional linking or the reviewed one-time re-login without changing credits/ownership. |
| Native auth | Preserve config, authorize, exchange, refresh, logout, redirect URI constraints, token verification, issuer/audience, and errors. |
| Asset upload | Preserve supported MIME types, limits, hashes, ownership, immutable source semantics, and existing R2 keys/salts. Do not widen upload formats accidentally. Audit pending PDF work before choosing the baseline. |
| Inspection and covers | Run the existing Python behavior on Modal through narrow authenticated internal endpoints. Read private sources from R2; return bounded metadata or object references. Do not install Python or shell to an undeclared Render proxy from Neon. |
| Preflight | Derive billable speech counts server-side using the same pipeline/options as execution. Never trust client counts or substitute raw file character counts. Cache only immutable, versioned inspection results keyed by content and semantic options. |
| Job admission/cancellation | Preserve atomic credit reservation, dispatch idempotency, active-job concurrency limits, optimistic versions, lease fencing, and cancellation/settlement races. Port current SQL/transaction behavior, including integer/decimal precision and JSON encodings. |
| Billing | Preserve WorkOS user mapping, credit lots, reservation/release/finalization, expiry/order, and ledger history. Preserve Stripe prices/customer references, raw-body signature verification and replay deduplication. No live purchases for tests. |
| Progress and reconnect | Preserve ordered durable event IDs, Last-Event-ID resume, snapshots, terminal state, and ownership checks; use bounded querying described below. |
| Artifact/cover downloads | Preserve authorization, filenames, MIME, range/cache behavior where supported, and private-object access. Existing clients must continue working; add direct signed transfers only after client compatibility is proven. |
| Notifications | Preserve preference updates, recipient selection, exactly-once claim semantics, and existing worker email behavior. |

Do not mount the browser-facing Data API or expose database credentials. Keep authorization in API handlers and enforce ownership on every resource operation.

## Python bridge and durable dispatch

The existing API directly imports Python for source inspection, cover modification, and preflight. It also uses Modal's Python SDK to spawn jobs. These are explicit migration tasks, not ordinary handler translation.

Add narrow internal Modal endpoints for inspection/preflight/cover operations and dispatch submission. Use Modal proxy authentication plus server-held credentials, explicit environment selection, strict bounded request schemas, and environment-specific secrets. Confirm current documented support before shipping the bridge. Verify credentials before expensive parsing or database access. Do not accept arbitrary Python function names, URLs, filesystem paths, code, or object-store prefixes from callers.

The TypeScript API authenticates the user and validates ownership. Modal independently limits operations to the named environment and existing server-owned source/dispatch records. The dispatch endpoint receives a committed dispatch ID, claims execution under existing database concurrency and lease rules, and invokes the existing Python worker. Repeated delivery must not create overlapping active attempts. An ambiguous spawn retains its lease until expiry, matching current behavior.

Keep durable admission and recovery ordering: establish a per-job recovery intent outside Postgres before admission can become stranded, atomically create the job/dispatch/credit reservation, and perform dispatch after commit. Uncommitted intents may become harmless stale signals; committed jobs must never lack recovery coverage. Use existing reviewed signal logic and daily reconciliation. Do not depend on `waitUntil`, in-memory promises, or HTTP response completion for guaranteed dispatch.

Inspection/preflight can initially use bounded request/response calls to Modal to preserve the public contract. Set explicit deadlines and cancellation behavior; a client retry must not duplicate a cover asset or a paid admission. If representative documents exceed those limits, resolve the API/client contract before cutover rather than silently returning a different response shape. Preserve new PDF support if it has reached the selected deployment baseline.

## Idle cost and dynamic scaling

1. Connect lazily. Do not initialize/query Postgres on health checks, static catalogs, anonymous preflights, or every module import. Use a small shared pool per isolate (initial call: max 3–5), short idle closure, TLS verification, attachDatabasePool as documented, and no new transaction held across Modal/R2/WorkOS calls. Preserve the existing asset/artifact/checkpoint retention row-lock critical sections across R2 deletion because they prevent admission/deletion races; their cost is bounded scheduled lock/connection time. New admission, bridge, upload-reaper and backup orchestration stays outside transactions during external work. Bound worker pools too.
2. Reuse idle recovery. Minute recovery may inspect external signals but must not connect to Postgres when no work is indicated. Batch reconciliation, retention, and verified logical backups into a daily wake. With Managed Auth, full dumps include private signing/session/account data and private release evidence: encrypt the complete compressed archive using vetted age CLI to an offline public X25519 recipient before uploading to shared R2. Preserve auth schemas. Keep the private identity offline and out of API/Modal/Neon; keep distinct full-read DB backup authority and public recipient only on daily maintenance via its own overlay. Runtime overlays contain only restricted DATABASE_URL. Validate both backup configuration and actual recipient before daily DB/retention; fail closed on dump/encryption errors and retain seven encrypted daily archives. Audit old plaintext backups and inherited backup credentials during Task 13; never auto-delete old evidence. During actual jobs retain necessary lease renewal and recovery guarantees.
3. Replace the present stream loop's approximately 50 ms database polling. First implementation: query new events by `job_id` plus `sequence > cursor`, with a bounded batch and an initial 1-second interval. Back off to at most 5 seconds when no events arrive; refresh immediately for local terminal detection and close on terminal/disconnect. Maintain resume semantics. Do not keep a dedicated LISTEN connection merely to avoid polling; evaluate measured latency/load before adopting a separate event system.
4. Fix the Studio five-second polling fallback to stop at completed/failed/cancelled states. Pause hidden-tab refreshes and reconnect once on return. Existing payment-confirmation polling already stops after one minute; preserve or tighten that bounded behavior. Do not introduce periodic balance refreshes in idle tabs.
5. Keep progress writes meaningful and bounded, but preserve cancellation responsiveness and every correctness transition. Do not weaken checkpoints or increase automatic retries to save database writes.
6. Retain R2 for binary content and use short-lived signed transfers where the current clients support them. Cache immutable catalogs/inspection results; never use cached balances as admission authority.
7. Start staging on existing Free allowances. Proposed production configuration after validation: Launch usage billing with 0.25 CU minimum, scale-to-zero enabled, and initially 1 CU maximum, raised only if representative workload evidence requires it. This is a proposed setting, not an activated paid subscription. Free quotas do not become paid allowances. Enabling billing is part of the reviewed rollout, with current account capability confirmed first.
8. Measure database CU-hours, function active/waiting capacity, invocation/transfer volume, query counts, completed-book counts and idle periods. Read consumption through provider control-plane tools rather than waking the database just to monitor it. Alert on anomalous usage and use authenticated admission limits to bound costly work. Provider autoscaling maxima limit instantaneous capacity, not total monthly spend.

Initial success target: with no users/jobs, no recurring database requests except the single daily maintenance window; both API and database can idle. With a representative book, record total provider usage and peak capacity rather than promising a fixed cost per book without evidence. Production overhead per completed book is total monthly hosting divided by completed books; there is no meaningful per-book allocation in a month with zero completions.

## Public ingress and domains

Inventory current DNS proxying, OAuth callbacks, Stripe webhook URLs, CORS origins, native endpoints, and cookie domains before changing routing. Both the Neon native URL and any custom domain remain reachable and require correct handler authentication.

Preferred cost-protection choice for public production traffic: reuse Cloudflare DNS with a minimal Worker at the existing API hostname to enforce edge request limits and forward to the native Neon Function URL with a server-only origin secret. The public API logic and Postgres live on Neon; the Worker is only ingress. The Worker owns its hostname; do not register that same hostname as a Neon custom domain or orange-cloud a Neon-registered custom-domain CNAME. This is a proposed operational choice, not a user requirement. It adds a small separately metered component and must be shown explicitly in the final operating cost model.

Preserve browser/native bearer/cookie authentication, OAuth redirects, CORS preflight/errors, raw Stripe bodies, stream reconnect and download behavior through that hop. Verify streaming and upload limits on the actual edge configuration. Do not log/forward the origin secret through redirects. Reject missing/invalid origin credentials on the native Function before database work. Rejected native requests still consume Function invocation resources; this is not a hard monthly cost cap.

If a Cloudflare Worker cannot be deployed under existing account permissions, keep staging on the authenticated native Function URL while access is resolved. Do not silently switch production to unreviewed routing. Current direct custom-domain alternative is DNS-only CNAME with Neon TLS; choose it only if the reviewed ingress design changes.

Earlier DNS token validation failed. Do not search unrelated secret backups or print any credentials. Recheck the specifically authorized token file privately at cutover; if still invalid, report the concrete access requirement. Do not request replacement credentials before the rest of the migration is ready for that step.

## Database migration, release and rollback

Use additive, backward-compatible schema changes only when a concrete port requirement demands one. Existing Python migration tooling is the sole schema authority during this migration; do not run migrations from request handlers or introduce competing migration histories. Apply versioned migrations as an explicit release step with locking.

1. Freeze an agreed baseline across core, server, Studio and relevant pending changes. Put implementation in isolated branches/workspaces and keep the old migration checkout intact. Confirm no other chat is cutting over the same resources; obtain explicit user authorization before messaging that chat.
2. Capture API contract fixtures and database invariants; implement and validate the TypeScript API and Modal bridge locally against disposable Postgres and synthetic data.
3. Restore and verify the current staging target and make the new API/clients ready before one coordinated transfer of the existing staging website/API and all worker consumers. Freeze/drain/pause old staging writers/schedules; never repoint an existing worker to Neon while the old staging API still writes Render. Reuse staging resources and unchanged write-only worker/mail secrets plus separate runtime/maintenance overlays; no permanent preview environment or paid service. Task 11 is code/local verification; Task 13 owns actual transfer, interrupted canary, Neon suspend/reconnect, overnight idle and encrypted independent backup restore. Deploy to existing Neon staging, with no production writes routed there. Rehearse a current staging export/restore into an empty approved target and verify schema, row counts/checksums, credits, payment deduplication and R2 references. Use backups privately; never commit dumps or secrets.
4. Test real staging login, existing-session behavior, native auth, upload/inspection/cover/preflight, job success/cancel, interruption/recovery, progress reconnect, download and signed Stripe test-event replay. Exercise concurrent admissions and duplicate webhooks from both implementations against equivalent synthetic databases, without duplicating live payment side effects.
5. Verify cold wake and scale-to-zero through control-plane state over at least one real idle interval, plus an overnight observation. Capture function termination/reconnect and 429 behavior. Load-test realistic overlapping jobs/viewers and compare measured usage to the old implementation.
6. Before production cutover, prepare a reviewable report with measured behavior/costs, release SHAs, proposed billing/compute settings, DNS changes, current job state, independent restore evidence, and exact rollback actions. Do not declare migration done at a staging deploy.
7. Cut over production in a controlled write pause: stop new admission and relevant schedules, allow active work to finish, take a final consistent export, restore and verify the Neon target, configure the API and Modal worker database overlays together, validate TLS/ownership/credits/Stripe, then route existing API hostnames and reopen admission. Keep a single authoritative writable database and webhook consumer throughout. A short planned maintenance window is the implementation default, since no zero-downtime requirement was given.
8. Retain the prior deployment and independent backup for rollback. Before new writes reopen, rollback may point both API/workers to the original database. After writes reopen or Neon-only identities exist, never roll back to an old snapshot or WorkOS-only image: deploy the rehearsed same-TypeScript/Neon-auth standalone fallback against the current Neon database and identical provider mapping, or reconcile every write and retain every Neon identity before changing databases. Preserve every credit/payment record. Task 12 builds/rehearses locally; Task 13 owns real ingress/cold/auth/upload/SSE/fallback target acceptance after consistent staging restore, restricted runtime roles and coordinated writer freeze/readiness.
9. Suspend retired paid API instances after successful cutover. Verify actual residual Render database/storage billing; do not assume suspension eliminates storage charges. Permanently deleting named old databases requires final explicit approval after independent restore and acceptance evidence. Report any ongoing charges until retirement is verified.

## Acceptance and non-negotiable invariants

- Existing user identity mapping and balances are preserved; the user accepted one-time re-login. Show a clear sign-in-again message; do not attempt to preserve WorkOS sessions as Neon sessions.
- Credit reservation and dispatch admission are atomic. Concurrency cannot overdraw a balance; duplicate payments/jobs do not duplicate credits or executions. Settlement happens once; stale workers cannot commit checkpoints or artifacts.
- Production and staging are isolated; all resource routes enforce verified ownership. Native Function and internal Modal endpoints reject unauthorized work before it can trigger expensive processing.
- All agreed baseline client operations work against Neon without hidden dependencies on Render.
- Python workers recover correctly from interruption while Neon/API clients sleep or disconnect. Checkpoint retention does not delete active-job data.
- Public progress updates are meaningfully responsive without the old 20-query-cycles/second/viewer loop; terminal views stop polling.
- Cold-start and overnight idle behavior are measured, including any expected first-request latency, with no keep-alive pings.
- A consistent backup has been restored independently and compared. Rollback has a tested, explicit path that preserves post-cutover writes.
- Operating cost report separates Neon database, Neon Functions, ingress, Modal, R2, and any remaining Render charges. No customer price changes are bundled into the migration.

## Builder discretion

Choose TypeScript package layout, test runner, fixture structure, indexed query implementation, and CI wiring to fit the repositories. Pin runtime/dependencies; implement the accepted one-time sign-in and verify Modal endpoint auth in early contract tests. Tune bounded polling/pool sizes from staging measurements. Do not ask the user to choose libraries or routine SQL details. Escalate only findings that change supported product behavior, account billing, data retention, or approved migration scope.

## References checked November 2, 2026

- Neon Functions and supported regions/runtime: https://neon.com/docs/compute/functions/overview
- Lifecycle, concurrency, connection pools and waitUntil: https://neon.com/docs/compute/functions/reference/runtime-limits
- Neon deploy/config policy: https://neon.com/docs/compute/functions/deploy
- Neon custom-domain behavior: https://neon.com/docs/compute/functions/custom-domains
- Current Neon pricing: https://neon.com/pricing
- Modal HTTP bridge and Python function invocation: https://modal.com/docs/guide/webhooks
- Modal authenticated endpoints: https://modal.com/docs/guide/webhook-proxy-auth
- Existing code/OpenAPI and the idle-hosting progress ledger in the source checkout above.
