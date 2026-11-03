# Studio Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans, or superpowers:subagent-driven-development if the user selects delegation, to implement this plan task-by-task. Track steps with checkboxes.

**Goal:** Remove disconnected UI and simplify persistent drafts, tracking and composition without changing user behavior.

**Architecture:** Keep the current React/Tauri host abstraction. Extract lifecycle and persistence responsibilities into focused modules, leaving rendering and user actions in components.

**Tech Stack:** Existing TypeScript, React, Vitest, Playwright and Rust/Tauri.

**Spec:** [Cleanup scope](../specs/2026-11-03-ecosystem-cleanup-design.md), [shared API design](../specs/2026-11-03-shared-api-architecture-design.md).

**Global Constraints:** All constraints and Review Focus in [the master plan](2026-11-03-ecosystem-cleanup.md) apply. Existing Python and Node runtime ranges and lockfiles are authoritative; do not substitute newer dependencies during cleanup. File paths below are relative to the named repository. Record a clean baseline, use a focused branch, and commit each task separately after verification.

## S1 — Remove unreachable UI and exclusive tests

**Files:** candidates `src/pages/{job,jobs,new-job}.tsx`, `src/router.tsx`, `src/components/{casting,chapter-selection,file-upload,job-progress,output-settings,tts-settings,voice-select}.tsx`, `src/host/pkce.ts`, `src/studio/scene-preview.tsx`; `tests/casting.test.tsx`, `tests/pkce.test.ts`; `src/studio/style.css`.

**Interfaces:** Consumes O1 selected Studio revision; produces the same live entry points with disconnected modules removed. The candidate list is not an unconditional deletion list.

- [ ] Trace imports from `src/main.tsx`, both host entries and build/native configuration. Search dynamic imports, exports, examples and fixtures; record consumers for each candidate. Exclude declaration files and generated schemas from unused-code conclusions.
- [ ] Delete only proven disconnected modules, their exclusive tests, and selectors used only by deleted markup. Keep shared controls used by current Studio. Do not add tests checking that deleted files are absent.
- [ ] Run `rtk npm test`, `rtk npm run check:api`, `rtk npm run build:web`, `rtk npm run build:native`, and `rtk npm run test:e2e`. Expected: no missing imports, both builds pass, supported journeys unchanged. Commit as a deletion-only change.

## S2 — Normalize saved drafts once

**Files:** `src/studio/store.ts`; create `src/studio/draft-storage.ts`; add `tests/draft-storage.test.ts`; existing draft/request tests.

**Interfaces:** Retain exports `readLibrary(key): Library`, `requestFor(draft, support): JobRequest`, `changeDraft(draft, patch): Draft`. `draft-storage.ts` owns validation and version migration. Persist a versioned envelope while continuing to read the current legacy envelope; invalid entries cannot contaminate valid records.

- [ ] Pin old-draft fixtures: missing optional speech/pause/title fields retains the exact previous effective request; explicit false/zero survives; invalid/null entries are isolated; corrupt JSON returns safe empty state; quota failure remains visible. A no-op edit retains the idempotency key and a changed effective request replaces it. Run `rtk proxy npx vitest run tests/draft-storage.test.ts` and confirm the newly required normalization case fails before implementation.
- [ ] Extract normalization/load/save responsibilities and migrate once on loading. Preserve old defaults as explicit compatibility rules, not new-draft defaults. Preserve capability-based omission and current stored keys/account scoping; do not overwrite unrelated account data. Replace component-side optional compatibility branches only after normalized types guarantee them.
- [ ] Run the new suite plus `tests/app.test.tsx`, existing settings/casting tests and both builds. Exercise loading an old draft and submitting its unchanged request. Commit the persistence change independently.

## S3 — Give job tracking one owner and simplify UI composition

**Files:** `src/studio/job-view.tsx`, `src/api/events.ts`; create `src/studio/use-job-tracking.ts`; existing `tests/job-idle.test.tsx`, `tests/api-client.test.ts`, channel tests; `src/studio/studio.tsx`, `src/studio/composer.tsx`.

**Interfaces:** Proposed hook `useJobTracking({id, client, host})` returns `{job, error, connection, retry, stop}`. Reuse existing EventResponse/JobResponse/Host types. `connection` is connected/reconnecting/disconnected; the hook owns snapshots, cursor, stream and fallback lifetime; transport parser owns only frame delivery.

- [ ] Extend existing tracking tests only for missing behaviors: terminal snapshot stops all timers/streams; hide/resume never duplicates a subscription; old request responses cannot overwrite a newer job; repeated disconnect has a bounded retry budget; cancellation stays nonterminal until acknowledged. Run `rtk proxy npx vitest run tests/job-idle.test.tsx tests/api-client.test.ts tests/channel-event-source.test.ts` before extraction and after it.
- [ ] Extract tracking and remove overlapping retry ownership from component/transport. Preserve Last-Event-ID and ordered-event deduplication, no hidden-tab requests, auth-expiry behavior and download cancellation. Do not introduce a state-management dependency.
- [ ] Extract `useLibrary` to `src/studio/use-library.ts` only for persistent library mutations, using S2 storage; retain API job data as server authority. Split composer's existing view sections into `composer-chapters.tsx` and `composer-output.tsx` with explicit props and callbacks; keep one owner of draft changes/preflight fingerprints. Do not create a generic form framework.
- [ ] Run all Vitest tests, both builds and Playwright studio/capability journeys. Verify native resume/download/sidecar tests when their host wiring changed. Commit tracking and component extraction as separate reviewable changes if they touch independent behavior.
