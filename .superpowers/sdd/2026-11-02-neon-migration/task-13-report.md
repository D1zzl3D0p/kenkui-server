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

## Review fix round 1 — supersedes the pre-review code verdict above

Base: `cb92b017ff48958f57d63e058508077a5b9fb937`. Read `task-13-review.md` verbatim: fixed its Critical finding and all seven Important findings together. No private/live invocation of these helpers occurred or was authorized. All operational gates remain pending. The controller approved the exact restore-review/historical-evidence boundary before implementation; no new signing infrastructure or importer exception was introduced.

1. **TLS/endpoint boundary:** moved the collector to auditable `scripts/neon/inventory.py`, invoked only through private stdin. The exact pinned psycopg/libpq parser validates effective host, single port, database and TLS **before connecting**, alongside strict URI/query validation. Duplicate query parameters, host/db/port overrides, service/hostaddr/options, multihost/port ambiguity and non-TLS escape attempts reject offline. Synthetic cleartext is explicitly limited to staging/127.0.0.1:55438/disposable rehearsal database/synthetic project+branch pins. Inherited PG variables stay cleared; read-only/read-committed/timeouts remain SQL settings after connection.
2. **Actual upgrade/sequences:** sequence metadata now includes type, start/increment/min/max/cache/cycle, exact OWNED BY dependency, last value and is-called. Every original sequence must be identical. Only the exact independently reviewed `newSequences` map is allowed after DDL. The real old-schema test applies actual0004 then creates the pre0008 source, independently dumps/restores it, and applies actual0008/0009/0010. The new `migration_rate_events_id_seq` is required in the separate review; omission rejects. Original rows/history are unchanged.
3. **Historical settled allocations:** collector reports exact candidate rows; no candidate automatically qualifies. Mandatory independent historical-pre-0004 source attestation is tied to the ORIGINAL source capture and exact authorization full-row digest, account/job/credits/status/created/finalized timestamps plus canonical legacy opening lot id/reference/created time. Only settled/zero-allocation rows with affirmative created≤finalized<legacy-lot-created chronology can qualify. Partial/reserved/new/changed rows and ambiguous/missing/late chronology remain blocked, including when an operator attestation claims them. No ledger seed, origin inference, lot relabel or accounting mutation. Canonical v1/v2 finance/importer code is untouched.
4. **Catalog completeness:** literal reserved `pg_` prefix exclusion includes legal `pgdata`; identifiers are fully quoted and cannot collide on dots. Emptiness discovers every user relation and user function, including sequence-only/view-only targets. Required views/functions and their definitions/options are captured; unsupported foreign/materialized/composite objects refuse capture rather than silently omit. Private-schema and quoted-name collisions are covered by real PostgreSQL fixtures. R2 key membership now uses a set.
5. **Independent provenance:** comparable `fullDigest` is separate from `captureDigest`, which binds all format/environment/target host+port+database/project/branch/capture-time/mode/managed-auth/projection-source provenance. Format is now `kenkui-restore-inventory-v2`; the prior unbound v1 is rejected. Every verification requires a trusted independent `restore-review-v1` object with original source and expected destination exact pins/capture digests, purpose, approval reference/operator/time. Source-stability requires distinct fresh captures of the same source; restored-copy/migrated-target requires a different physical destination and correct capture mode. No default or self-file restore proof. Migrated review also binds full target digest, exact pending migrations and new sequences. The review is an operator attestation through the existing private authority boundary, **not cryptographic proof of who approved it**; hashes alone confer no approval.
6. **Private output:** owner-only private parent directory and positive outside-checkout/outside-Git-metadata classification are required before exclusive0600 creation. Missing Git, dubious/unknown Git errors, Git metadata and uncertain ownership/permissions fail closed. Tests confirm no file appears for each refusal, with Git metadata parent permissions made private so that test actually reaches the metadata guard.
7. **Billing scope:** exported operation rejects every cents value except500 before fetch. Independent admin review binds selected org/principal/credential SHA256 plus approval/operator/time/admin role; self-asserted role or paid-tier GET read access is insufficient. Provider PUT enforcement and independent readback are still mandatory. Tests include positive non500 values and a paid read-only principal, missing attestation, changed principal and changed credential. No live billing call or activation.
8. **Behavioral schema:** column and default database collations, user triggers/enabled state/referenced function definitions, user function/view definitions and view options participate in content equality. Actual sequence-increment, disabled-trigger, changed-trigger-function and column-collation drift reject; supported private view capture is exercised positively.

