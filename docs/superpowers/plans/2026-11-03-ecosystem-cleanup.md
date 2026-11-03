# Kenkui Ecosystem Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans, or superpowers:subagent-driven-development if the user selects delegation, to implement this plan task-by-task. Track steps with checkboxes.

**Goal:** Reduce duplicate behavior, dead code, verification overhead and operational ambiguity across Kenkui, Server, Studio and infrastructure.

**Architecture:** Keep Kenkui processing in Python. Clean up the existing applications first, prove a shared TypeScript public API locally, and adopt it only if packaging and recovery are simpler in practice. Retire old provider paths only after verified replacement and rollback acceptance.

**Tech Stack:** Existing Python/uv/pytest/Ruff/mypy, TypeScript/Node/Vitest/React, Rust/Tauri, SQLite/PostgreSQL, Neon, Modal and R2. Preserve current locks and runtime pins unless a task explicitly changes them.

**Spec:** [Cleanup scope](../specs/2026-11-03-ecosystem-cleanup-design.md) and [shared API design](../specs/2026-11-03-shared-api-architecture-design.md).

## Global Constraints

- Preserve local/offline use, the existing Python library API, all selected document features, durable recovery, identities and financial history.
- No rewrite of Kenkui in TypeScript is proposed.
- No arbitrary line-count or test-count quota, no blanket coverage reduction, and no replacement tests that merely reproduce implementation.
- Preserve the Python local server until a supported replacement is accepted.
- Existing migrations, ownership mappings, ledger entries, recovery evidence and backup records are not disposable because their provider is retired.
- Permanent deletion of old databases requires explicit approval.
- Prefix all shell commands with `rtk`; examples below assume the named repository root. Inspect actual AGENTS.md instructions before execution. Use isolated branches/worktrees and preserve concurrent migration changes.
- Do not push directly to main. Use a separate reviewed change for each deliverable; follow existing publishing authorization when creating or merging PRs.

## Review Focus

1. A newer PDF or chapter-title feature disappears behind an older migration baseline: O1 inventories features; A1 exercises representative contract cases.
2. A UI cleanup changes saved drafts or idempotency: S2 pins old-draft and effective-request behavior.
3. A worker or API dies during cancellation/publication: A2 exercises process trees, recovery and fenced completion.
4. Recovery or rollback needs a supposedly obsolete identity/provider record: O2/O4 require evidence and retained compatible rollback.
5. Monitoring or tests silently wake production or omit integration checks: O3/A1 verify query behavior and selected test tiers.

## Plan structure and dependency order

| Workstream | Plan | Starts after | Delivers |
|---|---|---|---|
| Operations | [Operations and retirement](2026-11-03-cleanup-operations.md) | Start now | Baseline, accepted Neon release, current docs/monitoring, evidence-based retirement |
| Studio | [Studio cleanup](2026-11-03-cleanup-studio.md) | O1 baseline | Dead-code deletion and focused draft/job/UI modules |
| Core | [Core maintenance](2026-11-03-cleanup-core.md) | O1 baseline | Smaller tests/setup burden, dependency cleanup and focused planner |
| API | [API consolidation](2026-11-03-cleanup-api.md) | O1 baseline | Discoverable verification, contract authority, local shared-server experiment and adoption decision |

Recommended execution order: O1 → S1 → C1 → A1 → S2 → S3 → C2 → O3 → C3 → A2 → A3. O2 proceeds with the migration owner when staging is ready and is required before O4; no local refactor requires a production cutover. S1/C1 can proceed independently after O1. Do not delegate simultaneous edits to the same files.

## Baselines to refresh at execution

As observed during this review: core main e45039e; server main 1d63ab9; Studio main ea95155; infra main 6d9446b. Server migration `codex/neon-migration-20261103` was 63e92db. Studio migration working copy was 1671540; verify whether that exact revision is published and which branch owns it. These are source observations, not deployment claims. O1 replaces them with current full SHAs and records any dirty work.

## Completion and delivery

- [ ] Complete O1 and choose compatible source revisions before editing product code.
- [ ] Complete each independently reviewable deliverable in its owning repository, record relevant checks and commit it separately.
- [ ] Report deleted code/tests, preserved behavioral coverage, verification commands and remaining migration gates. Do not claim time/performance savings without measurements.
- [ ] Complete O2/O4 only with live acceptance and required authorization; pending acceptance is a visible outstanding task, not a reason to delete fallback code.
- [ ] Complete A2/A3 with a written adopt/defer decision. Adoption schedules a dedicated implementation plan based on prototype evidence; it is not bundled into housekeeping.
- [ ] Update the master checklist and current architecture map after accepted changes.

This document is a planning artifact. No implementation checks or live acceptance have been run as part of writing it.
