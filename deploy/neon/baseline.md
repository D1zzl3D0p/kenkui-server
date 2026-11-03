# Frozen migration baseline

The machine-readable source of truth is [`contracts/neon/baseline.json`](../../contracts/neon/baseline.json). It freezes 34 observed HTTP method/route pairs, 48 synthetic HTTP cases, the actual browser/native and hosted OpenAPI schemas, and SQL invariant results from a migrated disposable PostgreSQL 17 database. [`route-matrix.md`](../../contracts/neon/route-matrix.md) maps every observed route to its cases. This is a local compatibility baseline, not a statement that the migration is deployed or accepted.

## Source and deployment reconciliation

| Component | Selected immutable source | Difference from deployment evidence |
|---|---|---|
| Server | `8ce780667f893dabcf0d4ee41bf0e6eacf855cc1`, implementation base `2795daf838aac4e04cea28998e741d972f8c2972` | Historical deployed label is `22c6eadcc180fd916b2f121e46c602c39a5b6254`. Five intervening implementation commits add idle pooling/recovery, idle release configuration, scoped Studio verification, and database-secret overlays. Selected migration plan/spec commits add documentation. |
| Core | `97cba6bdb663c15d2d90d6e4e017be23ecdb9c7c`, package `10.1.1` | Matches historical deployed label; detached sibling checkout is required. The HTTP test verifies the actual imported core checkout SHA, rather than trusting the broad package version constraint. |
| Studio | `ea95155bd1649889c07aaadeafd3cb383d5bbf6f`, package `10.1.0` | Selected current local baseline includes managed desktop server, local voices and OpenRouter casting. Historical deployed label `468e067d02837e3a3ad79f5235c8b8111afc8e37` is absent from this shallow/local history, so a reliable diff cannot be claimed. |

Fresh read-only Render inventory on November 2 reports both API environments using:

`ghcr.io/d1zzl3d0p/kenkui-server@sha256:7964ea844ff40a7c17d77f888c546c1dec7fcd2cf8f2904c2ce4743d8ac2fee7`

The live deploy records separately report image SHA `sha256:4f00d1240d421b2c85c15f89ec7f59da1568e492f8b9d3e61a087dcf6ab53a9f`. Preserve both provider fields; do not assume they are interchangeable. Production service `srv-dajcep8jo6nc73dacrpg`, deploy `dep-db9ogep42hec73caqr5g`, finished October 17 at 14:40:49 UTC. Staging service `srv-dajceonqj5pc73d2qmsg`, deploy `dep-dasimmo473hc738fmbbg`, finished September 27 at 14:46:16 UTC. Production is unsuspended on plan `1c-2g`, with auto-deploy off. Root supplied these fresh connector results. Fresh OCI source-label verification, deployed Modal revision, live database migration IDs/checksums, and deployed Studio source provenance remain deferred. Historical source labels are explicitly marked historical in the manifest.

## Auth and provider inventory

Both live APIs use WorkOS client `client_01M0Y37EQMGD270AW5W7NZC1E0`, app `app_01M0Y37EQNP7Y35R8NA5DE5J12`, private-beta sandbox/Staging environment `environment_01M0Y347Z9N6FYNTGVY3725X1Y`, which has four users. The unused default Production environment has zero users. This resolves the project discrepancy; it is not an account export. Existing methods include password, Google, GitHub, Apple, Microsoft and SSO. The target remains Google/GitHub only, preserving existing internal ownership through verified provider-qualified proof. No profile, email list, token or live credential was read or exported by this task.

Both live native-config endpoints report `http://127.0.0.1:43827/callback`, which the fixture uses. Real browser/native OAuth, token refresh, account linking, issuer/audience and logout require staging acceptance; a synthetic SDK grant does not establish provider compatibility.

Existing Neon projects are staging `divine-boat-06396016` and production `mute-unit-91304892`, AWS `us-east-1`, PostgreSQL 17, Free plan, Auth disabled and no Functions. This read-only inventory was supplied by root. Contents and live schemas were not inspected here; no empty-project inference is made. No provider writes, paid billing, DNS, deployment, user export or emails occurred.

## Pending work and workspace isolation

The selected source advertises EPUB only and rejects `application/pdf` with 415. Do not silently change that fixture based on a newer core package. Read-only inspection found these separate pending/reference worktrees; none was modified or included:

| Work | Source/revision | Selected? |
|---|---|---|
| Upright-scan OCR option | Core branch `fix/pdf-upright-ocr-option`, `24df8967bd700c99ae0f8b83e8392713f15312ff` | No |
| Marker-free list provenance | Docling branch `fix/merged-text-provenance-spans`, `1eaefe847f5baacae927ed7e0016b9e6664e7771` | No |
| Leading-dot decimal pronunciation | Core branch `fix/leading-decimal-pronunciation`, `3262ffb272425615ed9bbbef70c0f94f656ffc51` | No |

These branches are not evidence that PDF has reached the selected deployment. Reconcile actual release contents before a staging/production rollout. The core baseline remains pinned rather than incorporating unrelated work automatically.

All selected checkouts are isolated siblings under this task's `work/`: server branch `migration/neon-full`, Studio branch `migration/neon-auth`, and detached core. Their original linked checkouts remain under `i-m-seeing-a-bill-from/` at server `2795daf`, Studio `ea95155`, core `97cba6bd`. Server and Studio original status checks were clean. The original core checkout was not edited. Tooling generated an untracked `.serena/` directory only in the isolated server workspace; it is excluded from commits without changing shared Git excludes.