Runbooks now specify the mandatory review shape/purposes, provenance digest, explicit new sequence review and narrow historical evidence. They also include BOTH Render staging API writers (primary plus free `kenkui-staging-api-idle` / `srv-dbkgesou01pc73afapc0`, sharing the source DB) and Modal consumers/schedules in the coordinated source freeze. No suspension, transfer, private evidence use, provider SQL or configuration action was performed.

### Round 1 exact RED/GREEN evidence

All commands used `rtk`, Node24.21.0 and only the authorized synthetic PostgreSQL55438. No unrelated Python/Studio/native/full-provider suite was rerun.

Initial offline RED:

```
rtk proxy /Users/dizzler/.local/share/mise/installs/node/24.21.0/bin/node --test scripts/neon/test/inventory-safety.test.mjs scripts/neon/test/billing-alert.test.mjs
```

Result before fixes:3 failed/3 passed. TLS override test: `Missing expected exception`; unavailable-Git private placement: `Missing expected rejection`; non500 billing: fetch count `1 !== 0`. After adding principal-bound cases, standalone billing RED also reported `Missing expected rejection` for paid read access without independent admin review. Following implementation: offline safety2/2 and billing5/5 passed.

New provenance/catalog/schema RED:

```
rtk proxy env KENKUI_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55438/postgres /Users/dizzler/.local/share/mise/installs/node/24.21.0/bin/node --test --test-name-pattern='real disposable' scripts/neon/test/restore-verify.test.mjs
```

Before fixes: self-verification `Missing expected exception`; `pgdata`/quoted-name entries absent; sequence definition drift `Missing expected rejection`; old CLI lacked captureDigest. First old-schema regression failed because historical candidate evidence did not exist. After implementing the evidence boundary, real pre0004→0004→pre0008→actual0008/0009/0010 succeeds, requires independently reviewed new BIGSERIAL sequence, and rejects missing/changed/partial/new/late historical claims.

Extraction formatting initially produced Ruff line-length/style failures. Corrected formatting and a supported-view string-concatenation error detected during self-review; a positive private-view capture regression first failed `Inventory capture blocked...` then passed after repair. No failing formatting/setup output was counted as successful behavior verification.

Final focused GREEN:

```
rtk proxy env KENKUI_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55438/postgres /Users/dizzler/.local/share/mise/installs/node/24.21.0/bin/node --test scripts/neon/test/inventory-upgrade.test.mjs scripts/neon/test/restore-verify.test.mjs scripts/neon/test/inventory-safety.test.mjs scripts/neon/test/billing-alert.test.mjs
```

Result: **23 passed,0 failed,0 skipped**, including independent full synthetic dump/restore and actual migrations, supported view capture, exact established quarantine digest equality, all catalog/provenance/privacy/schema/finance/R2 checks and billing guards. Added explicit independently attested ambiguous/late ORIGINAL-source chronology cases and reran:

```
rtk proxy env KENKUI_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55438/postgres /Users/dizzler/.local/share/mise/installs/node/24.21.0/bin/node --test scripts/neon/test/inventory-upgrade.test.mjs
```

Result: **1 passed,0 failed**, both source chronology claims correctly rejected as unresolved. `rtk proxy .venv/bin/ruff check scripts/neon/inventory.py`: **All checks passed!** `rtk proxy .venv/bin/ruff format --check scripts/neon/inventory.py`: **1 file already formatted**. `rtk git diff --check`: clean.

Self-review read all extracted Python and JS changes, tests, scope and runbooks. Confirmed pre-auth validation, no false empty target, exact original-sequence preservation/new-sequence review, no standalone historical exemption, no provenance self-approval, safe output classification and exact500 enforcement. No actual source data/private files were read and no provider calls were made. Operational limitations stay explicit: independent operator provenance/role/admin review, historical chronology gaps, existing-target provenance, real full Auth/backup/restore and owner custody, coordinated freezes, provider/auth/native/DNS/billing/cold/overnight acceptance remain controller-owned and pending.

## Review fix round 2 — remaining findings and staging boundary

Base: `c0af84e70a50546209ca1a3c76e5e6292f6f4519`. Read `task-13-rereview1.md` verbatim. Addressed only remaining findings4/5/6/8 and the new staging/shared-source runbook issue. Findings1/2/3/7 were not reimplemented. Private/live helper use stayed on hold; no provider actions or actual customer/private evidence were used. No importer or root ledger edits.

