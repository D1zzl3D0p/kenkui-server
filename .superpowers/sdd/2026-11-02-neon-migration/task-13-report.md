# Task 13 implementation report — code and synthetic rehearsal only

Status: DONE for the delegated implementation phase. **Operational acceptance is pending**, with no live gate checked by this report. Controller owns real source/target/provider/data/billing actions. No progress ledger was edited.

Base: `0db3ef389d6464245f562376cae5be2643482d71`, branch `migration/neon-full`. First scoped commit: `611f149` — `fix: preserve selected beta credits across hosted releases`. The following scoped rehearsal/runbook commit contains this report; its exact HEAD is returned to the controller after commit.

## Implemented

- `scripts/neon/inventory.mjs`: Node24 operator wrapper around the existing pinned `.venv` Python/psycopg implementation, using private stdin for database material, no startup PG GUCs or inherited PG variables, explicit SQL READ COMMITTED/READ ONLY plus bounded lock/statement timeouts after connection. Pins actual host/database, requires verified TLS outside the authorized synthetic55438 endpoint, discovers all nonsystem user tables (including private and managed-auth), checks schema/table/sequence access and unfiltered RLS, locks before capture and rejects catalog drift. Records migration IDs, complete row counts/hashes, column/default/constraint/index/RLS policy schema signatures, sequences, accounts/all-lot availability, signed net ledger/opening residual diagnostics, owner/FK/payment/allocation invariants and R2 references. Reuses existing `R2AssetStore._key` including actual checkpoint ids and retained source tombstones. Does not call R2 or use provider credentials.
- Calls the reviewed `migration_fixture_quarantine.restored_snapshot_digest` directly, retaining its exact existing locked inventory and digest domain. NULL before its whole fixed application inventory exists. The separate full inventory digest is not a quarantine manifest, classification fingerprint, source export hash or archive hash. No changes to Task9a/9b/9c importer/eligibility/accounting implementations.
- `restore-verify.mjs`: no destructive restore automation; strictly refuses an occupied target, performs exact source-copy comparisons before DDL, and separately requires an exact known migration delta plus independent full-target digest review and original-column row preservation after DDL. Rechecks the captured manifest digest, including projection integrity. Explicitly reviewed legacy-no-auth→managed-auth addition has its own transition. Original financial rows/sequences/invariants and R2 references cannot be excused by the DDL review. Diagnostic opening residual is not equated to legacy credited/current amount and is not a global balance-equals-ledger requirement.
- Private manifest files are exclusive mode0600 outside Git; stdout contains only counts/status/digests and failures are fixed/sanitized. Successful CLI and failing CLI tested. No user/credential creation or live restore/deploy/provider actions in CLI imports/defaults.
- `billing-alert.mjs`: injectable helper exposes `setSpendingAlert(orgId,cents)` with exact reviewed organization, already-paid Launch/Scale and admin preconditions; documented GET preflight, one PUT with `spending_limit_cents`, independent GET readback; redirects forbidden, bounded requests, no automatic retries or plan activation. CLI only accepts explicit selected `apply-reviewed-500`. Calculation requires all explicit usage inputs and reports a subtotal, not unknown-as-zero usage or a cap.
- Shared Function/fallback allowlist carries `KENKUI_BETA_CREDITS`. New real-PG test calls both actual deployment renderers, then real `BillingStore` SQL grants/replays and quote/catalog consumers with beta25/price333; the hosted catalog remains all selected VCTK voices, not the benchmark-only `KENKUI_VOICE_IDS`. No public API contract or customer pricing change.
- Expanded `deploy/neon/README.md`, new `acceptance.md`, `evidence/README.md`: private capture/restore ordering, canonical importer/v2 boundaries, live-target occupancy, full Auth data/config and backup/role/RLS gates, ordinary runtime `DATABASE_URL_UNPOOLED=''` shadow and daily-only backup overlay last, actual selected staging nonsecret settings and controller-verified Stripe default, concrete pending billing action/rates, coordinated transfer/native/worker/idle/DNS acceptance and evidence custody. All operational checkboxes remain open.

## TDD evidence

All commands below were prefixed with `rtk`; Node24.21.0, libpq18.6, the existing `.venv`, and only disposable synthetic databases on127.0.0.1:55438 were used. Initial restricted execution returned `EPERM` on local TCP before testing behavior; the same authorized synthetic commands were rerun with local access. No private cluster/data was inspected.

RED — required runtime parity bug, before implementation (from `hosted-api`):

```
rtk proxy env KENKUI_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55438/postgres /Users/dizzler/.local/share/mise/installs/node/24.21.0/bin/node node_modules/vitest/vitest.mjs run test/release-grants.integration.test.ts
```

Expected failure: `expected undefined to be '25'` at `selected.KENKUI_BETA_CREDITS`. The shared deploy-time renderer omitted the explicitly selected nondefault allowance. After the one-key allowlist fix: same command, **1/1 passed**, actual SQL allowance25 once per owner and quote666/999 for synthetic price333 through both Function/fallback renderers.

RED — new tooling missing (repository root):

```
rtk proxy /Users/dizzler/.local/share/mise/installs/node/24.21.0/bin/node --test scripts/neon/test/billing-alert.test.mjs scripts/neon/test/restore-verify.test.mjs
```

After correcting an initial test syntax typo, the command failed as expected with `ERR_MODULE_NOT_FOUND` for the not-yet-created billing/inventory helpers. Implemented the helpers after this failure. Initial collector SQL quoting/catalog-query setup errors were corrected before claiming GREEN; those setup errors were not treated as behavioral proof.

Additional observed RED→GREEN cycles used the same restore test command with `KENKUI_TEST_DATABASE_URL` set:

