# Reviewed release and ingress preparation

Task 12 builds and rehearses locally. Nothing here authorizes skipping Task 13's consistent staging restore, runtime-role validation, writer freeze, and joint readiness check. Reuse the **existing** staging website/API and Modal deployments; production waits for acceptance. No permanent preview app or extra warm paid service is required.

## Immutable artifacts and CI

Use Node **24.21.0** and the committed package locks. From `hosted-api`, run `npm ci`, `npm run typecheck`, `npm test`, `npm run build` and `npm run smoke` (the last uses only the explicitly permitted disposable `KENKUI_TEST_DATABASE_URL`). Build embeds the pinned Managed Auth browser SDK. It bundles runtime JS, checks external imports against Node builtins plus pg's unused optional `pg-native` accelerator, and reconstructs output from an allowlist:

```
dist/function/index.js
dist/function/package.json
dist/standalone/standalone.js
dist/standalone/package.json
```

There are no source maps, runtime `node_modules`, `.env`, provider exports, `.git`, or source-tree copies in either archive. `smoke.mjs` copies these exact files to a clean temporary directory, imports both, then runs the same separately bundled synthetic auth/PG/Stripe/R2/upload/cookie/CORS/stream contracts against Function fetch and the real Node adapter. `KENKUI_PG_BIN=/path/to/postgres17/bin node test/production-artifact.mjs` additionally creates and removes an independent SSL-enabled local PG17 cluster on **55439** and synthetic HTTPS JWKS, checking exact shipped artifacts through real verified-TLS JWT→owner lookup and embedded SDK serving. Only that child process receives its temporary CA; host trust and shared 55438 are unchanged.

The `neon.yml` workflow verifies PRs but prepares release archives **only** on explicit `workflow_dispatch` from `main`, after its selected GitHub `staging`/`production` environment gate. Task 13 must confirm those repository environments actually have required reviewers and branch restrictions; YAML cannot establish that protection. The workflow has no provider deploy, DNS, migrations, image publication, automatic branch deployment, or secret export. Supply a reviewed immutable Studio SHA. Its release input must contain the selected authority before packaging; missing production pins fail closed. The manifest records exact server/Studio/core/Node versions, public authority and file SHA256s. Operator deployment remains a separate reviewed action.

`release-image.yml` remains the independently invoked **legacy Python image** builder, retaining its GHCR `${server SHA}` tag and core inputs. It does not produce the same-auth fallback. Do not use that image for rollback after Neon-only identities exist. `ci.yml` retains its other checks and now uses Node24; Studio checks its independently frozen broader local schema, while `contracts/neon/baseline.json`, the frozen OpenAPI hash and hosted contract tests retain the older hosted baseline. Do not regenerate Studio from the older migration server. Studio's native workflow packages no Python/core sidecar at any revision; its optional local launcher remains unchanged.

## Canonical Function configuration

The only Neon config is **`hosted-api/neon.ts`**. Build first; it declares `source: './dist/function', bundler: 'none'`, supported by pinned `@neon/config` 1.10.0. The prebuilt directory is what is archived, not the repo root. No duplicate root `neon.ts` or request-time migrations.