- **4 — independent types:** emptiness now discovers independently created user types, including an otherwise fresh enum-only database. Full capture includes ordered enum labels in the content digest. Other independent types are explicitly unsupported and refuse capture; generated array types are identified through their actual parent `typarray`, and table-row types are represented by the table inventory. Tests use actual fresh enum-only databases, identical empty enum-bearing tables followed by enum-label insertion/order drift, and an unsupported domain refusal. Capture metadata must be recaptured with the new `types` field; unbound older inventory bodies cannot pass integrity checking.
- **5 — canonical reviewed identity:** every independently trusted review side now requires `databaseIdentity: {projectId, branchId, database}` consistent with its captured exact pins. Independent restore/migration must differ in that provider identity **and** in the normalized host/port/database tuple. Changing only a trailing-dot alias or another hostname while retaining the same project/branch/database rejects. Two different databases on one server remain valid, demonstrated with fabricated offline evidence and actual source/copy enum databases. Controller approved this boundary: the helper validates the independently reviewed provider mapping, not an independent DNS-resolution/provider-discovery claim. No new signing infrastructure.
- **6 — unreadable ancestor metadata:** writer independently checks every ancestor's `.git` entry with `lstat` before consulting Git. Any present entry or error other than definite ENOENT refuses output. A real synthetic checkout with an owner-only evidence child and sibling `.git` chmod000 reproduces the former bypass and now refuses without creating a file. Permissions are restored and the entire temporary fixture is removed.
- **8 — effective ICU rules:** database-default collation capture reads catalog JSON using supported `datlocale`/`daticulocale` field names and captures `daticurules` alongside provider/collate/ctype/version. ICU configurations lacking represented rules or effective locale refuse capture. Actual disposable ICU databases with the same `en` locale, one with `& z < a`, return opposite `'a'::text < 'z'::text` results and now yield different collation signatures/exact-copy rejection. A small catalog-boundary test covers both known locale fields and rejection when rules are unavailable.
- **Runbook correction:** `README.md`, `acceptance.md` and evidence instructions consistently separate Task13's staging database-writer freeze/transfer and provisional directory (expiry≤24hours, explicitly refreshed/reviewed as needed) from separately authorized Task14 shared WorkOS admission freeze, final watermark/export/target capture and final activation. Both named staging Render APIs and staging Modal consumers/schedules are covered; staging does not freeze the shared provider source or alter production auth/signup. All live gates remain unchecked.

### Round 2 RED/GREEN commands and results

Initial RED, only synthetic inputs/new disposable PG55438 databases:

```
rtk proxy env KENKUI_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55438/postgres /Users/dizzler/.local/share/mise/installs/node/24.21.0/bin/node --test scripts/neon/test/inventory-round2.test.mjs scripts/neon/test/inventory-safety.test.mjs
```

Result: **4 failed/1 passed**. Alias review: `Missing expected exception`; actual enum-only destination: `Missing expected rejection`; actual unreadable-.git fixture: `Missing expected rejection`; ICU signatures unexpectedly equal (`C.UTF-8`, `C.UTF-8`,153.14,i,en) despite the independently asserted true/false sort results. These failures directly reproduced all four remaining counterexamples before production edits. Following the fixes, same focused command passed5/5. Added known-catalog-field and unsupported-domain checks; `inventory-round2.test.mjs` passed4/4.

Final affected-helper GREEN:

```
rtk proxy env KENKUI_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55438/postgres /Users/dizzler/.local/share/mise/installs/node/24.21.0/bin/node --test scripts/neon/test/inventory-round2.test.mjs scripts/neon/test/inventory-upgrade.test.mjs scripts/neon/test/restore-verify.test.mjs scripts/neon/test/inventory-safety.test.mjs scripts/neon/test/billing-alert.test.mjs
```

Result: **27 passed,0 failed,0 skipped**. This preserves the existing actual pre0004→0004→0008/0009/0010, historical chronology, sequence, private/auth, provenance, R2, schema and exact500/admin-helper coverage while adding the new cases. No unrelated full Python/Studio/native/API suite was rerun. `rtk proxy .venv/bin/ruff check scripts/neon/inventory.py`: **All checks passed!** `rtk proxy .venv/bin/ruff format --check scripts/neon/inventory.py`: **1 file already formatted**. `rtk git diff --check`: clean.

Self-review read the scoped code/test/doc diff and the complete updated staging/final guidance. Checked actual enum-definition inclusion, conservative unsupported-type refusal, both locale field variants/rules requirement, independent ancestor marker checks, canonical provider identity plus normalized endpoint comparison, valid different-database/same-server operation and review-side bindings in existing fixtures. No new concrete implementation concern found. The operator/provider mapping and eventual shared-source final freeze remain independent live evidence requirements, not local-test claims. All operational acceptance, actual Auth/backup/restore, role/RLS, owner custody, billing/DNS/native/cold/overnight and production actions remain controller-owned and pending.
