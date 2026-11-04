# Ecosystem cleanup implementation status

This records the first cleanup pass. For reconciliation with the settled prelaunch
promotion and Render retirement, see [the continuation](render-retirement.md).

Implemented on isolated `cleanup/ecosystem-20261103` branches. Active migration checkouts were left unchanged. No branches were merged, no provider resources were written or deleted, and the live monitoring gate remains unchanged.

## Task disposition

| Task | Result |
|---|---|
| O1 baseline | Source SHAs, compatible hosted pin, newer-core feature differences and observed provider status recorded; deployment acceptance is explicitly separate |
| S1 unused UI | Removed 13 unreachable modules, two exclusive test files and exclusive preview CSS (523 lines in the deletion commit) |
| C1 tests/fixtures | Removed three incidental source/object-shape tests, narrowed prompt/provider secrecy assertion, shared equivalent engine-manifest construction while preserving scenario-local voice cases |
| A1 verification/contracts | Fast/integration/release CLI, disposable target guard, required restore tools, isolated app state, coherent CI commands and explicit contract authority; existing safety vectors retained |
| S2 drafts | Separate model/storage modules; legacy request/key/false/zero semantics preserved; versioned saves refuse future envelopes and surface storage errors |
| S3 tracking/composition | Extracted library/tracking hooks and composer sections; transport alone owns bounded SSE retries, snapshot fallback does not repeatedly create new streams; capability-aware E2E |
| C2 dependencies/lint | Removed Alembic plus greenlet/Mako/SQLAlchemy; regenerated unchanged-core lock; measured LiteLLM graph/files and documented retain decision; existing lint policy retained |
| O3 docs/monitoring | Current architecture entry points and historical-status banner; history search exclusions; gated daily Neon backup/failure audit, legacy frequent mode preserved. Deployment/idle observation pending O2 |
| C3 planner | Extracted immutable models and canonical identity; four pre-move fingerprint fixtures unchanged. Tightly coupled selection stays in planner to avoid a wider callback interface |
| A2 experiment | Reproducible non-release adapter smoke passes inspection/preflight/fixture render/restart/event replay/frame rejection/crash detection; real TTS, active-lifecycle and packaging acceptance unproven |
| A3 decision | Defer shared local API replacement; preserve Python local API + TypeScript hosted API with shared contracts. A wrapped full FastAPI app is not the intended simplification |
| O2 acceptance | Outstanding: no Functions listed on either observed Neon main branch; staging Auth enabled, production Auth disabled. Existing occupied-target, restore, coordinated transfer, actual login, overnight idle and production gates remain |
| O4 retirement | Outstanding and gated on O2/rollback/recovery policy. Retain WorkOS/Render runtime paths and all identity/ledger/migration evidence |

## Verified results

- Core: 1,944 passed, 12 optional skips, 73 deselected; branch coverage 92.52%, existing policy floor 90%. Seven real FFmpeg acceptance tests pass. Mypy 232 files, Ruff and formatting checks pass. One existing duplicate-ZIP fixture warning remains.
- Python Server: 407 passed, one skip for explicitly provisioned real voice. Encrypted full database backup, age decrypt and independent restore executed successfully using two synthetic disposable databases; it is no longer a missing-tool skip.
- Hosted TypeScript API: all 244 tests pass, including disposable PostgreSQL integration; typecheck and build pass.
- Studio: 173 tests pass; generated API check, web/native builds and five browser journeys pass. The same journeys also passed against the newer local-reference feature composition before the capability-aware expectation fix.
- Infra: five dependency-audit tests pass, including a two-hour-old failed job and refusal to accept fresh legacy reports as Neon backups. Workflow YAML and release/environment JSON parse; CI installer passes bash syntax check. Workflow execution and deployed monitoring observation remain rollout work.
- Local boundary experiment uses a deterministic engine and assembler, not real Pocket TTS; no model downloads or cloud calls were used.

## Review and decisions

An independent whole-change review found no critical issues. Important findings were fixed with observed failing/passing regressions and the full relevant suites: integration tool preflight, independent restore execution, daily failure window, correct Neon backup source, SSE retry session lifetime, capability-specific browser payloads, and voice-cache-safe app-state isolation.

Rulings made:

1. Use independent disposable clones rather than modify active migration checkouts (cost: explicit integration/handoff remains).
2. Use existing behavior checks for reversible deletion/refactors and no tests for human prose (cost: no artificial absence/source-spelling guards).
3. Keep hosted core97cba6b pinned; maintain current core separately (cost: newer PDF/TOC behavior still requires a feature-release reconciliation).
4. Keep distinct voice/provenance cases local but share their equivalent engine fixture (cost: some intentionally different fixture data remains).
5. Keep LiteLLM required after measuring its potential dependency savings (cost: current install footprint retained to preserve casting installations).
6. Keep pure selection in planner instead of adding a callback-heavy facade (cost: planner decomposition is narrower than the proposed three-file split).
7. Keep Python local API after the adapter experiment (cost: two public implementations still need contract parity; avoids unproven extra-runtime packaging/lifecycle).
8. Assert submitted chapter-title fields only when the server advertises them, while separately exercising the newer local composition (cost: release must still choose supported feature pins explicitly).
9. Keep all live migration/retirement gates rather than infer acceptance from code or synthetic tests (cost: O2/O4 remain outstanding).

Deferred minor: the rejected spike's response reader checks size after a complete line is buffered; inbound memory bounds for an unterminated response are unproven. This is documented in its README and must be fixed before any product use. No production endpoint uses the spike.

## Source commits before this status record

- kenkui: `1f8a22bbd7e94bbbe8f8f282715a042ff45e8e0f`
- kenkui-server: `cf8249ea20a9fd593daad3b83dc4bb7df753cef5`
- kenkui-studio: `919e008da9bd2d91811d300586711c0a8bca493c`
- kenkui-infra: `970ba223b159e6d7e55e97c6b203f694e13c32d8`

The repository branches contain separate commits for each cleanup deliverable and review fix. Preserve these branches and records until integration; do not merge the entire unpublished Studio migration into main as a side effect of reviewing only cleanup.
