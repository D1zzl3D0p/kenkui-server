# Hosted API foundation

Node 24.21.0; pinned dependencies and package lock. Run `npm ci`, `npm test`,
`npm run typecheck` and `npm run build` inside this package. The build emits a
Node 24 ESM bundle with external runtime packages and a source map without source
contents. Ship the installed production dependencies with the bundle, or let Neon
bundle `src/index.ts` from `neon.ts`. No deploy command has been run.

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

The production handler currently binds **no authentication verifier**. Missing
user credentials receive the frozen 401; supplied credentials on protected routes
receive explicit 503 until Task 5 binds real authentication. No token is accepted
as a real identity by this foundation. Real auth routes, CSRF/session lifecycle,
identity SQL and resource handlers belong to later tasks.

## Extension boundaries

- `createApi(deps: ApiDeps): Hono<ApiEnv>` returns an extensible router. Later
  modules can register routes on it; the request-ID/CORS/error/auth middleware
  already applies to those routes. Protected resource prefixes and the session/
  logout routes require verified authentication. Stripe webhook and notification
  unsubscribe paths are excluded from user auth and must get their own verification.
- `AuthVerifier.verify(Request): Promise<Principal | null>` must validate the
  provider token/session and map it to the stable internal `userId` UUID, `issuer`
  and `subject`. Null means unauthorized. Only the verifier may establish a
  principal; resource routes read `c.get('principal')`. Task 5 must update credential
  presence recognition for the chosen Neon session transport; the foundation
  recognizes Bearer and the frozen `__Host-kenkui-session` cookie.
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