The deploy-time allowlist in `hosted-api/release-environment.ts` requires an explicit `KENKUI_ENV`, `KENKUI_RELEASE_AUTHORITY` and **`KENKUI_API_DATABASE_URL`**. Obtain `KENKUI_RELEASE_AUTHORITY` from the selected `authority` in the reviewed release manifest (it derives from Studio's single validated input). It supplies exactly `apiOrigin`, `issuer`, `jwksUrl`, `clientId`, `resource`. The same public values compile into both `VITE_KENKUI_AUTH_AUTHORITIES` and `KENKUI_AUTH_AUTHORITIES` in Studio. No discovery fallback, copied production config, guessed client or runtime picker can add trust.

`KENKUI_API_DATABASE_URL` is deliberately mapped to Function `DATABASE_URL`. Neon's injected owner URL is **never** a release fallback. Before deploying, Task 13 must SQL-create and negatively test distinct restricted API and worker roles; Console/CLI/API role creation inherits `neon_superuser` and is unsuitable. Verify `current_user`, effective membership, DDL/DML denials, intended application and managed-user SELECT permissions. Backup full-read credentials remain maintenance-only. Validate the intended staging project `divine-boat-06396016`, branch `br-gentle-thunder-bbo796nz`, database `kenkui`, PG17 through current provider state. Production Auth currently has no approved release pins.

### Operator application of the exact reviewed artifact

Future releases are **operator-applied**, never push-to-deploy. This deliberately keeps provider-management credentials out of CI and requires the restore/freeze/readiness evidence before activation. No provider step below was performed in Task12.

Download the protected workflow artifact and check out its reviewed server and Studio SHAs. From the **server repository root**, run with Node24.21.0:

```
rtk proxy node scripts/neon/restore-release.mjs staging /approved/downloaded-release ../kenkui-studio
```

This checks environment, both exact checkout SHAs, core/Node pins, Studio's independently validated authority, the complete four-file manifest and each file SHA256. It rejects additional members, symlinks, devices, duplicate names and oversized members before installing **only the verified artifact bytes** into `hosted-api/dist/{function,standalone}`. Do not run the build afterwards. The manifest must come from the reviewed Actions run; a manifest supplied by an untrusted source is not an authenticity proof.

Prepare a permission-restricted API-only env file outside the checkout via the separately authorized selected-key route. Set `KENKUI_RELEASE_AUTHORITY` to that manifest's public `authority`. Validate actual database role/negative grants and the exact branch/public auth pins before apply. Use the existing approved target-scoped CLI credential/profile; do not mint a general CI key or log the file. Existing process env overrides `--env`, so use a controlled operator process with no stale API runtime settings. From **`hosted-api`**, the inspected official Neon8.17.1 command is:

```
rtk proxy npm exec --yes --package=neon@8.17.1 -- neon deploy --project-id divine-boat-06396016 --branch br-gentle-thunder-bbo796nz --config neon.ts --env /approved/private/staging-api.env --no-env-pull
```

Its `bundler:'none'` source is the restored two-file Function directory, so deployment archives the reviewed compiled bytes without rebuilding. `--no-env-pull` prevents an automatic owner-credential export afterwards. Review service/config changes without printing env values; do not add `--yes`, `--update-existing` or `--allow-protected` to bypass a drift/protected-branch review. Read back the function deployment/version/native URL and perform direct missing/wrong-secret checks before any ingress change. Production requires its independently reviewed real project/branch/pins and acceptance; never substitute a guessed target in this staging command. Provider drift, credential setup, live apply and read-back remain root-owned Task13/14 actions.

The allowlist requires API-needed R2, Modal proxy (`wk`/`ws`, never admin `ak`/`as`), bridge, unsubscribe and Stripe values and allowed web origins. Preserve exact existing salts/buckets/worker/mail values. Transfer only selected keys through the separately authorized protected route; never export/copy a full provider env or print config plans containing env values. Supply `KENKUI_ORIGIN_SECRET` identically to the Function and edge, with `KENKUI_INGRESS_PROTECTION=true`. Keep deployment credentials, restricted runtime credentials and backup credentials distinct. `neon deploy --env <protected-file>` reads a file whereas single-function `--env` accepts assignments; use the current installed CLI help and explicit project/branch targeting before any apply. Neither command is part of the package workflow.

## Edge preparation, then coordinated Task 13 activation

From `ingress`, use its committed lock, `npm run types`, `npm run typecheck`, `npm run dry-run`, `npm test`. New ingress pins Wrangler **4.156.1**, Miniflare **5.20261101.0-beta**, workerd **1.20261101.1** so local runtime supports the required **2026-11-03** compatibility date. Existing Studio stays on Wrangler4.131.1. Both trees constrain Miniflare's sharp to0.35.5 and undici to7.30.1 for known advisories; these local tools are not bundled into Worker output.

Studio additionally patches its existing Vite/Vitest/jsdom/source-map/brace-expansion development tools within compatible releases. Its retained generator7.13.0/Redocly1.x/minimatch5.1.9 chain has the moderate glob-amplification advisory [GHSA-g3hv-75wg-m249](https://github.com/advisories/GHSA-g3hv-75wg-m249); generation here consumes only the reviewed frozen local schema (or an explicit operator-selected trusted source). Do not expose schema generation as an endpoint or accept untrusted glob inputs. The current compatible generator/Redocly patch line still uses minimatch5, so neither a forced minimatch11 override nor a generator6 downgrade is bundled into this migration. Full audit output remains visible for review.

`node release.mjs staging <reviewed-native-function-HTTPS-origin>` writes only public `.wrangler/release.staging.json`, with the exact existing `api.staging.kenkui.fm` Worker custom domain, public origin and scoped limits. Review and run `npx --no-install wrangler deploy --config .wrangler/release.staging.json --env staging --dry-run` before any actual publish. The source has no deployable upstream/routes by default; a missing origin/binding/secret/client-IP fails closed. The production command is analogous only after production authority and acceptance exist. The native URL stays native; **never proxy a Neon-registered CNAME**. Cloudflare must own the consumer hostname.

At Task 13, after consistent restore, restricted-role tests, API/client readiness and a coordinated pause of **all** old staging writers/webhook consumers/schedules, deploy the existing staging API/website/Modal consumers onto one Neon authority. Never repoint the existing worker while the old API still writes Render. A temporary API host, if needed for initial origin/edge acceptance, belongs inside that coordinated stage and must be removed afterwards. Test native Function missing/wrong secrets, real browser/native sign-in and refresh, upload limits, SSE reconnect/cold runtime and signed downloads through the edge. Local tests are not evidence of deployed acceptance. Existing staging DNS points to `kenkui-staging-api.onrender.com`; current Wrangler OAuth reports no DNS-write. Root/operator must resolve the exact reviewed DNS/proxy transition rather than assume custom-domain creation will replace that CNAME.

The proxy overwrites `X-Kenkui-Origin-Secret`, preserves request bytes/path/query and upstream response/status/CORS/cookies/Location, uses `redirect:'manual'`, and never follows signed/external redirects with API credentials. It passes streams directly; abort/cancellation propagates. Public240/minute, login30/minute and costly30/minute bindings are separate per-IP route classes, with independent staging/production namespaces. Confirm namespace IDs are unused in the account before deployment. CF-owned connecting IP is the only key; no unverified subject/authorization value is trusted. Shared NAT may throttle multiple users. Limits are approximate **per Cloudflare location**, not global/per-user budgets. Neon native requests still incur invocation overhead before the origin-secret rejection, so the edge cannot guarantee a monthly spending cap. Verified account admission, credit reservations and concurrency stay in Postgres/API.

Logs contain fixed event/scope metadata only. Invocation logs are disabled, query strings are redacted in logs/traces, and sampled Logs **and** Traces are enabled. Never add request/response bodies, cookies, bearer/origin/proxy credentials, raw errors or query strings to logging. Provider-side capture and sampling must be read back during Task 13.
