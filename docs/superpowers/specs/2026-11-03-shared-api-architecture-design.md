# Shared Kenkui API and Python processing architecture

Status: proposed design for review; no implementation or production cutover is authorized by this document. Date: 2026-11-03.

## Intent and scope

Give Studio one stable API for local and hosted use and, if local packaging proves viable, one TypeScript implementation of its shared routes. Preserve Kenkui as a Python library, its existing Python API, local-first and self-hosted use, and the in-progress Neon migration. Reduce duplicate public validation and lifecycle behavior without sacrificing recovery, ownership, existing data, or audio correctness.

The shared TypeScript server is a target architecture, not a decision to discard the Python local server before the prototype succeeds. No new product capability, forced cloud dependency, pricing change, data deletion, or rewrite of Kenkui is in scope.

## Current baseline

GitHub main and the November 2 migration working copies are different baselines. The latter contain `hosted-api`, a TypeScript Neon API, `compute/api_bridge.py`, bounded authenticated Modal operations, and Python workers that read and write durable job state. The Studio development sidecar currently starts a Python local API and supervises its lifetime. Core main includes features, including PDF support, that may be newer than the migration's frozen EPUB-only baseline. Record exact SHAs, uncommitted changes, deployed revisions, and selected supported features before implementation; do not silently lose newer functionality or assume an archived working copy is deployed.

## Architecture

Studio (TypeScript UI, Rust desktop shell) communicates only with the public v1 API. A shared TypeScript API core validates HTTP, serializes responses, enforces access, and coordinates jobs. Composition selects a local or hosted set of ports. Kenkui and the worker remain Python and communicate through a small versioned processing contract.

Local: Tauri supervises the TypeScript API; it supervises the Python execution processes. The local adapters use existing local durable storage and filesystem semantics. Hosted: Neon Functions execute the same portable routes with hosted auth, PostgreSQL, R2, and a Modal dispatcher. Modal runs the Python worker. Public behavior is common where capabilities overlap; hosted identity, billing, quotas, and cloud execution are explicit capabilities, not pretend local implementations.

Proposed repository ownership: `kenkui` owns the Python library; `kenkui-server` owns TypeScript public API, protocol schemas, Python processing worker and both compositions; `kenkui-studio` owns UI and the Tauri supervisor. Existing Python local API remains supported during transition and until replacement acceptance. Build/package choices, exact repository paths, and runtime bundling are implementation decisions validated by the prototype.

## Authority and state

- The public API owns authorization, public request validation, idempotent admission, and initiating cancellation. Persist admission before dispatch. Hosted admission atomically reserves credits, creates the job and dispatch, and preserves existing recovery-signal ordering; local admission uses an equivalent durable transaction without billing.
- The durable repository is authoritative for job state, attempt ownership, cancellation requests, ordered events, and published artifact metadata. TypeScript and Python may both write only through explicitly specified guarded operations against that shared state. Local uses the existing SQLite-based store; hosted uses PostgreSQL. Do not rely on in-memory events or a live HTTP request for correctness.
- A worker claims a committed dispatch, receives an attempt/lease token, renews its lease and reports progress and terminal outcomes with fencing. It is the only party that executes a claimed attempt; each repository enforces guards on writes. Job status and its event commit atomically. Stale attempts cannot publish completion, settle credits or overwrite artifacts.
- Cancellation is a durable request, not proof that processing stopped. Queued jobs can become cancelled immediately; running jobs become cancel_requested until the worker acknowledges or recovery finalizes them. Retain current exactly-once settlement, dispatch idempotency, retry budget, and checkpoint semantics. Reuse existing tested Python repository/worker behavior first; move transaction ownership only as a separate, proven change.
- API streams replay durable ordered events after `Last-Event-ID`, drain terminal events before closing, and respect disconnection. Local notifications may wake readers but do not replace the durable log. Hosted polling is bounded and backs off; no idle database keepalive.

## Processing contract

Define versioned, bounded schemas and shared conformance examples for inspection, preflight, cover processing, start/claim execution, cancellation, progress, completion, and safe errors. Specify required/optional fields, UTF-8, maximum message and field sizes, integer precision, ordering, timeouts, compatibility, and version negotiation. Generate or validate types from a single schema where practical; canonical schema source and generator are a prototype decision. Public OpenAPI remains a separate user-facing contract; do not expose private protocol details.

