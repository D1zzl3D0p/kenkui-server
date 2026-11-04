# Isolated staging runtime

The live production service currently uses the default `kenkui-staging` Modal app. Do not edit that app or its maps to restore testing.

Deploy the isolated test app with these public build inputs:

```text
KENKUI_DEPLOYMENT=staging
KENKUI_MODAL_APP=kenkui-staging-test
KENKUI_DATABASE_IDLE=1
KENKUI_BACKGROUND_SCHEDULES=0
KENKUI_PUBLIC_WEB_ORIGIN=https://app.staging.kenkui.fm
KENKUI_PUBLIC_API_ORIGIN=https://api.staging.kenkui.fm
```

Within Modal's `staging` environment, this selects only:

- `kenkui-staging-test-worker`: test R2 bucket-scoped access and normal worker/model-provider settings. Do not copy production R2 or database access.
- `kenkui-staging-test-database-idle`: restricted worker login for the separate staging database.
- `kenkui-staging-test-bridge`: distinct bridge secret matching only the staging API.
- Volume `kenkui-staging-test-models`: test-app model cache, provisioned on demand.

The test app has no `retain_objects` registration, no backup map and no periodic schedules. The manual `recover_jobs` function remains available. Its bridge dispatches only to its selected app name.

Completion and migration SMTP mail are suppressed by an empty final password override. If deliberately enabling test mail, set deployment input `KENKUI_TEST_EMAIL_NOTIFICATIONS=1` and separately configure `kenkui-staging-test-mail` with staging origins and test-only mail settings. This never selects the existing `kenkui-staging-mail` map. The API's `KENKUI_EMAIL_NOTIFICATIONS=false` should remain the default until mail testing is intended.

The separate staging API needs its own database, test R2 bucket, bridge URL/secret, test-mode Stripe credentials, staging Auth authority and public origins. Set `KENKUI_ENV=staging` and `KENKUI_SERVICE_ENVIRONMENT=staging`. Runtime label selection does not create or verify those resources. Creating credentials, maps, storage or deployments is a separate operator step.

Without the optional app override, staging and production retain their existing names, five function registrations, secret-map selection and scheduling choices. This source change does not switch a live service.
