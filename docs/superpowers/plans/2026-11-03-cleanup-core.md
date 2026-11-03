# Core Maintenance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans, or superpowers:subagent-driven-development if the user selects delegation, to implement this plan task-by-task. Track steps with checkboxes.

**Goal:** Reduce fragile tests, dependency and lint overhead, and planner coupling while preserving the public Python API and generated audio semantics.

**Architecture:** Keep Python processing and current pure planning/effectful execution split. Move cohesive code without changing semantic fingerprints or persistence formats.

**Tech Stack:** Existing Python/uv, pytest, Ruff, mypy and native FFmpeg acceptance.

**Spec:** [Cleanup scope](../specs/2026-11-03-ecosystem-cleanup-design.md), [shared API design](../specs/2026-11-03-shared-api-architecture-design.md).

**Global Constraints:** All constraints and Review Focus in [the master plan](2026-11-03-ecosystem-cleanup.md) apply. Existing Python and Node runtime ranges and lockfiles are authoritative; do not substitute newer dependencies during cleanup. File paths below are relative to the named repository. Record a clean baseline, use a focused branch, and commit each task separately after verification.

## C1 — Remove incidental assertions and repeated fixture construction

**Files:** core `tests/test_process_execution.py`, `tests/test_import_boundaries.py`, `tests/conftest.py`, manifest/resolution tests, `tests/helpers.py`; production files only if an actual behavior gap is discovered.

**Interfaces:** Preserve genuine import boundaries, process isolation and bounded-output guarantees. Fixture builders are test-only and use existing production types; avoid a generic dependency-injection layer.

- [ ] Refresh the existing test-cleanup branches/worktrees first to avoid repeating completed work. Record default suite results, collected test count and durations using `rtk proxy uv run pytest --durations=15`; do not interpret count alone as value.
- [ ] For each source-text/old-name/object-shape assertion, identify the user or operational guarantee and an existing test that proves it. Remove assertions such as temporary-directory existence. Replace a broad source-spelling guard only if a meaningful missing behavior needs protection; keep intentional dependency/security boundaries.
- [ ] Consolidate repeated valid production manifest and voice/bindings fixtures into focused helpers; keep scenario-specific mutations local and cache isolation autouse. Parameterize cases only when setup/assertions are equivalent and retain descriptive case IDs.
- [ ] Run changed suites, then `rtk proxy uv run pytest`, `rtk proxy uv run mypy`, `rtk proxy uv run ruff check .`, `rtk proxy uv run ruff format --check .`. Preserve existing coverage policy unless a separate evidence-backed decision changes it. Commit test-only changes and report removed assertions plus retained behavioral cases.

## C2 — Remove unused dependencies and reduce lint noise deliberately

**Files:** server `pyproject.toml`, `uv.lock`; core `pyproject.toml`, `uv.lock`, `_characters/llm.py`, installation docs; selected lint configuration/docs.

**Interfaces:** Local/default and hosted package compositions remain installable. Optional LLM packaging is a decision backed by measured dependency differences, not assumed savings.

- [ ] Search imports, tools, release scripts and examples for Alembic. If no consumer exists in the O1 baseline, remove that dev dependency and regenerate only the relevant lock with `rtk proxy uv lock`; run migration tests and server lint/type checks. Do not replace the existing SQL migration system.
- [ ] Compare base installation with and without required LiteLLM using disposable environments and lock resolution. Record installed dependency/byte differences and how existing users install character casting. Decide explicitly whether a new `llm` extra merits the compatibility/documentation cost. Keep hosted/local casting compositions complete if adopted.
- [ ] If optionalized, add a focused missing-extra test asserting an actionable stable error before any provider call; retain a single-narrator installed-wheel smoke without LiteLLM and a casting smoke with the extra. Run package build/installed-wheel checks and existing LLM tests. Otherwise document the decision and keep the dependency.
- [ ] Inventory the most frequent lint suppressions and repeated formatting friction. Select explicit rule changes only where they simplify maintenance; keep correctness checks. Format only touched/new modules, avoiding a repository-wide reformat mixed with behavior changes. Run Ruff/mypy and commit dependency/lint decisions separately.

## C3 — Decompose planning without changing output identity

**Files:** core `src/kenkui/_domain/planning.py`; proposed `_domain/plan_models.py`, `_domain/plan_identity.py`, `_domain/plan_selection.py`; existing planning/selection/cache/announcement tests.

**Interfaces:** Keep `compile_execution_plan` and existing public/imported names compatible. `plan_models.py` contains immutable plan DTOs, `plan_identity.py` contains semantic fingerprint construction, `plan_selection.py` contains pure selection transforms. Preserve byte-for-byte canonical encoding and existing schema version strings for unchanged semantics.

- [ ] Map imports before moves and establish one-way dependencies: models → pure helpers → orchestration. Character and effectful modules must not become dependencies of pure domain modules. Do not add class hierarchies, provider registries or shell services.
- [ ] Select existing representative fixtures covering Unicode text, selected ranges, manual tuning, chapter titles, cache identity and voice assignments. Record unchanged plan/fingerprint results; add a small contract fixture only where existing assertions cannot detect accidental identity changes.
- [ ] Move one responsibility per commit, keep compatibility re-exports for internal consumers during migration, and eliminate cycles. Leave `pipeline.py`'s public fluent API intact; extract its validation/checkpoint logic only as a later focused change if it demonstrably reduces coupling.
- [ ] Run changed planning/selection tests, import-boundary tests, complete default suite and existing native FFmpeg acceptance tier with its documented opt-in environment. Run type/lint checks. Expected: identical plans/fingerprints for unchanged requests and no extra rendering/cache misses attributable to the move.
