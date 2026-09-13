# Private beta release runbook

The library is the audiobook engine. This release adapts the server, workers,
storage and browser to its current public API. Paid checkout is disabled in the
hosted beta composition; invited accounts receive a one-time allowance.

## Verified locally

- Real API submission through nested Pocket synthesis, M4B metadata/chapter
  inspection and full FFmpeg decode.
- Browser upload, preflight, job completion and download against the fixture server.
- PostgreSQL bootstrap/replay, concurrent idempotent admission, artifact/settlement,
  queued cancellation/release and retention using disposable schemas.
- AuthKit boundary tests use a fake SDK. They do not verify a live WorkOS tenant.
- R2 adapter tests use a fake object client. They do not verify a live bucket.

Run all local gates from `kenkui-server`:

```sh
uv sync --frozen --all-groups --extra hosted
# In the sibling web checkout: npm ci; npx playwright install chromium
python scripts/verify-workspace.py
```

Set `KENKUI_TEST_DATABASE_URL` to a disposable PostgreSQL database to include its
integration tests. The test role must be allowed to create/drop schemas. Set
`KENKUI_BETA_TEST_VOICE` to an already loaded voice to include real synthesis.
The real-render test creates its own temporary job/source/output directory.

## Local server and web

```sh
# Provision explicitly before serving. Rendering never downloads models.
uv run python -c 'import kenkui as kk; kk.load_voice("eponine")'
# Build ../kenkui-studio first with npm run build.
uv run kenkui-server --web-build-path ../kenkui-studio/dist --max-jobs 2 --render-workers 1
```

The server advertises loaded voices by default. `KENKUI_POCKET_MANIFEST` selects
the managed manifest consistently for discovery and worker rendering. Character
mode is enabled with repeated `--model` options; preflight accepts only allowed
models and validates all referenced voices. The adapter explicitly disables the
library's optional identity-model default rather than selecting an unconfigured
external model. Character mode still requires its own audio-quality acceptance.

## Staging prerequisites

Use a dedicated PostgreSQL database, private R2 bucket and WorkOS environment.
Do not point a first migration at an existing unrelated database. Use
`deploy/beta.env.example` as the configuration inventory; store actual values in
provider secrets or an ignored file, never Git. Generate `KENKUI_SESSION_SECRET`
as a Fernet key (32 random bytes encoded as URL-safe base64), using
`cryptography.fernet.Fernet.generate_key()`. The WorkOS Python SDK requires this
format; Render-generated hexadecimal secrets cannot seal its session cookies. Keep `R2_KEY_SALT` stable for a bucket.

Configure HTTPS web/API origins on the same site, for example
`app.kenkui.fm` and `api.kenkui.fm`, because browser sessions use SameSite=Lax.
Register `WORKOS_REDIRECT_URI` in WorkOS and allow the web origin as the logout
return URL. Set `KENKUI_INVITED_EMAILS` to the initial allowlist. No invitations
or other email are sent by these scripts. Only verified, allowlisted email
addresses receive access. Removing an address rejects subsequent requests.

`KENKUI_VOICE_IDS` must name voices provisioned into the worker model volume.
Configure a voice catalog appropriate for the intended use; the application does
not infer voice rights from whether an embedding is present.

## Build and deploy sequence

The production-shaped deployment is now Render API + managed PostgreSQL,
Cloudflare static web/R2, and Modal workers. Follow [production.md](production.md)
for the environment-specific Blueprint generator, pinned release images, Modal
network configuration, and website deployment commands. This replaces the
single-environment `kenkui-beta` worker instructions.

## Staging rehearsal (still required)

- Real WorkOS login, callback, refresh, logout and rejection of a non-invited user.
- Real R2 upload/materialization, completed download and denied cross-user access.
- Real Modal synthesis, worker termination, lease expiry/retry and API restart.
- Cancellation while queued, during synthesis and near publication; released
  allowance after failure/cancellation and exactly one settlement after success.
- Several representative EPUBs, including a long book; listen to output and check
  chapter order, metadata, covers and playback in intended players.
- Test retention on explicitly disposable aged records. Sources expire 24 hours
  after all dependent jobs terminate; unsubmitted uploads expire after 24 hours;
  artifacts expire after 30 days. Unpublished attempt objects expire after 24 hours.
- Measure duration, memory, storage and compute cost. Current defaults are two
  simultaneous jobs globally, one library render worker per job, three active jobs
  per account, 50 MiB uploads, two million speech characters per job and 1,000
  initial credits per invited account. Adjust only after measuring.

## Operations and recovery

`GET /v1/health` proves the API process is running, not that providers are ready.
Check a canary job to verify the complete service. Correlate API request IDs and
job/dispatch IDs with worker logs. Alert on elevated API errors, jobs stuck past
the execution timeout, expired claims, repeated worker failures, failed retention
runs and provider budget thresholds. Provider alert configuration remains a
staging task; no external dashboards were configured locally.

Keep a database backup schedule and rehearse restoring into a separate database.
A restore must use the matching private bucket and key salt. Keep the previous
API image, web bundle and worker release. Roll back application versions together;
do not reverse database migrations or restore production data blindly. After
rollback, run one canary and check outstanding job/credit states.

## CI setup

The server workflow runs the complete sibling integration suite and disposable
PostgreSQL tests. Set repository variables `KENKUI_CORE_REPOSITORY`,
`KENKUI_CORE_REF`, `KENKUI_WEB_REPOSITORY`, and `KENKUI_WEB_REF` to compatible
immutable revisions. For private sibling repositories, supply a read-only
`KENKUI_REPO_READ_TOKEN`. The workflow deliberately fails if these are missing.
Real synthesis remains an explicit provisioned-machine release gate.

## Tester onboarding

Give testers the beta URL, supported EPUB/M4B scope, their usage allowance and
retention periods. Ask them to retain source files and download completed books
before expiry. For failures, request the job ID and a description of the action;
do not request private book contents by default. Provide the chosen support
address before launch. Payments, subscriptions and native packaging are deferred.
## Container sizing

The current API image includes the library's full locked dependencies, including
Torch and CUDA packages, and is approximately 9 GB locally. Allow for image pull
time and registry capacity during the staging rehearsal. A smaller API dependency
set is a follow-up optimization that must preserve library inspection behavior.
