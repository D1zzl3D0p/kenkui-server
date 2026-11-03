# Private evidence is stored outside Git

This directory contains instructions only. Never place actual exports, manifests, profiles, customer/credit rows, object-key inventories, dumps, age identities, provider settings, environment files, credentials, sessions or SMTP evidence in this checkout, even if ignored. Use the release operator's separately approved encrypted private evidence location with restricted directories/files and explicit retention/custody. `inventory.mjs` refuses a Git destination and writes new manifests exclusively with mode0600. Keep plaintext decrypt/restore material only in the approved offline isolated workspace; encrypt complete backups before shared R2 upload. No private age identity on services or in logs.

Retain a private chain for each environment/run: exact source authority/client and complete pagination; independent invitation/admission/write-freeze attestations; source capture/export bytes and SHA256; approved fixture artifact/provenance/digest; per-owner full reviewed inventory, classification fingerprints and exact canonical strict-v1/v2 finance evidence; source-before/source-after/restored-copy manifests; original-column post-DDL projection and complete target review/digest; exact migration IDs; R2 listing provenance/keys/content checks; full backup/encrypted archive digest, owner recipient verification/custody and independent decrypt/restore; managed-auth data/config restore; actual runtime/backup role/RLS/negative-grant evidence; directory dry-run and atomic activation results; deployed revisions/overlays/route/readbacks; owner-requested login/mail/visible-book/credit acceptance; overnight control-plane usage and billing readback.

Keep these meanings distinct:

| Evidence | Meaning |
| --- | --- |
| Source export SHA256 | Exact authenticated export bytes; does not approve fixture classification |
| Fixture fingerprint | Existing reviewed selective ownership/entitlement classification; unchanged v1 encoding, even in manifest v2 |
| `restoredSnapshotDigest` | Direct existing locked `kenkui-restored-target-v1` application-inventory digest consumed by importer; NULL before all required tables exist |
| Restore `fullDigest` | Complete discovered private table/schema/sequence/invariant/R2 inventory plus original-column projection; not an importer/quarantine manifest |
| Archive SHA256 | Exact encrypted backup bytes; requires actual decrypt/restore proof |
| Final freeze attestation | Independently verified release authority, freeze/watermark/source/target freshness; a hash or filename alone confers no authority |

The tools expose only counts/status/digests on stdout and fixed sanitized failures on stderr. Private manifests still include sensitive account diagnostics and R2 references, so never paste them into chat or a PR. Record a nonsensitive evidence reference in acceptance/report documents, not private paths containing identifying labels or evidence contents. Preserve inactive and active directory/quarantine history under the existing no-automatic-pruning policy. Failed permission checks, unreadable tables, changed fingerprints, unknown provenance and unverified provider writes are blockers to record, never reasons to omit evidence or regenerate approvals.
