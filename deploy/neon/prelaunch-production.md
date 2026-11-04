# One prelaunch Neon deployment

Keep the already tested Neon project, main branch, Auth issuer and public desktop
client. The Google/GitHub provider callback URLs therefore stay unchanged; no new
provider apps or private credentials are needed. All existing data is prelaunch
test data. Further database-copy and disaster-recovery rehearsals are out of scope.

`production-public.json` is the source-reviewed public configuration. It contains
no secrets. `KENKUI_ENV=production` binds the public API authority/receipt to
`api.kenkui.fm`. The separately validated `KENKUI_SERVICE_ENVIRONMENT=staging`
retains the existing internal Modal app, bridge checks and R2 recovery-marker
namespace. This label does not select the public Auth audience. Keep the existing
restricted API database credential and existing matching Modal/R2 secrets.
Deploy the existing staging Modal app with `KENKUI_BACKGROUND_SCHEDULES=0`,
`KENKUI_PUBLIC_WEB_ORIGIN=https://app.kenkui.fm`, and
`KENKUI_PUBLIC_API_ORIGIN=https://api.kenkui.fm`. These public-only overrides keep
completion/unsubscribe links on production while reusing existing secret maps.

Set `KENKUI_ACCOUNT_POLICY=neon` for normal verified Neon enrollment. It keeps
existing issuer/subject bindings, creates new accounts without WorkOS directory
imports, and never merges accounts by email. Omission retains the old migration
policy. Existing account balances are retained. `KENKUI_BETA_CREDITS=0` can disable
new beta grants while keeping existing balances; omission keeps the previous1000
default. Root selects the actual production setting explicitly.

## Publish the API

1. Confirm the production Studio authority matches this public config exactly.
   Record immutable server/Studio revisions. Build on Node24.21.0 using the pinned
   existing lockfiles; typecheck, changed Auth/release tests and runtime build must
   pass. Package with `scripts/neon/package.mjs production <Studio-path> <SHA>`.
   Restore/check those same bytes with `scripts/neon/restore-release.mjs` before
   evaluating `hosted-api/neon.ts`. No database migration or copy is required.
2. Root privately selects the restricted API connection and existing bridge/R2
   credentials. Do not export the provider owner connection or overwrite Modal
   secret maps. Resolve the payment policy before upload: the staging Stripe key
   is not evidence of live payment configuration. The API's previous default also
   grants1000 beta credits per new account; do not silently carry this into a
   publicly reachable deployment.
3. Upload the production Function to the SAME branch. The Function name is
   `kenkui`; this replaces that branch's prior staging Function configuration.
   Treat the old staging web/native public service as retired when this happens.
   It may still serve catalogs, but its former native audience is no longer the
   API's configured production audience.
4. Root applies only the explicit public Auth metadata additions in the JSON:
   production trusted web/API domains, production resource on server and existing
   public client, production login/consent pages. Re-read current full resources
   before replacing lists; preserve unrelated resources if any were added. Keep
   dynamic registration/CIMD disabled, loopback redirect unchanged, consent on,
   provider apps/secrets/issuer/JWKS unchanged. No private client is created.
5. Root updates the gateway Function-origin target as needed and switches only
   `api.kenkui.fm` to Cloudflare, then publishes Studio at `app.kenkui.fm`. Confirm
   public catalog, native config issuer/client/audience and authenticated owner
   account read. A worker request must retain internal environment `staging`.
   Provider redirects remain bound to the existing Neon issuer. The owner chose
   to preserve real production checkout, so select the existing production
   Stripe settings rather than copying staging test settings. Payment code is
   unchanged; no payment-disabled switch was introduced.
6. Remove Render services/databases only under the owner's separate explicit
   deletion authorization. Stop background schedules independently; an idle API
   alone does not prove idle database billing. This checklist makes no guarantee
   that unrelated providers, domains or existing plans cost under$10/year.

## Exact Auth API operations for root

All target project `divine-boat-06396016`, branch `br-gentle-thunder-bbo796nz`.
Use `update_auth_oauth_server` with the JSON `server` fields, preserving enabled
state and other fields. Use `update_auth_oauth_client` for existing public client
`UwdWqqDMzWqQGrtIUHDmRBIENDDogySX`, changing only its full `resources` list.
Use `add_auth_trusted_domain` twice with `auth_provider:better_auth` and the two
exact domains. These are pending root coordination; this document does not claim
they have run. Fetch authoritative metadata afterward and compare exact pins.
