# Hosted API foundation

Node 24.21.0; pinned dependencies and package lock. Run `npm ci`, `npm test`,
`npm run typecheck` and `npm run build` inside this package. The build emits a
Node 24 ESM bundle with external runtime packages and a source map without source
contents. The build embeds the managed browser SDK asset. `neon.ts` now uses
`dist/index.js`, so build before deployment and ship/stage the external runtime
dependencies. No deploy command has been run.

`src/index.ts` exports the Neon web `fetch` handler. It reads configuration on
the first invocation, constructs the Hono app once, and passes the lazy `getPool`
function without calling it. Importing modules does not read `DATABASE_URL`, open
connections, query, run migrations, or provision services. A failed configuration
load returns a sanitized 503 and can be retried.

## Configuration

Set values separately for staging and production:

| Variable | Behavior |
| --- | --- |
| `KENKUI_ENV` | Required: `staging` or `production` |
| `KENKUI_ALLOWED_ORIGINS` | Comma-separated exact browser origins; empty disables CORS |
| `KENKUI_VOICE_SET` | Default `vctk`, or explicit IDs present in the pinned manifest; unknown IDs fail configuration |
| `KENKUI_MODEL_ALLOWLIST` | Comma-separated models; default matches the existing hosted DeepSeek model |
| `KENKUI_EMAIL_NOTIFICATIONS` | `true` advertises configured notification support; deployment must match worker settings |
| `KENKUI_INGRESS_PROTECTION` | `true` requires the origin secret at configuration load |
| `KENKUI_ORIGIN_SECRET` | When set, every request must carry matching `X-Kenkui-Origin-Secret`, including OPTIONS; edge ingress injects this, clients never receive it |
| `DATABASE_URL` | Required only when a database operation first calls `getPool`; use the environment's pooled Neon URL |

The handler binds real Neon JWT verification when `NEON_AUTH_BASE_URL`,
`NEON_AUTH_JWKS_URL`, `KENKUI_AUTH_RESOURCE`, and `KENKUI_NATIVE_CLIENT_ID` are
configured. Missing configuration fails closed. Unmapped subjects require recovery;
production new-account admission remains gated on a trusted reconciliation policy.
Optional `KENKUI_LEGACY_TOKEN_ISSUER` enables the verified legacy-JWT recovery
consumer after exact-subject backfill. See [auth rollout and client protocol](../deploy/neon/oauth-setup.md)
for browser/native v2 contracts, provider configuration, and mandatory live gates.

## Extension boundaries

- `createApi(deps: ApiDeps): Hono<ApiEnv>` returns an extensible router. Later
  modules can register routes on it; the request-ID/CORS/error/auth middleware
  already applies to those routes. Protected resource prefixes and the session/
  logout routes require verified authentication. Stripe webhook and notification
  unsubscribe paths are excluded from user auth and must get their own verification.
- `AuthVerifier.verify(Request): Promise<Principal | null>` must validate the
  provider token/session and map it to the stable internal `userId` UUID, `issuer`
  and `subject`. Null means unauthorized. Only the verifier may establish a
  principal; resource routes read `c.get('principal')`. Neon API transport is an
  explicit Bearer JWT; the old `__Host-kenkui-session` cookie is recognized only
  to return a one-time-login requirement and is never a Neon identity.
- `ApiError(status, code, message, details?, headers?)` exposes a safe canonical
  `{error:{code,message,details,requestId}}` response. For 429, use the existing
  `http_error` envelope and explicit `Retry-After`; no in-memory rate limiter is
  supplied. Unknown exceptions become a sanitized 500. `Logger` gets metadata,
  never raw exceptions, tokens, request bodies, DB URLs or query strings.
- `getPool(): Pool` is shared lazily per isolate. `createLazyPool(config, deps?)`
  allows dependency injection; `close()` drains that instance, and a later getter
  creates a fresh pool. `closePool()` exposes this for the shared instance; do not
  close after every invocation. Max 3, 10-second idle timeout, 5-second connection
  timeout, verified TLS. All URL `ssl*` and libpq compatibility flags are stripped
  so they cannot replace TLS verification. The Neon SDK handles expected idle
  disconnects; unexpected errors emit only `database.pool_error` metadata.

