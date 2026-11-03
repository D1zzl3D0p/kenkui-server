# API Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans, or superpowers:subagent-driven-development if the user selects delegation, to implement this plan task-by-task. Track steps with checkboxes.

**Goal:** Make contracts and verification authoritative, then determine whether the hosted TypeScript API can replace the local Python API with lower total complexity.

**Architecture:** Reuse the Neon API and Python worker. Prototype a local composition with the existing durable state semantics; keep a shared public contract even if one implementation is deferred. Production replacement gets a dedicated evidence-based plan after the prototype.

**Tech Stack:** Existing Node 24.21.0 migration runtime, Hono, Python worker, SQLite/PostgreSQL, Vitest/pytest, Tauri and current packaging tools.

**Spec:** [Cleanup scope](../specs/2026-11-03-ecosystem-cleanup-design.md), [shared API design](../specs/2026-11-03-shared-api-architecture-design.md).

**Global Constraints:** All constraints and Review Focus in [the master plan](2026-11-03-ecosystem-cleanup.md) apply. Existing Python and Node runtime ranges and lockfiles are authoritative; do not substitute newer dependencies during cleanup. File paths below are relative to the named repository. Record a clean baseline, use a focused branch, and commit each task separately after verification.

## A1 — Define contract authority and coherent verification

**Files:** server `scripts/verify-workspace.py`, `.github/workflows/{ci,neon}.yml`, `hosted-api/package.json`, `README.md`, `contracts/neon/{baseline.json,route-matrix.md}`; create `contracts/README.md`; Studio `scripts/api-types.mjs`.

**Interfaces:** `verify-workspace.py --tier fast|integration|release --component all|server|hosted-api|studio`. Fast runs local deterministic checks; integration requires a disposable DB and browser fixtures; release composes required checks plus packaged artifact smoke. Missing prerequisites fail visibly for a requested tier; no silent skips counted as acceptance.

- [ ] Document rule ownership: public route schema and errors, processing options/defaults, canonical job encoding, pricing, financial transactions and events. Identify concrete duplicate Python/TypeScript definitions. Public schema source is the selected versioned OpenAPI artifact; processing semantics remain Python-authoritative until a reviewed move. Preserve existing wire fixtures during migration.
- [ ] Add `tests/test_verify_workspace.py` around command selection with subprocess mocked: fast opens no provider/DB connection; requested integration fails clearly without disposable DB config; all-component includes hosted typecheck/test/build and Studio checks; return nonzero on a failed required command. Run the focused pytest file before implementing the tier selector.
- [ ] Make local and CI verification call the same component commands where practical; retain dedicated Neon release checks, pinned tools and actual integration DB guards. No forced relocation of all CI into one giant job. Run `rtk proxy python scripts/verify-workspace.py --tier fast --component all` on the O1 sibling checkout; run integration with the existing synthetic harness and inspect skips/failures.
- [ ] Add contract vectors to existing route/request suites for O1 newer features, invalid/unauthorized requests, exact monetary integer handling and ownership. Use equivalent public behavior assertions against both APIs where they overlap; declare environment capabilities explicitly. Regenerate schema/types only for deliberate contract changes and run both OpenAPI/type-generation check commands.
- [ ] Identify Pydantic-specific error wording/coercions that have no actual supported client reliance. Record proposed contract simplifications; do not alter v1 compatibility during acceptance. Apply only versioned or proven-compatible reductions after O2 and rerun contract vectors. Commit verification and subsequent contract changes independently.

## A2 — Build the disposable local shared-server experiment

**Files:** server proposed `spikes/shared-local-api/{README.md,local.ts,protocol.schema.json,worker.py,report.md}`, `hosted-api/src/app.ts` and route registrations only if a minimal composition seam is required; reuse existing Python worker/repos and local job fixtures. Studio development-only Tauri launch configuration as required by the packaged experiment. Keep all spike code clearly non-release.

**Interfaces:** Versioned JSON-lines control channel; stderr for logs. Operations `inspect(sourceId)`, `preflight(sourceId, options)`, `start(dispatchId)`, `cancel(jobId)` resolve only server-managed IDs. Every request has protocolVersion and requestId; responses correlate them; errors expose stable code/message. Render acknowledgement is not completion. Large files/audio stay in server-owned storage.

- [ ] Reuse the existing TypeScript app and route registrars through injected local services. Keep Python as the initial owner of local SQLite mutations using existing repositories, including admission, cancellation and guarded worker transitions. This avoids a simultaneous SQLite schema/transaction rewrite. Keep the bridge limited to actual local repository/processing operations and count its scope in the adoption assessment.
- [ ] Define protocol v1 schema, bounded messages, startup readiness, cancellation and clean EOF handling. Start with a 1 MiB frame ceiling for metadata/control, reject oversized frames, transfer file content by reference, and verify legitimate maximum book metadata; do not truncate or change supported books silently. Local subprocess arguments and IDs cannot contain arbitrary shell commands or caller paths. Hosted keeps its existing bounded Modal HTTP transport.
- [ ] Implement the smallest local composition through a supervised Python subprocess. Preserve no-network single-narrator operation, local auth/loopback protection, ordered durable progress and private data paths. Let Python claim committed dispatches; timeouts reconcile durable state instead of duplicating work. API and worker crashes must not strand a forever-running job.
- [ ] Exercise actual short-render success, inspect/preflight, cancellation while queued/running, disconnected progress resume, API death, worker death, stale attempt completion, closed parent pipe and FFmpeg child cleanup. Use temporary data and existing fixtures; retain only tests needed to understand prototype behavior. Do not port the full test suite into spike-specific mocks.
- [ ] Produce an installable development artifact on the first supported desktop target with Node, Python, FFmpeg, model provisioning and a per-launch local credential. Record startup/readiness, installed size, RSS, process tree, offline behavior and cleanup; compare with the existing Python sidecar on the same machine/input. List platform gaps for macOS/Linux/Windows and signing/update prerequisites. No universal performance claim follows one-platform evidence.
- [ ] Write `report.md`: bridge operation count, duplicated rules/SQL, packaging/lifecycle cost, parity results, remaining defects and recommended adopt/defer decision. Include concrete fallback and rollback. No production traffic or provider cutover is part of this task.

## A3 — Decide adoption and produce the implementation handoff

**Files:** A2 `report.md`; shared API architecture design; server/Studio native packaging docs; new conditional `docs/superpowers/plans/<date>-shared-local-api-rollout.md` if adopting.

**Interfaces:** Consumes A1 contract suite and A2 measured result; produces an explicit architecture decision and a fully scoped production rollout plan, or a documented deferral with two implementations sharing one contract.

- [ ] Adopt only if representative local behavior and first-platform packaging pass, financial/recovery authority is unambiguous, and the bridge avoids duplicating the current server's business rules. If the internal Python API is effectively a second public API with all the same complexity, prefer the contract-only fallback or a narrower redesign.
- [ ] If adopting, write the next implementation plan using proven adapter interfaces. Include all supported routes/capabilities, storage migration/rollback, platform packaging/process/auth tests, release provenance, Python sidecar entry-point relocation from Studio into Server, dual-run parity using synthetic separate databases, feature-selected desktop rollout and old API removal criteria. Do not dual-submit real jobs or charge twice.
- [ ] If deferring, preserve the current local API and public contract checks, retain useful non-spike contract improvements and document the measured reason. Remove throwaway packaging and spike-only dependencies once evidence is retained.
- [ ] Commit the decision and handoff. The user reviews the concrete replacement plan before production implementation. No wholesale Python server or Kenkui rewrite is implied by acceptance of housekeeping tasks.
