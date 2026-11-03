# Dependency, lint and contract decisions

## LiteLLM and lint

Measured from the unchanged current-core lock graph and existing local Python environment. This is a marker-insensitive dependency closure and installed-file census, not a fresh-install size or startup benchmark. Extra markers/platform alternatives can overstate a removable set.

Required-root graph: 90 packages. Omitting the direct LiteLLM root yields 59. Potentially unique names: 31; their installed files in this local environment occupy 121,899,684 bytes.

Keep LiteLLM required in this cleanup: automatic character casting remains a documented default installation capability. Optionalizing it is an installation/API-policy change, not a free dependency deletion; potential savings merit a separate reviewed packaging proposal with clean environment/platform measurements. Alembic was actually unused by Server's SQL migration authority, so it and three unique transitive packages were removed with a regenerated lock.

Most common explicit test lint suppressions: SLF001: 112, PLR2004: 47, D103: 35, PLC0415: 21, TC003: 11, D102: 7, PLR0913: 6, EM101: 5. These include test docstrings and deliberate private test seams; do not globally disable correctness checks to remove suppressions. Keep the existing policies and format changed files only. The narrow shared engine-manifest fixture now removes equivalent repeated setup while voice-specific provenance/variety fixtures remain local.

## Concrete validation compatibility inventory

| Existing duplication | Authority/evidence | Cleanup decision |
|---|---|---|
| Public snake/camel aliases, ignored extras, coercion and field errors | Frozen Python request oracle and `hosted-api/test/job-request.test.ts` | Preserve v1 until accepted clients and versioned compatibility change |
| Malformed JSON byte/codepoint offsets and Python wording | `hosted-api/src/routes/job-request.ts` + existing invalid-body vectors | Candidate to standardize to stable error code/field location in a later contract revision; no silent wording change now |
| Exact integer parsing/serialization | `billing/integers.ts`, Python canonical vectors and PostgreSQL monetary tests | Retain exact amounts, including beyond JS safe integer range |
| Canonical JobSpec defaults and ordering | Python job model/encoder and TypeScript `billing/job-spec.ts` | Preserve shared vectors; no parallel schema/default rewrite |
| Pricing/reservation/release/settlement | Authoritative transaction/recovery tests in disposable PostgreSQL | Preserve worker fencing/idempotency and exact-once financial effects |
| New chapter-title capability | Studio uses advertised capabilities; local-reference E2E supports it, migration baseline does not | Assert exact keys when supported and omit unsupported keys; both compositions exercised |
| PDF/layout support | Current core optional modules vs frozen EPUB-only hosted baseline | Keep explicit 415 on unsupported API; do not expose core features accidentally |

The existing cross-runtime request oracle and PostgreSQL vectors already protect the listed v1 safety cases. New regression work is concentrated on demonstrated gaps: capability-aware E2E, saved-draft version safety, bounded SSE session retries, restore prerequisite checks, isolated server state, and daily backup/failure monitoring. Operational acceptance and future versioned error simplification remain separate release decisions.
