# WorkOS runtime retirement

November 4, 2026. Hosted authentication now lives in the TypeScript Neon API;
Python serves local requests and processing. No WorkOS tenant, Render resource,
live credential, database row or deployed service was changed by this cleanup.

## Removed

- Python WorkOS SDK, its `hosted` install extra, and five exclusively required
  lock entries: WorkOS, PyJWT, cryptography, cffi and pycparser. Remaining package
  versions and the hosted core pin are unchanged. CI and workspace verification
  now install `worker`.
- Old Python browser/native session routers, SDK adapter, API composition and
  `serve` command. The maintenance module still supports migration/recovery.
  Local no-auth and generic injected Bearer authentication remain supported.
- WorkOS API secret field and notification-origin fallback to a WorkOS callback.
  Current notifications use the explicit public `KENKUI_API_ORIGIN`.
- Exclusive old-login tests and the old ASGI HTTP-baseline regeneration harness.
  The checked baseline, source provenance and original harness remain available
  on the prior backed-up branch. Current TypeScript tests still exercise frozen
  resource cases; SQL/payment/ownership/worker comparisons remain active.

The billing comparison helper keeps the old allowance transition as a narrow
test-only SQL/repository oracle, rather than starting a fake WorkOS API just to
grant credits. No production billing rule changed.

## Retained deliberately

| Remaining WorkOS reference | Why it remains |
|---|---|
| TypeScript `auth/recovery.ts`, optional `KENKUI_LEGACY_TOKEN_ISSUER` and migration link endpoint | Explicitly enabled dual-proof legacy-owner recovery, not normal Neon sign-in. Removing it could strand an unmapped old owner. |
| Provider bindings, `workos_subject`, SQL migrations and identity backfill | Durable identity/ownership history and source reconciliation. Do not rename/drop these during runtime cleanup. |
| Directory, challenge, quarantine and evidence tools/tests | Protect existing identity, credit and migration history; no WorkOS SDK dependency is required. |
| Frozen artifacts and archived instructions | Historical provenance; not current setup or fallback commands. |

Studio and core contain no active WorkOS implementation to remove. Normal Neon
enrollment uses `KENKUI_ACCOUNT_POLICY=neon`; migration policy remains an explicit
supported composition until legacy recovery is closed deliberately.

## Verification

New regressions first reproduced the old cookie being accepted as a Bearer
credential, `serve` reaching WorkOS configuration, and old callback settings
selecting notification links. All pass after retirement. Worker recovery,
maintenance and local API tests remain active.

- Full Python suite: 413 passed, one provisioned-voice skip. The same suite passes
  in the isolated SDK-free development/worker environment, installed with image-pinned
  uv0.12.10. WorkOS, PyJWT, cryptography, cffi and pycparser are absent there.
- Hosted TypeScript: 251 passed, 12 optional paired-locale skips. This includes
  optional recovery, Neon admission and billing comparison checks.
- Studio: 173 passed and five browser journeys passed; generated API and both
  frontend builds pass. Frozen local OpenAPI remains unchanged.
- Neon archive/restore/fallback tooling: all 66 checks pass against synthetic local
  fixtures. Mypy and Ruff pass; no live login, provider deployment or tenant
  deletion is claimed.

Self-review confirmed the remaining cookie-specific middleware was removed with
its routes, the generic Bearer ownership boundary still rejects missing credentials,
and the old allowance oracle no longer requires SDK/provider scaffolding. An
over-broad intermediate edit was caught by targeted tests and corrected before
the complete SDK-free and integration checks.
