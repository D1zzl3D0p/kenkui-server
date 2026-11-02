# Sleeping API and database hosting

This opt-in release uses Render Free APIs and separate Neon Free PostgreSQL 17
projects for staging and production. Preserve the existing R2 buckets, key salts,
Modal environments/apps/volumes, and job concurrency. Never stop queued or running
jobs to reduce hosting costs. Production follows successful staging acceptance.
No paid upgrade or subscription is part of this procedure.

## Release and configuration

Build from the tested server commit and the deployed dependency pair. Dispatch
**Build verified release image** with
`core_ref=97cba6bdb663c15d2d90d6e4e017be23ecdb9c7c` and
`web_ref=468e067d02837e3a3ad79f5235c8b8111afc8e37`. The reusable verification
workflow checks out both overrides; image checkout/label uses the core override.
The web override selects verification code only; it does not deploy Studio.
Empty inputs use `KENKUI_CORE_REF` / `KENKUI_WEB_REF` as before. Do not change
shared repository variables to build this release. Record
the server/core/Studio SHAs, image digest, and matching Modal checkout in private
cutover evidence. Use the same tested image digest in both environments.

Generate the desired final configuration with a real digest:

```sh
python deploy/render_blueprint.py --environment staging --idle-hosting \
  --image ghcr.io/OWNER/REPOSITORY@sha256:DIGEST > render.staging.idle.yaml
# Repeat with --environment production for render.production.idle.yaml.
```

