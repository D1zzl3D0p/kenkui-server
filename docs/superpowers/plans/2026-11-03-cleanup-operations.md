# Operations and Retirement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans, or superpowers:subagent-driven-development if the user selects delegation, to implement this plan task-by-task. Track steps with checkboxes.

**Goal:** Make source/deployment state explicit, accept the Neon replacement and retire legacy operations safely.

**Architecture:** Infra stores environment/release facts; server stores reusable release tools and acceptance contracts. Current operating docs link historical evidence rather than repeating mutable release facts.

**Tech Stack:** Existing release scripts, GitHub workflows, Neon/Modal/R2 and provider control-plane tools.

**Spec:** [Cleanup scope](../specs/2026-11-03-ecosystem-cleanup-design.md), [shared API design](../specs/2026-11-03-shared-api-architecture-design.md).

**Global Constraints:** All constraints and Review Focus in [the master plan](2026-11-03-ecosystem-cleanup.md) apply. Existing Python and Node runtime ranges and lockfiles are authoritative; do not substitute newer dependencies during cleanup. File paths below are relative to the named repository. Record a clean baseline, use a focused branch, and commit each task separately after verification.

## O1 — Record one compatible baseline

**Files:** infra `release.json`, `environments.json`, `README.md`, `STATUS.md`; create infra `docs/current-architecture.md`; server `deploy/neon/baseline.md`; existing server release-manifest tools in `scripts/neon/`.

**Interfaces:** Produces a compatible source set, selected feature matrix, deployed-state evidence pointers and ownership list consumed by every other task. `release.json` remains the machine-readable current release index; dated `releases/*.json` remain historical receipts. Do not introduce a competing manifest.

- [ ] Inspect current remote branches/PRs and authorized migration working copies. Record full source SHAs, dirty changes, branch owner, current deployed revisions and evidence timestamps; use provider reads where authorized, otherwise label live state unverified.
- [ ] List supported EPUB/PDF modes, chapter titles, voices, local/native operations, auth modes, public resource routes and billing semantics. Compare core main, migration fixtures and actual deployment; explicitly choose compatible feature revisions before modifications. Preserve unresolved discrepancies as blocked parity items.
- [ ] Update the existing release index and baseline references; distinguish intended target from observed deployment. Verify every referenced SHA with `rtk git cat-file -e <sha>^{commit}` in a checkout containing that revision. Use the migration's existing release-manifest verifier for receipts; do not invent another verifier.
- [ ] Verify JSON parsing with `rtk proxy python3 -m json.tool release.json` and `rtk proxy python3 -m json.tool environments.json`. Review every doc link and ensure no secret values appear. Commit the baseline/docs change.

## O2 — Close Neon acceptance and rollback gaps

**Files:** server `deploy/neon/acceptance.md`, `oauth-setup.md`, `rollback.md`, `maintenance.md`, evidence indexes; infra release records/runbooks. Preserve private evidence outside Git.

**Interfaces:** Consumes O1 source set. Produces an acceptance record identifying passed/failed/deferred checks, target resources, tested release hashes and rollback expiry criteria. O4 requires it.

- [ ] Read existing unchecked acceptance entries. Execute them using disposable local data or the existing staging environment as each check requires: actual browser/native Google/GitHub login and logout, account recovery, stable owner IDs/credits, private upload/inspection/cover, render/cancel/recovery, progress reconnect, artifact download and signed Stripe test replay. Reconcile newer features from O1. Never substitute mocked success for live acceptance.
- [ ] Rehearse independent encrypted backup restoration, auth/service configuration recovery and the same-auth standalone fallback against current Neon data. Prove post-cutover writes and identity/payment records survive fallback; preserve current tool receipts and hashes.
- [ ] Measure idle suspension and cold reconnect using control-plane observations without keepalive SQL. Record provider-side auth wakeups and actual caveats.
- [ ] Produce a concrete cutover report with exact releases, admission/write pause, database authority, ingress/DNS and worker changes, rollback and residual costs. Production changes follow the migration's existing review/authorization, not this cleanup task alone.
- [ ] Update the acceptance record only for observed results, commit non-secret evidence references, and record who can accept the rollout. Do not erase failed or deferred gates.

## O3 — Consolidate docs and make monitoring idle-aware

**Files:** infra `README.md`, `STATUS.md`, `runbooks/README.md`, `runbooks/production.md`, `.github/workflows/availability.yml`, `.github/workflows/backup.yml`, `scripts/dependencies.py`; server `deploy/neon/maintenance.md` and current setup docs.

**Interfaces:** O1 release index is authoritative. Produces one current setup/operations entry point and a monitoring schedule compatible with daily database maintenance.

- [ ] Move duplicated mutable release facts into references to the release index. Keep short current status plus dated historical receipts. Inventory links before moving archival docs; update links and add search exclusions for history to appropriate repo tooling, retaining deliberate access to history. Do not delete operational evidence merely to reduce checkout size.
- [ ] Separate public static/health/auth-boundary checks from database audits. Keep cheap public checks at the existing cadence only where they are DB-free; run database lease/settlement/capacity checks in the established daily maintenance window. Preserve active-job recovery signals and their existing responsiveness.
- [ ] Add `scripts/test_dependencies.py` using the standard library test runner only if needed to protect changed query scheduling. With injected fake database factory, assert idle/static checks open zero DB connections, daily audits run once per configured environment, overdue active work still alerts, and failures exit nonzero. Run `rtk proxy python3 -m unittest discover -s scripts -p 'test_*.py'`.
- [ ] Check modified workflow syntax with existing CI tooling or actionlint if available. Rehearse changed audits on synthetic state; after authorized rollout verify externally that idle endpoints leave compute suspended and backups still complete. Commit docs/monitoring together where dependent.

## O4 — Retire legacy providers and temporary paths

**Files:** server `deploy/render_blueprint.py`, legacy deployment docs/image workflow, `src/kenkui_server/auth/{browser,native,workos}.py`, `pyproject.toml`, `uv.lock`; migration recovery routes; Studio old keyring/auth support; infra old provider records.

**Interfaces:** Requires O2 acceptance, a retained compatible fallback, explicit rollback-period completion and a decision on remaining legacy user recovery. This task produces a reviewed deletion inventory; individual deletions follow it.

- [ ] Classify each reference as active local/self-hosting, hosted fallback, account recovery, historical evidence or genuinely obsolete. Verify runtime imports, release commands and active schedules. Python FastAPI is not obsolete while it serves local/self-hosted users.
- [ ] Prepare deletions of unused Render deployment automation and WorkOS runtime dependencies only once no supported composition needs them. Preserve historical migrations, identity mappings, ledger rows, receipts and backup tooling. Retain recovery support until a specific supported recovery policy replaces it.
- [ ] Remove exclusive tests and configuration with each retired path. Run remaining Python hosted/local suites, relevant TypeScript auth/release suites and web/native builds using A1 commands. Confirm rollback now targets the accepted Neon-auth-compatible artifact.
- [ ] Separately inventory paid provider resources and residual charges. Permanent deletion of named old databases needs explicit approval after restore evidence; code cleanup is not resource deletion authority. Commit the code/docs retirement and report any remaining cost.