- Retained checkpoint: independent fixed synthetic object key supplied for checkpoint **id**; initial chapter-key derivation failed `R2 reference invariant failed`; changed capture to actual object id, then passed.
- Explicit new managed-auth scope transition: initial comparison failed `Managed auth scope mismatch` even with a separate reviewed addition; added the explicit reviewed transition while requiring real target auth inventory, then passed. Missing required Auth still rejects.
- Edited full-target manifest: changing a newly added table count initially produced `Missing expected exception`; added canonical full-digest recomputation and bound original-column projection, then passed.
- Changed RLS policy: initial exact-copy comparison produced `Missing expected rejection`; added RLS flags/policies to schema signature, then passed.
- Valid retained source tombstone: initial tracking check incorrectly reported orphan1; changed referential check to all existing source records while live-object presence remains required, then passed. Actual nonexistent resource/key still rejects.

## Final verification

From `hosted-api`:

```
rtk proxy env PATH=/Users/dizzler/.local/share/mise/installs/node/24.21.0/bin:/opt/homebrew/bin:/usr/bin:/bin KENKUI_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55438/postgres npm test
```

Result: **30 files,238 tests passed**, no skips/failures. Full hosted suite includes the actual renderer/grant regression. `rtk proxy env PATH=/Users/dizzler/.local/share/mise/installs/node/24.21.0/bin:/opt/homebrew/bin:/usr/bin:/bin npm run typecheck` and the same prefix with `npm run build`: **exit0**, no errors.

From repository root after building exact local artifacts and the retained-tombstone fix:

```
rtk proxy env PATH=/Users/dizzler/.local/share/mise/installs/node/24.21.0/bin:/opt/homebrew/bin:/usr/bin:/bin KENKUI_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55438/postgres node --test scripts/neon/test/*.test.mjs
```

Result: **36 tests passed**,0 failed/skipped. Existing archive/release/fallback/ingress contract tests remain green. After the final explicit SQL read-only/read-committed transaction configuration tightening:

```
rtk proxy env KENKUI_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55438/postgres /Users/dizzler/.local/share/mise/installs/node/24.21.0/bin/node --test scripts/neon/test/restore-verify.test.mjs scripts/neon/test/billing-alert.test.mjs
```

Result: **16 tests passed**,0 failed/skipped. Synthetic PostgreSQL confirmed SHARE locks are supported in a READ ONLY transaction. Reused reviewed digest agrees exactly with the independent existing Python function; the generic fixture has available4/net-3/residual7 without inventing a ledger seed. Full independent synthetic `pg_dump`→empty database→`pg_restore` comparison included private release and managed-auth rows. Actual SQL mutations covered missing row, changed credit, duplicate payment, orphan owner and R2 reference, RLS/policy/private-read failure, wrong environment/host, occupied target preservation, explicit DDL/projection/digest attestation, missing managed Auth, safe CLI output and private-file/Git refusal.

`rtk git diff --check` and staged `--check`: clean. Full Python/Studio/ingress suites were not repeated because their implementation was unchanged; the new real-PG Node tests execute the Python collector and canonical digest directly, and the full hosted/release-script suites cover the changed JS/TS surfaces. No unrelated worker or provider tests were run.

## Sources and operational pending gates

Primary billing API/rate docs read2026-11-03: [PUT spending limit](https://neon.com/docs/reference/api/organizations/set-organization-spending-limit), [GET spending limit](https://neon.com/docs/reference/api/organizations/get-organization-spending-limit), [notification behavior](https://neon.com/docs/introduction/spending-notifications), [pricing](https://neon.com/pricing), plus controller's public billing arithmetic review. Current organization remains Free and500cents unconfigured. Helper tests used injected synthetic fetch; no live writes or paid activation. Admin emails at$4/$5,15-minute checks, org-wide scope and no auto-suspend are explicit. Actual usage unknown.

Controller-supplied public selected runtime inventory: staging beta1000/price126/maxspeech10000000/worker-maxjobs2/model`openrouter/deepseek/deepseek-v4-flash`/voice-set`vctk`/benchmark-voice`eponine`. Staging Stripe default controller-read `2026-07-29.dahlia`; production independent capture remains pending. No provider secret/source files were read by implementer.

Live staging target is occupied: exact provenance/reconciliation or an independently approved empty target is a prerequisite. Root's earlier encrypted source backup/offline local restore is preparation, not final frozen equality, actual Neon restore, full managed Auth backup/config restoration or owner-independent key custody. All remain pending. Also pending: SQL-created actual runtime/backup roles and effective grants/RLS/full dump; private exhaustive source-plus-approved-v2 coverage and final freeze/activation; ordinary-OTP enforcement; owner-requested sender delivery and browser/native Google/GitHub/link/refresh/logout; real ingress/DNS/native/cold/SSE/upload/download/fallback acceptance; coordinated existing API/website/Modal transfer with single writable authority; interrupted canary/fencing/lease/settlement and actual suspension/overnight control-plane observation; protected deployment settings, live billing/usage readbacks and production report. Never repoint workers while old API writes Render. DNS action remains exact owner CNAME release, not broad key issuance. Do not treat code completion as any of these gates passing.

## Self-review

Reviewed the full new scripts/tests, scoped runtime diff and runbooks against the brief/canonical Task13 constraints. Corrected checkpoint id derivation, explicit Auth addition handling, full-manifest integrity, RLS schema drift and legitimate retained tombstones using observed failing tests. Original finance data and canonical v1/v2 authority remain unchanged. No unresolved implementation uncertainty; operational limitations are deliberately explicit and pending. No source/profile/email/identity/secret inspection, SMTP, live provider SQL, deploy, paid activation, DNS mutation or push occurred. Only the authorized synthetic local database was used.