## Private assets and document operations

Asset configuration is read only by the first relevant resource request, with no
provider connections at import, static requests or route registration. Keep R2,
Modal, database and credentials isolated per environment. Reuse the existing
Python R2 settings exactly: `R2_ENDPOINT`, `R2_BUCKET`, `R2_ACCESS_KEY_ID`,
`R2_SECRET_ACCESS_KEY`, and `R2_KEY_SALT` (existing empty-salt semantics remain).
The same salt, kind and server-owned ID produce the same private key in Python
and TypeScript. No public request accepts an object key or URL.

Set `KENKUI_MODAL_BRIDGE_URL` to the matching deployed function's actual HTTPS
`*.modal.run` root. `KENKUI_MODAL_PROXY_KEY` and `KENKUI_MODAL_PROXY_SECRET` are
the separate Modal proxy-token pair; `KENKUI_BRIDGE_SECRET` matches the inner
bridge's secret. All three credentials are server-only. The client disables
redirects and implicit retries, caps response bytes, and bounds its HTTP wait to
120 seconds. This is not a promise that timed-out Python work has stopped.
Transient 429/503/504 upstream failures return safe retryable public errors.

`registerAssetRoutes` preserves EPUB-only 50 MiB uploads and PNG/JPEG 8 MiB
covers. It derives the owner from the verified internal principal before private
reads or compute calls. Uploads retain baseline behavior: bytes labelled EPUB
are accepted and hashed; inspection performs actual EPUB validation. Covers are
immutable deterministic snapshots in Python. Internal `/cover` uses the same
existing reader and 8 MiB decompressed member bound, returning bounded base64;
public responses remain image bytes with private/no-store and nosniff headers.

Apply additive `0007_source_upload_intents.sql` only through the existing Python
migration command, before deploying either runtime. No request runs DDL. A source
PUT first commits a durable intent, then transfers bytes with no open transaction,
then atomically publishes its owned asset and removes the intent. Failed or
ambiguous PUTs leave tracking. Finalization, retries and daily cleanup share a
per-source transaction advisory lock and enforce database-clock expiry. Expired
intents permanently fence publication of that attempt and retain tombstones,
so delayed PUT completion remains eligible for later deletion. Covers keep a
semantic operation ID, with one stable live attempt generation. After a failed
attempt expires, or a completed snapshot reaches normal retention, the same
source/image can retry using a fresh generation and a different private key.
Old cleanup cannot delete that new key. Existing live completed snapshots remain
idempotent, including legacy generation-zero IDs. A newly reserved cover attempt
has no digest until its deterministic transformation finishes; neither runtime
can publish it while the digest is NULL.

The existing daily retention pass also makes at most 100 upload/tombstone cleanup
attempts, selected fairly by indexed next-due time. It commits claims before R2
DELETE, tolerates missing objects, retains tracking after failures, and rotates
each attempt to the next day. NULL-digest cover reservations never reached PUT:
they retain their generation fence but are excluded from the due index, cleanup
query and recurring R2 DELETE attempts. Cleanup failures emit only an aggregate
`source_upload_cleanup_failed failure_count=N` warning, with no IDs, keys or raw
provider exceptions. Completed assets keep their existing durable row:
repeat cleanup uses `deleted_at` plus `next_source_cleanup_at`, preserving the
original live/recent-job guard and normal 24-hour source TTL. Live assets always
suppress deletion. Tracking rows are retained indefinitely because a lost or
hung provider operation has no proven maximum lifetime. This adds row growth
and bounded daily retries; backlog can delay deletion beyond 24 hours. No extra
queue, polling loop or keepalive was added.

