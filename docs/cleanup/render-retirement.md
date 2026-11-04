# Render retirement and post-Neon cleanup

Continuation of the approved ecosystem cleanup, November 3, 2026. The owner's
request is to retire Render, reconcile the settled Neon updates, and reconsider
the original simplicity goals.

## Scope and decisions

1. Combine the backed-up cleanup with `release/neon-production-promotion`,
   `release/neon-production-ui`, and the three newer committed migration changes.
   Preserve the original cleanup branches. Never change the active migration
   checkouts. The selected hosted core remains `97cba6b`.
2. Delete the obsolete Render Blueprint generator, its exclusive tests, the
   Python image workflow and its exclusive Dockerfile. Remove the rejected local
   adapter spike from the active tree; retain its report/code on the original
   backed-up cleanup branch. Archive obsolete setup
   instructions; retain migration evidence and WorkOS identity recovery. Keep the
   Python local/self-hosted API and the hash-verified TypeScript standalone
   fallback, which can run on an approved Node/container host without Render.
3. Update Infra's current environment inventory and operating entry points for
   the single prelaunch deployment. Public production uses the existing
   `divine-boat-06396016` / `br-gentle-thunder-bbo796nz` Neon branch, while internal
   Modal/R2 names remain staging. Remove legacy scheduled backup and frequent
   audit paths. Monitoring is manual while the prelaunch deployment intentionally
   disables scheduled work; backup/recovery acceptance remains explicit.
4. Run existing Python, hosted TypeScript, ingress and Studio checks after
   reconciliation. Protect the changed audit boundary with a regression proving
   it opens only the selected database and inspects the retained internal backup
   namespace. Review the resulting changes and back up the review branches.

## Live observations

The Render connector reports all three Kenkui API services and both databases
suspended. Neon lists Function `kenkui`, active deployment 3, on the reused branch
above. The separate production-named Neon project has no Function; it is not the
selected public production authority. These are control-plane observations, not
proof of browser/native login, payment acceptance, restoration, or idle billing.

No database, bucket, identity, ledger row, or provider resource is deleted by
this source cleanup. Permanent named database deletion remains a separate action.
The prelaunch promotion explicitly excludes further data-copy/DR rehearsals; this
does not retroactively mark the historical migration acceptance checklist passed.

## Progress

- Source reconciliation: promotion branches and newer migration commits merged
  into new `cleanup/neon-render-retirement-20261103` branches without conflict.
- Source retirement and fresh verification complete. Independent review found no
  critical issue; its two important runbook gaps were corrected: explicitly select
  Neon worker/backup overlays, and preserve internal routing/account policy in
  the standalone fallback. GitHub backup is the final source step.
- Provider resources remain suspended and retained pending explicit named deletion.
  Active default-branch Infra availability/backup workflows remain enabled:
  automatic approval review rejected disabling them without explicit approval
  because it removes operational coverage. Their branch replacements are manual;
  this source change alone does not stop default-branch timers.

## Remaining simplicity goals

Prefer one current deployment inventory and short entry points over duplicate
runbooks. Keep shared wire contracts between the Python local and TypeScript
hosted APIs; the previous adapter spike did not justify an extra desktop runtime.
Keep core Python and its feature pin explicit. Do not remove behavioral tests,
account recovery or durable state merely to reduce line counts. Further work
should target duplicate contract fixtures, WorkOS-only runtime composition after
its recovery policy closes, and dependency boundaries after a measured feature
release reconciles newer core features.

## Fresh verification

- Python Server: 431 passed, one skipped provisioned-voice test. Mypy, Ruff and
  generated OpenAPI checks pass. Encrypted backup/decrypt/independent restore ran
  against synthetic disposable PostgreSQL databases.
- Hosted TypeScript: 251 passed; 12 tests skipped because their separate paired
  Docker locale rehearsal was not requested. Typecheck and build pass.
- Studio: 173 tests, five browser journeys, generated API check, web build and
  native frontend build pass with the promoted authority. This does not claim
  newly packaged platform installers or actual Google/GitHub login.
- Ingress: 13 contract tests and two actual Worker/TCP runtime tests pass;
  generated types, typecheck and dry-run build pass.
- Neon release tools: 66 tests pass, including synthetic restore/inventory drift,
  archive authorization and verified same-auth fallback. Exact Function and
  standalone artifacts pass local PostgreSQL17 TLS/JWKS/JWT and embedded SDK proof.
- Infra: five tests pass, including the observed failing/passing selected-database
  regression. JSON, workflow YAML and embedded Python parse. The exact manual
  production availability script passed live web/API/CORS/unauthenticated checks.

The first release-test invocation lacked its explicit PostgreSQL binary directory;
the rerun passed after supplying it. The artifact fixture additionally needs the
server executable, not only libpq clients; it passed using the installed PostgreSQL
17.11 binaries. No production data was used. Git connectivity checks for Server
and Studio pass after exact historical object recovery.

Review boundaries: real browser/native identity and payments, actual emergency
host activation, live restore/custody, deployed schedule readback, provider costs
and permanent deletion are not established by these source/synthetic checks.
The source follows the existing no-schedule prelaunch decision, whose costs are
manual interrupted-job recovery, retention and backup. No claim is made that
normal production reliability or disaster recovery acceptance is complete.

## Revisited goals and next useful changes

| Priority | Opportunity | Concrete boundary / reason to defer implementation |
|---|---|---|
| Implemented in follow-up | Separate worker dependencies from legacy Python hosted auth | The [worker-extra follow-up](worker-dependencies.md) removes the SDK from image requirements while preserving legacy account recovery. A live image rebuild remains an operator step. |
| Next | Close the supported legacy recovery policy, then retire WorkOS-only composition | Normal hosted enrollment now supports `KENKUI_ACCOUNT_POLICY=neon`. `auth/recovery.ts`, Python hosted auth and frozen migration fixtures serve distinct compatibility/evidence purposes; do not delete them together without deciding which remains supported. |
| Next feature release | Reconcile newer core features with the hosted pin | Explicit EPUB/PDF/TOC capabilities and worker revisions are simpler than accidentally installing current core under older contract fixtures. Preserve the old pin until that release is tested. |
| Later | Break repository implementations into focused modules if changes require it | `storage/postgres.py` groups job, identity, asset, event, dispatch and billing repositories. Move whole repository classes behind existing exports; avoid a new generic storage abstraction or transaction-boundary changes. |
| Later | Continue extracting cohesive Studio form/state sections | Composer and Studio remain the largest handwritten UI modules. Use actual change pain to choose boundaries; generated `schema.ts` is not a manual refactor target. |

Keep the previously extracted draft storage, tracking hooks and planner identity
modules. No arbitrary test-count reduction or blanket coverage reduction is
needed. Exclusive tests disappeared with the retired Blueprint; behavioral
ownership, billing, stream, storage and recovery tests remain valuable.
