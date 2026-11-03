# Neon authentication rollout — reviewed local configuration, not applied

Task 5 implements the server and reusable client protocol. No Auth enablement, deployment, provider credentials, SMTP, DNS, billing, emails or real sign-in was performed. Task 10 owns Studio/Tauri integration; Task 12 owns packaging/ingress; Task 13 must satisfy the gates below before cutover.

## One deployment authority

Use `hosted-api/neon.ts`. It declares `auth:true` and the existing Kenkui Function with source `./dist/index.js`. The config toggle provisions Auth; it does **not** enforce the social-provider policy, configure the OAuth server/client or implement recovery by itself. Keep staging admission closed until configuration is read back and checked.

Build from `hosted-api` with Node 24.21.0 and the committed lockfile:

```sh
rtk proxy env npm_config_cache=/private/tmp/kenkui-neon-npm-cache mise exec node@24.21.0 -- npm ci
rtk proxy env npm_config_cache=/private/tmp/kenkui-neon-npm-cache mise exec node@24.21.0 -- npm run typecheck
rtk proxy env npm_config_cache=/private/tmp/kenkui-neon-npm-cache mise exec node@24.21.0 -- npm run build
```

`build.mjs` bundles the managed browser SDK into the server artifact as an embedded script, served at `/v1/auth/client.js`. There is no CDN dependency or second frontend deployment. Do not commit `dist`. CI must build before evaluating/deploying the Function config. Node server dependencies remain external: deployment must stage the pinned runtime packages alongside the artifact (or bundle them through the provider's deployment packager). A copied `dist/index.js` alone is insufficient.

## Branch setup owned by the release operator

Staging project: `divine-boat-06396016`; production project: `mute-unit-91304892`. They are separate projects. Select the approved branch explicitly; never rely on the CLI's default project/branch. The recorded staging branch is `br-gentle-thunder-bbo796nz`; re-read before applying. Root owns all actual provisioning and secret transfer.

After reviewed enablement, read the branch Auth response to obtain `base_url` and `jwks_url`. Do not manufacture these values from an endpoint ID. Functions inject `NEON_AUTH_BASE_URL` and `NEON_AUTH_JWKS_URL`; verify their actual values. Only then compute the upstream provider callback URLs by appending `/callback/google` and `/callback/github` to the **complete returned base_url**. There are no real callback URLs to print before Auth exists.

| Setting | Staging value | Production value |
| --- | --- | --- |
| Protected resource / `KENKUI_AUTH_RESOURCE` | `https://api.staging.kenkui.fm` | `https://api.kenkui.fm` |
| OAuth server login page | `https://api.staging.kenkui.fm/v1/auth/login` | `https://api.kenkui.fm/v1/auth/login` |
| OAuth server consent page | `https://api.staging.kenkui.fm/v1/auth/consent` | `https://api.kenkui.fm/v1/auth/consent` |
| Trusted page origins | `https://api.staging.kenkui.fm`, `https://app.staging.kenkui.fm` | `https://api.kenkui.fm`, `https://app.kenkui.fm` |
| Native public client redirect | `http://127.0.0.1:43827/callback` | `http://127.0.0.1:43827/callback` |
| Native client ID / `KENKUI_NATIVE_CLIENT_ID` | Actual returned staging public-client ID | Separate returned production public-client ID |

These HTTPS hostnames are the intended release configuration, not a claim that the new routes are deployed. Retain `KENKUI_ALLOWED_ORIGINS` and the existing private ingress-secret mechanism. The hosted login pages must be reachable through the approved ingress: the browser must never receive the ingress secret.

1. Read configured upstream OAuth providers. Configure **only owned Google and GitHub** applications; no shared development Google credentials at production cutover. Disable/remove other new-login methods under the operator's reviewed change. Apple remains deferred. Supply provider secrets privately in protected settings, not command output, checked-in files or application bundles.
2. Read and disable email/password authentication (`neon neon-auth config email-password update --enabled false`, with explicit project/branch). Do not enable general email OTP, magic-link or anonymous login. Read back their actual managed settings and prove unauthorized methods cannot obtain application access; absent UI buttons are not a control-plane policy. **No independent ordinary email-OTP disable control has been verified.** The API cannot distinguish an alternate login method for an already-mapped Neon subject from the documented JWT claims alone. If Managed Auth cannot disable an undesired method independently, hold acceptance and report the limitation instead of assuming it is off. Resolve through a supported provider disable control, or a separately reviewed trustworthy server-side authentication-method attestation if the provider exposes one. Do not silently expand login methods or switch providers.
3. Add exact trusted HTTPS page origins. Enable branch localhost access only for the required managed public-client loopback flow. Current provider rules require this branch-wide exception. Register **only** the exact callback, including port; no wildcard, custom scheme, all-interface listener or arbitrary-port assumption.
4. Enable OAuth Provider with the hosted login/consent URLs and the single API resource. Omit MCP, CIMD and DCR. Preserve unrelated existing resources when submitting the replace-all resource list. No fake MCP configuration to obtain its refresh retry window.
5. Create owned client `Kenkui Desktop`, type `public`, exact redirect above, resource above, `skip_consent:false`. Public token authentication is `none`; there is no client secret in the desktop app. Set the returned public ID in protected Function config and the versioned native configuration.
6. Read back the branch issuer, JWKS, trusted origins, disabled login methods, resources, consent policy and public-client redirect. Test before admitting users.

Provider setup authority: [OAuth Provider](https://neon.com/docs/auth/guides/plugins/oauth-provider), [CLI Auth](https://neon.com/docs/cli/neon-auth), [social OAuth](https://neon.com/docs/auth/guides/setup-oauth). The newer provider-specific localhost condition takes precedence over the generic production checklist; its broader origin effects remain a staging test, not an assumed per-client exemption.

## Browser contract and hosted pages

Browser session management remains the managed SDK's HTTP-only session cookie at the Auth origin. The separate Kenkui API receives an explicit SDK JWT in `Authorization: Bearer …`; it never treats the Auth cookie or old WorkOS sealed cookie as a Kenkui credential. Default browser JWTs require EdDSA, `iss` and `aud` equal to the Auth **origin**, nonempty `sub`, `iat`, `exp`, authenticated role, and no anonymous/banned flag. Native JWTs require the full branch issuer, API resource audience membership, and the owned native `client_id`. Only after signature/claim validation do both map to the canonical complete branch authority plus subject. Prove actual browser/native subject parity on staging before rollout.

`createBrowserAuth` retains the managed vanilla adapter and exposes visible resume, on-demand accessToken, Google/GitHub login, continuation, consent and managed sign-out. `sessionOptions` is passed **inside** `BetterAuthVanillaAdapter`: refetch interval 0, focus refetch false, offline refetch false. No `useSession` atom/hook subscription is created. Task 10 must not introduce one indirectly through UI providers. Hidden resume calls do nothing, hidden protected requests fail before token fetching, and explicit visible resume revalidates local UI state. SDK cache hits may avoid server revalidation; local UI state never grants account access.

The page script preserves the provider's original signed query for the SDK's automatic query handling and navigation. It does not rebuild or navigate a second time after consent/continue. The SDK's signed query includes the `ba_param` manifest; a hand-built query with only `sig` is invalid. Consent displays client name, requested scopes/resource, and the exact loopback callback with a local-app warning. Both pages use a `frame-ancestors 'none'` HTTP CSP and no-referrer/no-store headers. Expired/invalid authorization errors are displayed.

Pinned `@neon/auth` is 0.8.2. Compatible Better Auth/core/OAuth Provider are pinned to 1.7.12 through overrides: unrestricted `~1.7.12` originally resolved 1.7.15 and produced a conflicting fetch peer. The installed SDK is exercised in tests, including cookie credentials, SDK OAuth methods and signed-query forwarding. The 0.8.2 `/token` cache can return the cached `{session:{token},user}` shape instead of `{token}`; the facade normalizes those two SDK outputs only. A candidate opaque cookie token is still rejected by server JWT verification. Package peer compatibility and mocks do not prove the deployed provider works.

Safari/third-party-cookie restrictions still apply to cross-origin `credentials:include`. Validate the actual deployment in Safari and the native system browser. If a same-origin Auth proxy/shared-parent-domain setup is needed, implement and review it before cutover; no unverified proxy is included here.

## Native protocol version 2 and Task 10 handoff

`GET /v1/auth/native/config` is public/no-store and now returns `protocolVersion:2`, `redirectUri`, `issuer`, `jwksUrl`, `clientId`, `resource`, `discoveryUrl`, and `scopes`. The old `/v1/auth/native/authorize`, `/exchange`, `/refresh`, and `/logout` routes return canonical **426 `client_upgrade_required`** with `details.protocolVersion:2`. This intentionally forces a coordinated desktop update; it is not backward-compatible sealed-token behavior.

The reusable `NativeAuthClient` is a protocol reference for Task 10's Tauri binding, not an already-updated Rust desktop app:

- Bind the exact loopback host/port/path before opening the system browser; fail safely on port collision. Keep the attempt private and one-use, with fresh state, S256 PKCE and five-minute expiration. Reject duplicate/missing/mismatched state, issuer, code, redirect, error responses and replay. Close the listener on completion/timeout; no credentials in callback URLs.
- Discover with exact issuer equality and pinned HTTPS issuer-scoped endpoints. Send resource on **both** authorize and token requests; request `openid profile email offline_access`. Token POSTs are form-encoded with public `client_id` and no secret/basic auth. Opaque access tokens are rejected.
- Inject a strict access-token verifier: EdDSA/JWKS, full issuer, exact API audience membership, owned client ID, nonempty subject and expiry. `verifyToken` is a required trusted adapter interface, not a caller identity assertion. Never accept an ID token or browser JWT in this adapter. The API independently repeats its verification.
- Supply OS secure credential storage with **atomic save** and a single app-wide/cross-process exclusive lock. Share the store across every API caller; no module-global server credential cache. Task 10 owns this real adapter.
- Refresh only for actual API work/resume near expiry. Before POST, atomically persist `refreshUncertain:true`; after a valid same-subject reply, atomically replace the whole credential set and clear the marker. Discovery failure leaves credentials untouched. Transport failure, response loss, process death or post-response storage failure leaves the marker. Never automatically replay that refresh token; require fresh login to safely recover an uncertain rotation. This is not a claim the provider declared credentials invalid. Serialize refresh, login completion and logout against the same store.
- Logout attempts refresh-token revocation and always clears local credentials. Return `{revoked:false}` on failure/offline/unsupported discovery; do not claim remote signout. Native logout does not clear the system browser's managed session. Managed browser sign-out is separate. Already-issued JWTs remain usable until expiry; no immediate/global JWT revocation is promised.

`GET /v1/auth/session` retains `{userId}` after linked authentication. `POST /v1/auth/logout` now returns `{protocolVersion:2,managedSignOutRequired:true}`; the browser must call SDK `signOut()` to remove the managed session. `/v1/auth/callback` no longer consumes WorkOS authorization codes and returns `login_required`. Old WorkOS sealed bearer/cookie credentials require one-time login.

## Recovery and deliberate new accounts

The authoritative legacy source is WorkOS private-beta sandbox environment `environment_01M0Y347Z9N6FYNTGVY3725X1Y`, client `client_01M0Y37EQMGD270AW5W7NZC1E0`. The empty default Production client is wrong. Four old users include one with neither recorded Google nor GitHub profile; no unsupported-user loss is acceptable. Do not copy private exported profiles into this repository.

Task 13 must authenticate/reconcile the export against the restored old database and apply the reviewed exact-subject WorkOS backfill, preserving every internal UUID and all ownership/credit histories. The new API never joins by email or username and never creates a replacement account from a mapping miss.

`POST /v1/auth/migration/link` requires the **new** Neon Bearer token plus a JSON body containing exactly `{legacyAccessToken}`. It rejects extra `userId`, `issuer`, `email`, `confirmed-new`, or verification-method assertions. Both tokens must verify before any proof is written. Legacy JWT verification pins RS256, configured issuer, the exact private-beta client, user subject, session ID, issued-at and expiry; impersonation/agent-like tokens and sealed values fail. Obtain the actual source issuer privately and set `KENKUI_LEGACY_TOKEN_ISSUER`; there is deliberately no guessed default. JWKS is the public WorkOS `/sso/jwks/<exact-client>` endpoint. [WorkOS session-token contract](https://workos.com/docs/reference/authkit/session-tokens).

### Public recovery and admission

Both legacy clients hold sealed sessions. Explicit recovery uses a new signed Neon Bearer on the three POSTs below. All JSON bodies are strict, limited to 2 KiB and protected by the mutation Origin check. Ordinary session and resource requests return 409 `recovery_required` on a mapping miss. Studio should offer recovery first and explain that deliberate creation with a different mailbox makes a separate account.

| Path under /v1/auth | JSON input | Successful result |
|---|---|---|
| /migration/challenge | `{"legacyEmail":"..."}` | 202 `{challengeId}`. Display “If this address is eligible, check your inbox.” |
| /migration/verify | `{challengeId,code}` | 200 `{userId}`. After an interrupted response, check the normal session. |
| /onboarding | `{}` | 200 `{userId}`. |

The same-branch projection reads `neon_auth."user"` by verified subject UUID. Admission checks the verified Neon address against the active directory and deployed invitation policy. Returning owners can recover with a different or private current address. A disabled or missing current profile cannot recover. The JWT-only legacy link and migration-code verification share transaction guards. Proof creation, provider binding and proof consumption commit together, preserving the original internal UUID. No challenge supplies an auth session.

The protected Modal preflight `POST /migration-email/ready {environment}` checks the existing environment-specific SMTP configuration before mailbox lookup. Delivery uses `POST /migration-email {environment,challengeId,code}`, with the recipient resolved and claimed by Python. One HTTP attempt has a 1,500 ms deadline. Real and decoy 202s each wait at least 2,000 ms starting after readiness and the committed issuance decision, before optional delivery. A timeout can follow successful delivery. Only explicit resend after cooldown requests another code. Confirm authorized sending and receipt with an owner in Task 13.

### Offline directory release

Use the database owner as a distinct release role. Apply migrations and the exact WorkOS provider backfill before activating. Both legacy API environments independently used the private-beta environment and client above. Keep each target's private source, restored database, policy and release credentials isolated. For the selected target set `KENKUI_ENV` and the corresponding `KENKUI_STAGING_RELEASE_DATABASE_URL` or `KENKUI_PRODUCTION_RELEASE_DATABASE_URL` in a private environment. Use this command first with `--dry-run`, then with `--activate` after reconciliation.

```
python scripts/neon/import-legacy-directory.py --environment staging --source-file PRIVATE_EXPORT --attestation-file PRIVATE_ATTESTATION --dry-run
```

The export has top-level `capturedAt` (offset ISO timestamp), `authority:{environmentId,clientId}`, `source` and `result.userlandUsers` with `listMetadata:{before:null,after:null}` and a complete `data` array. Each row has string `id`, safe `email`, nullable offset timestamp `emailVerifiedAt` and boolean `banned`. Extra profile fields are ignored. Existing identities.email values record lookup-only evidence. Never use a preference address for delivery.

Privately attest `environment`, `sourceEnvironmentId`, `sourceClientId`, identical `capturedAt`, `complete:true`, independently verified integer `expectedSourceCount`, `sourceDigest` (lowercase SHA-256 hex of exact file bytes), `watermark`, `mode`, and `invitedEmails`. Verify the deployed `KENKUI_INVITED_EMAILS` with its owner. Explicit `[]` means confirmed open to verified users. A nonempty list restricts admission. Unknown policy blocks activation. A provisional run expires 24 hours after capture. For final mode additionally attest `admissionsFrozen:true`, `writesFrozen:true`, `finalWatermark` and `finalDigest` matching the frozen export. The final snapshot stays active until replaced. A pointer swap invalidates earlier challenges.

Apply the scoped role grants in `migration-runtime-grants.sql` with the actual non-owner Function and worker roles. Neither role may inherit release ownership, PUBLIC schema CREATE, or default directory DML. Check effective privileges and deployed Function access to the managed profile table before enabling this path. A database owner used at runtime defeats role separation. The release CLI prints aggregate counts and digest only. Task 11 calls `prune_migration_recovery(connection, *, batch_size=100)` from existing daily maintenance, bounded to at most 1,000 rows per call.

## Mandatory live gates

1. Actual Google and GitHub login/consent/logout in browser and native system browser; private GitHub email; same-person cross-provider linking only with proof; browser/native subject parity; four legacy UUIDs/credits/ownership unchanged; explicit new-account policy and legacy proof acquisition/recovery working.
2. Actual Tauri fixed-loopback listener, occupied port, error/denial/expired state/PKCE mismatch, callback issuer, wrong-branch JWT and API resource checks; secure cold restoration and refresh rotation; concurrent work, refresh timeout/crash and local logout; actual public-client revocation. No native parity claim until this runs.
3. Cookie behavior including Safari and redirects through ingress; login/consent CSP; actual signed `oauth_query` handled by the pinned SDK; provider methods read back as only approved Google/GitHub signup.
4. Real managed idle proof with all apps closed, idle visible, idle hidden including reconnect/cross-tab events, and JWKS-only requests. Observe externally without keepalive SQL, for a window long enough to catch provider background activity. Shared request-driven JWKS caching and zero app-added auth timers do not prove zero managed-service database queries. Provider-side idle wakeups remain a cost/acceptance gate.
5. Node 24 built-artifact smoke, external dependencies staged, protected ingress, coordinated Task 10 client update, reviewed environment config and real staging acceptance before production. Never silently deploy this artifact over old clients.
