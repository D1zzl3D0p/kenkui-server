# Render / Cloudflare / Modal deployment

This is the deployment path for private beta and subsequent production releases.
The Cloud specification now selects Render for the API and managed PostgreSQL.
Beta uses granted allowances. For card payments and cost-based pricing, follow
[payments.md](payments.md); test/live Stripe settings belong on the API service.

## Environment inventory

| Resource | Staging | Production (private beta initially) |
|---|---|---|
| Web | app.staging.kenkui.fm | app.kenkui.fm |
| API | api.staging.kenkui.fm | api.kenkui.fm |
| Render API | kenkui-staging-api | kenkui-production-api |
| Render database | kenkui-staging-db | kenkui-production-db |
| Modal environment | staging | production |
| Modal app | kenkui-staging | kenkui-production |
| Modal secret | kenkui-staging-worker | kenkui-production-worker |
| Modal volume | kenkui-staging-models | kenkui-production-models |
| R2 | separate staging bucket/keys | separate production bucket/keys |
| WorkOS | staging environment | staging environment during private beta |

Render API and database default to Virginia, 1 CPU / 2 GB each, with 10 GB
database storage. These are initial sizing choices, not benchmark results.
Confirm account costs before provisioning. Keep render workers near the database.

## 1. Source and release image

Configure the server repository's CI variables from README.md. The library ref
must be a full commit SHA. Run **Build verified release image** in GitHub Actions.
It runs the integration suite and publishes a Linux amd64 image to GHCR, recording
the immutable `ghcr.io/OWNER/REPOSITORY@sha256:DIGEST` in its summary. The image has
server and library revision labels. Configure Render's registry credentials for
a private GHCR package before deploying it; never make private code public merely
to bypass registry authentication.

Use the same image digest for staging and production. Keep the exact server and
core checkouts used to build it when deploying Modal. Record the web commit with
the release as well. Run the real-render gate on a provisioned machine.

## 2. Modal network and environments

Create Modal environments `staging` and `production`. Ordinary Modal workers
connect directly to Render's external PostgreSQL endpoint using TLS and database
credentials. No Team/Enterprise plan or fixed outbound IP is required.

The default Blueprint permits authenticated database connections from dynamic
IPv4 addresses. Use a dedicated database role and verify the server certificate.
Optionally configure `KENKUI_MODAL_PROXY` and pass its static IPs with
`--worker-cidr` when an IP allowlist is desired. Modal's optional Proxy requires
Team/Enterprise. Do not configure an allowlist of static IPs without also routing
all database-accessing worker functions through the matching proxy.

## 3. Generate and sync the Render Blueprint

From the server checkout, using the real image digest :

```sh
python deploy/render_blueprint.py --environment staging \
  --image ghcr.io/OWNER/REPOSITORY@sha256:DIGEST > render.staging.yaml
```

The output is JSON, which is valid YAML. Repeat with `production` and save as
`render.production.yaml`. Commit the generated files to the deployment repository
and select the corresponding file when creating each Render Blueprint. The
generator requires immutable images and supports optional database IP restrictions.
The generated document contains secret prompts, never secret values.

Render injects the database's internal connection string into the API. Its
pre-deploy command applies ordered migrations before switching the service.
Migrations must remain backward compatible with the previous release.

Supply the prompted R2, WorkOS, Modal, and invitation settings from
`beta.env.example`. The session secret is generated separately per API service.
Copy the supplied beta emails from the local workspace `.dev.vars` into the
production service's `KENKUI_INVITED_EMAILS`. That file is not loaded automatically.

## 4. Worker secrets and voice provisioning

Create the environment-specific Modal secret from a private environment file
containing `DATABASE_URL`, `R2_BUCKET`, `R2_ENDPOINT`, `R2_ACCESS_KEY_ID`,
`R2_SECRET_ACCESS_KEY`, `R2_KEY_SALT`, and `OPENROUTER_API_KEY`.
Completion email is sent by the worker from a **second** Modal secret,
`kenkui-{environment}-mail`, holding `KENKUI_SMTP_PASSWORD`, `KENKUI_SMTP_SENDER`,
`KENKUI_SMTP_USERNAME`, `KENKUI_WEB_ORIGIN`, `KENKUI_API_ORIGIN`, and
`KENKUI_UNSUBSCRIBE_SECRET`. It is separate so adding mail never rewrites worker
credentials that cannot be read back. Both secrets must exist in an environment
before deploying it; leaving the SMTP password empty is supported and simply
sends no mail. iCloud+ custom domains authenticate as the Apple ID, so
`KENKUI_SMTP_USERNAME` is that address and `KENKUI_SMTP_SENDER` is the alias.
Use Render's **external** PostgreSQL URL here, with `sslmode=verify-full` and
the platform's trusted certificate chain. Verify connectivity from Modal before
admitting jobs. Do not copy Render's internal hostname to the workers.