Job download routes must select an authorized job's stored `artifacts.path`:
worker attempt IDs are `jobUUID.leaseUUID`, while older objects may use a UUID.
`ObjectStore.artifactUrl` preserves the existing 300-second private signed GET
and the hosted route's 303/no-store redirect, so Range transfers go directly to
R2. Never substitute jobId for an attempt's stored path. `streamArtifact` offers
a backpressured, cancellable Range-aware fallback without buffering an audiobook.
`artifactFilename` preserves Python's sanitization and Unicode character limit.
The Modal preflight count is authoritative; billing/admission must enrich its
null pricing and balance fields using the authoritative account.

Live gates remain deferred to staging acceptance: verify private bucket access,
matching existing salted objects, successful browser/native upload and inspection,
immutable cover retry and read, actual signed download MIME/filename/Range and
cancellation, proxy plus inner authentication, deadline behavior, and the existing
daily maintenance schedule's coverage of migration 0007. Exercise failed/ambiguous
PUT and late-completion cleanup with real R2; synthetic tests establish protocol
behavior, not provider acceptance. No providers or customer prices changed here.

## Hosted credits, checkout and admission

Billing resolves an authenticated internal identity to its existing credit account.
First use applies the original `beta-allowance:{identity UUID}` reference atomically.
Before enabling traffic, reconcile imported account IDs, balances, lots, ledger
references and the actual legacy `KENKUI_BETA_CREDITS` setting (default 1000).
Use the existing `KENKUI_ESTIMATED_COST_CENTS_PER_MILLION` setting (default 126).
Job quotes take the normalized count from the Python preflight bridge.

Checkout requires the environment's `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`,
`KENKUI_WEB_ORIGIN` and `KENKUI_STRIPE_API_VERSION`. Obtain the API version used
by the legacy Stripe account and verify managed-payment checkout in Stripe test
mode before pinning it. Missing version disables checkout. The webhook can still
fulfill signed existing sessions using its signing secret. Preserve the deployed
`KENKUI_STRIPE_TAX_CODE` (default `txcd_10105001`), both catalog versions, and the
checkout session ID in payment and purchase references.

The API arms an empty private R2 marker at
`operations/recovery/{environment}/{job UUID}` before committing an admission.
It retains the marker across dispatch acceptance and ambiguous failures. Replay
rearms the original job before deleting the unused candidate. Match `KENKUI_ENV`
to Python's `KENKUI_DEPLOYMENT` and verify the existing recovery and daily cleanup
schedules in staging. Python owns execution leases and terminal credit settlement.

## Catalog provenance and compatibility

`src/catalog.json` contains 59 VCTK metadata records exported from core
`97cba6bdb663c15d2d90d6e4e017be23ecdb9c7c` using the real
`CATALOG`/`catalog_voice` registry and server `select_hosted_voices('vctk', ...)`,
sorted by registry ID. Source SHA-256 values cover the core's actual `builtin.json`
and `pack.json`. No synthetic `narrator` fixture is used in production.
This describes the selected core catalog; staging acceptance must still confirm
the worker has the same voices provisioned. Changing the supported voice set
requires an explicit manifest update and matched worker release.

Frozen HTTP tests consume `contracts/neon/baseline.json` directly, including
generated early-auth request IDs and unauthenticated CORS preflight. The baseline
has no 429 case: the additional 429 test covers the same error envelope and
Retry-After without inventing application rate limiting. Catalog GET routes also
reject unsupported methods, including HEAD, with canonical 405.

An optional PostgreSQL lifecycle test uses only the disposable synthetic instance
at `127.0.0.1:55438/postgres` when `KENKUI_TEST_DATABASE_URL` is set. It terminates
only its own idle backend, verifies recovery, waits for the actual 10-second idle
closure, and verifies close/reopen. Only that test's injected local pool disables
TLS because the disposable instance has no TLS; production TLS options are
separately checked after node-postgres URL parsing.

Primary references checked November 2, 2026:
[Functions quickstart](https://neon.com/docs/compute/functions/get-started),
[runtime limits](https://neon.com/docs/compute/functions/reference/runtime-limits),
and the published `@neon/functions@0.11.0` and `@neon/config@1.10.0` READMEs.
`attachDatabasePool` is supported outside the Neon runtime; configuration uses
the current `@neon/config/v1` import and top-level `functions` field.
