# Boundary experiment result and architecture decision

Decision: **defer replacement of the Python local API**. Keep one shared public contract and the current Python local API / TypeScript Neon API implementations. This is the fallback explicitly allowed by the design; no Kenkui rewrite or local API deletion follows the cleanup.

Observed on this macOS host, Node 24.21.0 and the pinned Python/core environment:

- Seven adapter operations: upload, inspect, preflight, start, snapshot, cancel and terminal event replay.
- The smoke completes a synthetic fixture render, preserves completed job state across worker restart, rejects an oversized outgoing frame and detects a killed worker. One observed Python startup/readiness was 214 ms; this is not a benchmark or service-level promise.
- The worker still constructs the whole Python FastAPI app and the Node adapter duplicates public route dispatch. It preserves all Python state authority rather than porting transactional semantics, but it does not demonstrate the intended smaller processing-only boundary. Existing hosted route types require session/bearer principal and PostgreSQL credit admission even though a local server is unmetered. Sharing the production route implementation requires deliberate local ports and optional hosted policy, not a thin wrapper.
- Initial startup failed because this migration source lacks `create_app(stop_workers_on_close=...)`, while the newer desktop/local server expects that policy. The experiment runs without that option only on completed fixture jobs; it provides no proof of active worker-tree shutdown. That mismatch is a release blocker, not resolved by this adapter.
- A real Pocket TTS render, active cancellation/lease-loss/publication races, true live SSE disconnect/resume, a standalone packaged runtime and platform/signing/update parity were not established. Existing product tests separately cover core/native audio, Python leases/transactions and Studio tracking; those do not turn this fixture harness into product acceptance.

## Why this is a defer decision

Shipping this adapter would add a TypeScript runtime/process and preserve the full Python API. That increases the moving parts without removing the duplicated contract implementation. Stop this direction before building a misleading installable product. Keep the reproducible experiment as non-release evidence; no spike dependency was added to production manifests. The existing tests/contracts and dead-code cleanup remain useful independently.

## Preconditions for reconsideration

Design local repository/storage/compute ports that do not tunnel the public Python HTTP API; separate hosted credit/account policy from shared request/response handling; reconcile the newer managed-desktop worker lifetime with the pinned migration. Select one local transaction/migration authority. Then prototype real speech, active cancellation, API/worker crashes, fenced artifact publication, Node/Python/FFmpeg provisioning and a first-platform package before writing a production rollout plan. The public Python library and existing local data remain supported during that work.