CI already requires immutable 40-character core/Studio refs, uses PostgreSQL 17, installs frozen Python/Node dependencies, and runs `scripts/verify-workspace.py`; release CI publishes pinned image digests. No CI changes are needed for this task. Manifest SHA-256 values freeze `uv.lock`, Studio's `package-lock.json`, checked OpenAPI and all seven SQL migrations. Both `0002_beta.sql` and `0002_notifications.sql` are intentional distinct migration names.

## Running the baseline

Use the pinned sibling core checkout above; do not install an arbitrary latest core version. `uv.lock` resolves the editable sibling to core `10.1.1`. Tested runtime: Python 3.12.13, Node 22.22.3, npm 10.9.8, PostgreSQL 17.7. Exact relevant Python package versions are in the manifest.

```sh
rtk proxy uv sync --extra hosted --frozen
rtk proxy uv run --frozen pytest tests/test_neon_contract_export.py -q
```

The HTTP test runs offline with synthetic input. The SQL test requires the existing `KENKUI_TEST_DATABASE_URL` convention and is explicitly skipped without it. It creates a fresh random schema, runs the actual migrations twice, exercises real SQL and drops that schema. Never point this test convention at a live database. All credential-like fixture strings are synthetic.

The disposable instance prepared for this task is PostgreSQL 17.7 from `@embedded-postgres/darwin-arm64@17.7.0-beta.16`, tarball SHA-1 `b066d0075a493a7593c3727dd1038cc58c47c566`. Binaries are `/private/tmp/kenkui-neon-pg/package/native/bin`, data `/private/tmp/kenkui-neon-pg/data`, log `/private/tmp/kenkui-neon-pg/server.log`, socket `/private/tmp`, loopback port `55438`. Its synthetic test URL is `postgresql://postgres@127.0.0.1:55438/postgres`. It uses local trust auth and must remain loopback-only; no user data or provider credentials exist in it. Initialization/start and database connections required reviewed local permissions because the normal environment blocks shared memory and local sockets. It remains available for subsequent migration tasks. Stop it when no longer needed:

```sh
rtk proxy /private/tmp/kenkui-neon-pg/package/native/bin/pg_ctl -D /private/tmp/kenkui-neon-pg/data -m fast -w stop
```

Some existing tests call `create_app()` without a data directory and default to the real home directory. The initial restricted run had seven SQLite failures from that path. The baseline suite passes with a temporary home substituted only inside the Python test process; no shell `HOME` was changed and no product code was edited:

```sh
rtk proxy env KENKUI_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:55438/postgres uv run --frozen python -c 'from pathlib import Path; import tempfile,pytest; test_home=Path(tempfile.mkdtemp(prefix="kenkui-test-home-")); Path.home=classmethod(lambda cls:test_home); raise SystemExit(pytest.main(["-q"]))'
```

In this workspace use `UV_CACHE_DIR=/private/tmp/kenkui-neon-uv-cache` with `rtk proxy env` if the normal cache is protected. Downloads required reviewed network access; no lockfile changed. Studio baseline commands in its isolated sibling were `rtk proxy npm ci`, `rtk proxy npm test`, and `rtk proxy npm run build:web` (a writable temporary npm cache was used for the successful install).

## What is characterized, and what remains live acceptance

The HTTP fixtures include deterministic request payloads/raw bytes, response status/errors, camelCase fields, CORS, host-only secure cookies, CSRF rejection, native PKCE input validation/refresh/logout, cross-owner denials, credit history, raw signed Stripe replay, and `Last-Event-ID` replay. They run the actual ASGI application unchanged. External WorkOS SDK calls use complete synthetic grants; HTTP resource storage uses the existing SQLite/local fixture and hosted service fixture. Billing uses the real in-memory ledger. Clock and IDs are fixed; only transport cookie expiry wall time is normalized to `<clock>`.

Actual SQL separately demonstrates 500 purchased credits once on duplicate payment; one 200-credit reservation for concurrent same-key admission; one acceptance/one rejection for two concurrent 300-credit admissions; repeated release/settlement leaves 200 credits; two jobs/dispatches, five ledger entries, preserved ownership, and exactly seven migrations. Existing suite checks still cover leases, checkpoints, cancellation, notification state, retention and other branches. The HTTP fixture is not a claim of exhaustive status/branch coverage.

The frozen middleware characterization includes early auth/CSRF rejection with a generated request ID returned in `X-Request-ID`; that generated value replaces any caller-supplied ID. The earlier note claiming these responses lacked the header was incorrect. Preserve the observed fixture without an unrelated normalization. The fixture also records notification preference routes as unavailable without configured hosted identities and checkout as 503 without a payment client. Real enabled notification/checkout, successful R2 download/cover transforms, actual synthesis, live OAuth linking and deployment parity remain required staging acceptance checks.

Treat `baseline.json` as immutable evidence. Later TypeScript compatibility tests consume its requests and expected responses; do not regenerate it to make a port pass. If scope or supported behavior intentionally changes, record the decision and review the explicit fixture delta.

## Cleanup source reconciliation — November 3

The cleanup implementation uses the existing migration source without regenerating this frozen baseline. Its source SHA is `254543854d8e9cfd8b7ebfb71f693efc03a85fa7`; Studio candidate is `167154028908dbd90a9d42f3da71f7e9b2d46b7b`. Core cleanup is separately based on main `e45039eeac2467d8bb4a8e18e7515105b7ce222b`. Hosted compatibility verification retains this document's core pin. Newer PDF/TOC/voice behavior requires a deliberate selected-feature release and must not silently replace characterized EPUB-only behavior. The infrastructure source inventory records these distinctions; no live acceptance is claimed.
