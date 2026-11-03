# Frozen Python route matrix

Source: `baseline.json`; 16 checked OpenAPI resource paths plus conditional auth, Stripe, unlisted unsubscribe, and docs helpers. Each row has at least one actual ASGI request fixture. OPTIONS is a middleware fixture.

| Method | Path | Modes | Synthetic cases (status) |
|---|---|---|---|
| GET | `/v1/assets/{asset_id}/book` | local, browser-native, hosted | inspect-book (200); foreign-asset (403) |
| GET | `/v1/assets/{asset_id}/cover` | local, browser-native, hosted | cover-missing (404) |
| GET | `/v1/auth/callback` | browser-native | browser-callback-invalid (400); browser-callback (303) |
| GET | `/v1/auth/login` | browser-native | browser-login (303) |
| GET | `/v1/auth/native/authorize` | browser-native | native-authorize (303) |
| GET | `/v1/auth/native/config` | browser-native | native-config (200) |
| GET | `/v1/auth/session` | browser-native, hosted | browser-session (200) |
| GET | `/v1/billing` | local, browser-native, hosted | billing (200) |
| GET | `/v1/billing/history` | local, browser-native, hosted | credit-history (200); foreign-credit-history (200) |
| GET | `/v1/capabilities` | local, browser-native, hosted | capabilities (200) |
| GET | `/v1/docs` | local, browser-native, hosted | docs (200) |
| GET | `/v1/health` | local, browser-native, hosted | health (200) |
| GET | `/v1/jobs` | local, browser-native, hosted | missing-auth (401); invalid-auth (401); cors-missing-auth (401); jobs (200) |
| GET | `/v1/jobs/{job_id}` | local, browser-native, hosted | job (200); foreign-job (403) |
| GET | `/v1/jobs/{job_id}/artifact` | local, browser-native, hosted | artifact-missing (404) |
| GET | `/v1/jobs/{job_id}/events` | local, browser-native, hosted | sse-resume (200) |
| GET | `/v1/notifications` | local, browser-native, hosted | notification-get (404) |
| GET | `/v1/notifications/unsubscribe` | local, browser-native, hosted | unsubscribe-invalid (400) |
| GET | `/v1/openapi.json` | local, browser-native, hosted | openapi.json (200) |
| GET | `/v1/voices` | local, browser-native, hosted | voices (200) |
| HEAD | `/v1/docs` | local, browser-native, hosted | head-docs (200) |
| HEAD | `/v1/openapi.json` | local, browser-native, hosted | head-openapi.json (200) |
| PATCH | `/v1/notifications` | local, browser-native, hosted | notification-patch (404) |
| POST | `/v1/assets` | local, browser-native, hosted | upload-epub (201); upload-pdf-unsupported (415); upload-empty (422) |
| POST | `/v1/assets/{asset_id}/cover` | local, browser-native, hosted | cover-invalid (422) |
| POST | `/v1/auth/logout` | browser-native | browser-csrf (403); browser-logout (303) |
| POST | `/v1/auth/native/exchange` | browser-native | native-exchange (200); native-invalid (422) |
| POST | `/v1/auth/native/logout` | browser-native | native-logout (204) |
| POST | `/v1/auth/native/refresh` | browser-native | native-refresh (200) |
| POST | `/v1/billing/checkout` | local, browser-native, hosted | checkout-unavailable (503) |
| POST | `/v1/billing/webhooks/stripe` | hosted | stripe-invalid-signature (400); stripe-paid (204); stripe-replay (204) |
| POST | `/v1/jobs` | local, browser-native, hosted | job-invalid (422) |
| POST | `/v1/jobs/preflight` | local, browser-native, hosted | preflight (200) |
| POST | `/v1/jobs/{job_id}/cancel` | local, browser-native, hosted | cancel-terminal (200) |

Fixtures include complete synthetic request JSON/raw-body bytes, cookies and selected response headers. Auth is exercised with real handlers and synthetic SDK grants. Raw signed Stripe bodies credit the real test ledger once on replay. Database invariants separately run actual Postgres SQL.

A baseline case does not claim every status/branch is covered. Existing auth, billing, lease/checkpoint, retention, local API and PostgreSQL suites remain required. Native-config matches both live APIs; live OAuth/provider token lifecycle, successful R2 transfers and real synthesis remain staging acceptance gates.
