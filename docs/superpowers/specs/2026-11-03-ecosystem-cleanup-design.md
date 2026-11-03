# Kenkui ecosystem cleanup scope

The user requested a plan implementing the ecosystem cleanup review. Optimize for fewer duplicate rules, easier navigation and verification, and less maintenance for people and agents. Preserve local/offline use, the existing Python library API, all selected document features, durable recovery, identities and financial history.

Scope: compatible revision inventory; Neon acceptance and eventual legacy retirement; removal of disconnected Studio code; behavior-focused tests and reusable fixtures; coherent verification commands; authoritative API contracts; a local shared-TypeScript-server prototype with an explicit adoption decision; unused dependencies and lint/formatting friction; current operational docs and idle-aware monitoring; focused decomposition of large core and UI modules.

The shared API target and constraints are specified in 2026-11-03-shared-api-architecture-design.md. A successful prototype is required before planning the production replacement in detail. No rewrite of Kenkui in TypeScript is proposed. Preserve the Python local server until a supported replacement is accepted.

Use small independently reviewable changes. No arbitrary line-count or test-count quota, no blanket coverage reduction, and no replacement tests that merely reproduce implementation. Static cleanup is verified with existing meaningful checks. Add a regression only when an important contract would otherwise be unprotected. Existing migrations, ownership mappings, ledger entries, recovery evidence and backup records are not disposable because their provider is retired.

Architecture implemented is distinct from staging accepted, production cut over, and rollback expired. Operational actions must be based on actual evidence and existing authorization. Permanent deletion of old databases requires explicit approval. Retaining a provider reference needed for recovery is compatible with cleanup.