Processing requests use server-owned opaque source IDs, validated options, and dispatch IDs. Local adapters resolve IDs to controlled files; hosted workers resolve owned references to R2. Arbitrary caller paths, object keys, URLs, and shell commands are never accepted. Large books and audio remain in storage; protocol messages carry references and metadata. Normalize error codes across adapters without exposing internals. Inspection and preflight use bounded request/response operations; render is durable and asynchronous.

Local transport: supervised Python subprocesses using versioned JSON lines on a dedicated control pipe, stderr for logs, bounded framing and explicit startup readiness. A broken pipe is not a terminal result: reconcile durable state and lease ownership. Do not treat a local process PID as durable job identity. Hosted transport: retain the existing authenticated, bounded Modal HTTP bridge for inspection, preflight, covers, and dispatch. A timeout or lost response is ambiguous; use committed dispatch IDs, idempotency and lease-based recovery rather than blindly spawning again. Authentication and environment checks apply before expensive work.

## Lifecycle, security, and portability

For desktop, Tauri starts the API on loopback with an OS-assigned port and a per-launch local credential; the server accepts only the expected app origin and credential. Avoid passing credentials in command-line arguments or logs. Keep listener ownership race-free and the readiness channel separate from logs. TypeScript supervises workers, while existing process-tree shutdown mechanisms must cover the API, Python worker, synthesis children and FFmpeg on POSIX and Windows. Explicitly preserve the current managed-desktop stop-on-close policy and standalone server's existing survive-shutdown policy unless separately changed. Define graceful deadlines, forced cleanup, parent death, orphan reaping, and restart behavior in the prototype. No detached process should outlive its intended owner.

Offline local use must not contact Neon or Modal. Keep credentials and user data in their existing appropriate stores. Hosted authorization must verify owner and environment on every resource operation, including the internal bridge; sign-in preserves stable internal owner IDs. Existing account linking, purchases, ledger history, R2 references, checkpoint retention, and rollback remain migration gates, not cleanup opportunities.

## Delivery and evaluation

1. Inventory the actual migration branches, deployed versions, pending PDF/TOC features, supported public routes, existing local database and desktop process policies. Freeze compatible baseline SHAs and contract examples. Record any discrepancies as blockers for parity, not silently chosen defaults.
2. Build a disposable local vertical slice: existing TypeScript API plus a local Python processing adapter, using a temporary data directory and synthetic inputs. Exercise EPUB inspection, preflight, a short real render, progress/reconnect, cancellation, worker crash, API restart, and no-network operation. Exercise the same job contract against the hosted adapter with synthetic data. This is a spike, not a production switch.
3. Demonstrate an installable desktop prototype on the first target platform, including both runtimes, FFmpeg and model provisioning. Check process cleanup and resource usage; identify the path for remaining supported platforms. Compare complexity and distribution size with the existing Python sidecar. Obtain representative startup and operational measurements, not assumptions.
4. Decide whether one shared TypeScript API is simpler in practice. If it requires a second full internal server, excessive packaging, or loss of recovery/self-hosting behavior, retain the Python local API and share the public contract instead. Preserve the migration work regardless.
5. If the prototype passes, implement parity incrementally: share route contracts and composition first; preserve existing job/repository invariants; migrate local ownership behind feature selection; run both implementations against the same public behavior suite and synthetic local fixtures. Switch desktop only after parity, packaging, security and lifecycle acceptance. Remove old code only after verified rollout and an explicit rollback window.

## Acceptance

Identical requests for overlapping capabilities produce compatible responses, errors, job transitions and resumable progress in local and hosted modes. Newer agreed features, supported documents and saved drafts remain functional. A local render succeeds offline; cancellation, process death, restart, and parent termination are exercised on target platforms. Hosted duplicate submissions and webhooks, concurrent admissions, lease loss, retries and settlements retain current guarantees on disposable PostgreSQL. Real staging validates Neon Auth, owner mapping, upload/inspection, render, cancel, recovery and download before cutover. A verified backup and write-safe rollback exist before production changes. No source removal is accepted solely on passing mocks or reduced line count.

## Decisions to validate

The split above, JSON-lines local transport, single shared public route implementation, and Python worker ownership are design recommendations. The protocol schema tool, Node packaging strategy, exact SQLite driver/transaction integration, worker process granularity, and deployment packaging remain open until the vertical slice. Specifically test whether Node and the existing Python SQLite worker can safely share migrations, transactions, and lease semantics; if not, evaluate a single local state owner before choosing a driver. Avoid duplicating billing and transition SQL by convenience. Preserve current release and migration safeguards.