Validate both documents against the current
[Render schema](https://render.com/schema/render.yaml.json). The idle document
contains only `kenkui-{environment}-api-idle`, a free image service, and secret
prompts. It has no database resource, `fromDatabase`, or pre-deploy command.
`--worker-cidr` is rejected because Render cannot secure an external Neon database
through that option. Legacy invocations retain the paid service/database names.

| Setting | Where it belongs |
|---|---|
| `DATABASE_URL` | API and new Modal `kenkui-{environment}-database-idle` overlay secret; external Neon URL, pooled or direct, verified TLS |
| `DATABASE_URL_UNPOOLED` | Same database overlay for daily backup; direct Neon host (no `-pooler`), verified TLS |
| `KENKUI_DATABASE_IDLE=1` | API and environment used when deploying Modal |
| `KENKUI_DEPLOYMENT`, `MODAL_ENVIRONMENT`, `KENKUI_MODAL_APP` | Matching staging/production only |
| `R2_BUCKET`, `R2_ENDPOINT`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_KEY_SALT` | Existing matching API/worker values; retain bucket and salt |
| `WORKOS_API_KEY`, `WORKOS_CLIENT_ID`, `WORKOS_REDIRECT_URI`, `KENKUI_SESSION_SECRET`, `KENKUI_INVITED_EMAILS` | API; preserve callback origin and session secret during service transfer |
| `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET` | API credentials for the matching Modal environment |
| `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` | API; staging sandbox credentials remain separate from production |
| `OPENROUTER_API_KEY` | Existing worker secret |
| SMTP settings and `KENKUI_UNSUBSCRIBE_SECRET` | Existing separate mail secret; shared unsubscribe key also on API |
| Private GHCR registry credentials | Render service image pull configuration |

Use private files/secret stores, never URLs or credentials in logs, shell
arguments, generated Blueprints, or Git. Do not replace existing Modal secrets
with partial dictionaries or stale local copies: live values are write-only and
may contain newer credentials. Create a separate
`kenkui-{environment}-database-idle` secret in the matching Modal environment,
containing only `DATABASE_URL` and `DATABASE_URL_UNPOOLED`, entered through a
private secret-management interface. In idle mode this overlay is attached last
to every function, after the unchanged worker and mail secrets;
[later Modal secrets override duplicate keys](https://modal.com/docs/guide/secrets).
Legacy mode does not attach the overlay. Confirm R2 credentials can list/put/delete
`operations/recovery/{environment}/` and `operations/backups/{environment}/`.

## Database migration and free-service replacement

1. Confirm the account, Free quotas, PostgreSQL 17, target region, and five-minute
   Neon scale-to-zero for each project. Restore a staging export into an empty
   target first. Use PostgreSQL 17 `pg_dump --format=custom --no-owner --no-acl`
   and `pg_restore --no-owner --no-acl --exit-on-error`, with credentials in a
   private environment/pgpass file. Store exports and rollback configuration
   outside Git with mode 0600. Keep configuration credentials out of R2 backups.
2. For the final export, pause admission/writes, drain all nonterminal work, and
   pause writers/schedules. Preserve Stripe retries; acknowledge only durable
   processing. Compare every table's row counts and stable ordered digests,
   migrations, balances, credit lots/allocations, ledger sums, payment deduplication,
   and object references before allowing writes on Neon. Rehearse independent
   restore and record duration.
3. Run the migration command once from the exact locked release, with the direct
   Neon URL loaded privately as `DATABASE_URL`:

   ```sh
   uv run --frozen --extra hosted python -m kenkui_server.hosted migrate
   ```

   The command uses `DATABASE_URL` (not `DATABASE_URL_UNPOOLED`) and holds the
   existing PostgreSQL migration lock. Verify success before starting API/workers.
   Free services do not run the legacy paid pre-deploy migration command.
4. Create a **new** free service named `kenkui-{environment}-api-idle` using the
   [Create service API](https://api-docs.render.com/reference/create-service) or
   Dashboard, with the pinned image, Virginia region, `/v1/health`, and
   `python -m kenkui_server.hosted serve`. Render rejected the paid-to-free plan
   update with HTTP 400 during this migration; do not retry it as a downgrade.
   Initially omit the custom domain already attached to the paid service. The
   generated idle Blueprint records the desired domain for the eventual transfer.
   Validate the new service through its Render hostname before changing DNS.
5. Point API and **all** worker consumers at the same verified Neon target and
   deploy the matching Modal checkout with both `KENKUI_DEPLOYMENT` and
   `KENKUI_DATABASE_IDLE=1` set. Create and verify the matching database overlay
   before deployment; never replace the existing worker/mail secrets from local
   JSON files. Keep those secrets and models untouched.
   Run the staging checks below before production. For changes to an existing
   replacement use the [Update service API](https://api-docs.render.com/reference/update-service)
   and trigger a deploy explicitly; updates alone do not deploy.
6. Transfer the custom domain from paid service to validated replacement, update
   Cloudflare DNS to the replacement's displayed target, verify TLS, and rerun
   auth, CORS, SSE and download checks through the original API origin. Keep
   auth/API/download responses uncached and existing WorkOS callbacks unchanged.
   Suspend the paid API **only after** domain cutover and all consumer checks.

Do not sync the new document into the old Blueprint as the migration mechanism.
Disable old Blueprint automatic sync and detach its retired resources through
Render's management controls before adopting a service-only idle Blueprint.
Review the proposed sync: no database creation, deletion, or paid plan is allowed.
Do not assume an omitted `databases` list deletes or preserves an existing
database safely. Do not resync legacy generated files: they can recreate paid
databases. Keep originals and exports until verified cutover; final deletion
requires explicit approval naming the two original Render database IDs.

## Idle behavior, backups, and budget

The free API has 0.1 CPU / 512 MB. Measure startup/representative request RSS before
acceptance. Render sleeps after 15 minutes without inbound traffic and can show a
loading page while waking (about a minute). Local disk is ephemeral; keep books
in R2. There is no free shell, one-off job, or persistent disk. The shared workspace
allowance is 750 active instance-hours/month, so suspend staging outside testing.
Avoid uptime pings or warmers. Bandwidth and unusually high outbound traffic can
cause charges or suspension; check the account's usage limits. Render positions
Free for previews/hobby use, so this private-beta choice accepts interruptions.
See [Free limits](https://render.com/docs/free) and
[compute plans](https://render.com/docs/compute-plans).

Neon Free currently includes 100 CU-hours, 1 GB storage and 5 GB public transfer
per project, with five-minute scale-to-zero. Recheck these limits at provisioning.
Observe sleep via its control-plane status; SQL probes wake it. See
[Neon plans](https://neon.com/docs/introduction/plans) and
[scale-to-zero](https://neon.com/docs/introduction/scale-to-zero).

Idle pools start with no connections and release idle connections after 60
seconds. Minute recovery lists private R2 markers and opens no database when empty.
Active-job recovery retains its existing leases and concurrency. The two scheduled
functions per environment remain: minute recovery and daily maintenance (replacing
hourly retention). Daily maintenance reconciles authoritative nonterminal jobs,
repairs markers, performs retention, and uploads a consistent compressed logical
backup under `operations/backups/{environment}/YYYY-MM-DD.dump`, retaining seven
copies. `DATABASE_URL_UNPOOLED` is mandatory for that backup. Monitor failed
schedules and backup age; daily backups imply up to 24 hours of data loss after a
successful run, longer if failures go unnoticed. Test restore separately.

Target fixed API/database compute cost is $0 within these allowances after paid
resources are retired. R2 storage/operations, Modal work/maintenance, transfers,
and other providers remain usage based; daily backup wakes consume resources.
Review quota usage and provider billing after cutover, including retained database
storage. Do not silently upgrade. The already-paid October bill is unchanged.

## Live acceptance and rollback

Local tests/schema checks do not establish live acceptance. During Task 4, record
actual service IDs, revisions, RSS, elapsed wake times, and results in private
evidence. Staging acceptance must include a genuine 15-minute period with no API
traffic, followed by capabilities, invited login/callback, uninvited rejection,
session refresh/logout, upload/preflight, progress SSE reconnect, existing/new
download, tiny book completion, cancellation, interrupted-worker recovery without
double settlement, and a signed Stripe **sandbox** webhook after idle. Verify
Neon remains asleep with empty minute recovery ticks, then wakes for real work.
Repeat the key account/balance/download/canary checks for production. No live
purchase or unrequested email is needed.

If the observed wake response breaks Studio, first capture it in a failing
Vitest test. Any fix must bound idempotent startup-read retries to 90 seconds,
2-second backoff, respect aborts, coalesce within one client, and handle network
failures/transient 502/503/504/HTML warm-up responses. Never retry 401/403 or replay
checkout/uploads/arbitrary writes. Keep the existing create-job idempotency key.
Do not add frontend retries merely from speculation.

Before writes resume, rollback means restoring both API and workers to the
preserved Render database and matching previous image/core/Studio settings,
resuming the paid service, and transferring domain/DNS back. After writes resume,
never point consumers at an old snapshot: pause/drain writers, export and verify
current Neon data, restore/sync it to the rollback database, and only then return
all consumers together. Recheck credit/payment deduplication and a canary. Keep
backward-compatible migrations; application rollback does not undo database data.

For Modal rollback, redeploy the matching previous checkout with
`KENKUI_DATABASE_IDLE` unset or `0` and the same `KENKUI_DEPLOYMENT`. This removes
the database overlay from function configuration and restores the database URL
in the preserved worker secret, along with the legacy schedules. Verify that URL
targets the rollback database before resuming writers. The overlay can remain
stored but unused; do not delete or rewrite worker/mail secrets. After Neon writes
have resumed, complete the data synchronization described above before switching
either API or workers back.
