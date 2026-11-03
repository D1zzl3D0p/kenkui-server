-- 0009's second added CHECK covers the complete run-level attestation.
ALTER TABLE migration_directory_runs
    DROP CONSTRAINT migration_directory_runs_check2,
    ADD CONSTRAINT migration_directory_runs_quarantine_metadata_check CHECK (
        (quarantined_owners=0 AND num_nonnulls(quarantine_version,quarantine_digest,
          approval_artifact_digest,approval_ref,attested_by,attested_at,restored_snapshot_digest)=0)
        OR (quarantined_owners>0 AND num_nonnulls(quarantine_version,quarantine_digest,
          approval_artifact_digest,approval_ref,attested_by,attested_at,restored_snapshot_digest)=7
          AND quarantine_version IN (1,2) AND quarantine_digest ~ '^[0-9a-f]{64}$'
          AND approval_artifact_digest ~ '^[0-9a-f]{64}$'
          AND restored_snapshot_digest ~ '^[0-9a-f]{64}$'
          AND length(btrim(approval_ref))>0 AND length(btrim(attested_by))>0));

ALTER TABLE migration_directory_quarantines
    ADD COLUMN finance_policy TEXT NOT NULL DEFAULT 'strict-v1',
    ADD COLUMN legacy_finance_evidence JSONB,
    ADD CONSTRAINT migration_directory_quarantines_finance_check CHECK (
        (finance_policy='strict-v1' AND legacy_finance_evidence IS NULL)
        OR (finance_policy='preserve-approved-unknown-legacy' AND legacy_finance_evidence IS NOT NULL
            AND jsonb_typeof(legacy_finance_evidence)='array' AND legacy_finance_evidence <> '[]'::jsonb));