R2 credentials and key salt must match the API in that environment. The public
`KENKUI_VOICE_SET=vctk` policy is part of the release rather than a secret.
WorkOS keys and browser session secrets are not needed in workers.
`KENKUI_UNSUBSCRIBE_SECRET` is a separate key precisely so that stays true; it
must match the API in the same environment, or its unsubscribe links will not
verify.

```sh
uv run --extra hosted modal secret create kenkui-staging-worker \
  --env staging --from-dotenv /PRIVATE/PATH/worker.env
KENKUI_DEPLOYMENT=staging \
  uv run --extra hosted modal deploy deploy/modal_app.py --env staging
KENKUI_DEPLOYMENT=staging \
  uv run --extra hosted modal run --env staging deploy/modal_app.py::provision
```

Repeat with production names and environment. Do not provision while jobs run.
The API uses `MODAL_ENVIRONMENT` and `KENKUI_MODAL_APP` to select the matching app.
Recovery and retention are deployed on their existing minute/hour schedules.

## 5. WorkOS, DNS, and website

During private beta, both deployments use the existing WorkOS staging application
`Kenkui private beta` (client `client_01M0Y37EQMGD270AW5W7NZC1E0`). Register
each API's `/v1/auth/callback` URL and matching web logout return URL on that
application. WorkOS production activation is a later promotion task. Enable Google, GitHub, and Apple. Use
provider callback URLs supplied by WorkOS when configuring those providers.
Production provider credentials must belong to Kenkui. Apple users must share
their allowlisted email; a private relay address will not match it.

Configure each API custom domain using Render's displayed DNS records and verify
TLS. Keep API/auth/download responses uncached at Cloudflare. Test SSE through
the chosen proxy configuration, including idle periods and reconnects.

From the exact web release checkout:

```sh
npm ci
npm test
npm run deploy:staging -- --dry-run
npm run deploy:staging
# After staging acceptance, from the same commit:
npm run deploy:production
```

The deployment script builds with the matching API origin before invoking the
pinned Wrangler version. Supply `CLOUDFLARE_ACCOUNT_ID` and a scoped
`CLOUDFLARE_API_TOKEN` through CI secrets or the local environment. Custom-domain
routes and SPA fallback are checked in. Browser builds receive no server secrets.

## 6. Recovery, monitoring, and promotion gates

Paid Render Postgres supplies continuous backups/PITR. Confirm the account's
recovery window and restore into a separate database before beta admission.
Record the restore duration and recovered job/credit counts. Keep an independent
logical export for long-term recovery, with private access and defined retention.
HA can be enabled on an eligible database/workspace; it is not enabled by this
initial Blueprint and incurs standby cost. Review it before wider public launch.
See [Render recovery](https://render.com/docs/postgresql-backups).

Configure provider alerts for API availability/errors, database capacity,
worker failures, stuck jobs, scheduled-task failures, and budget thresholds.
These account-level alerts are not created by these files. `/v1/health` checks
the API process only; use a canary render to verify all dependencies.

Complete the live rehearsal in README.md: both invited logins, uninvited
rejection, refresh/logout, upload/render/download, cancellation/retry, cross-user
isolation, and allowance settlement. Record evidence against all three code SHAs
and the image digest. Then promote the same code to production.

For rollback, restore the previous image digest in the Blueprint, redeploy the
matching Modal checkout, and deploy the previous web commit. Do not roll back
database contents to undo an application deployment. Run a canary afterward.

## Local validation versus live acceptance

The Render generator is schema-validated locally, Modal definitions can be
imported without deployment, and Wrangler supports a deployment dry run.
These checks do not prove provider credentials, regional connectivity, DNS,
database restoration, subscriptions, alerts, or live authentication are configured.

The API Docker build now disables the dependency cache in the image layer.
It still includes the library's inference dependencies; CPU-only dependency
packaging requires separate compatibility work and is not claimed complete.
