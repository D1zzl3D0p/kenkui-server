ALTER TABLE migration_directory_runs
    ADD COLUMN matched_restored_owners INTEGER,
    ADD COLUMN quarantined_owners INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN quarantine_version INTEGER,
    ADD COLUMN quarantine_digest TEXT,
    ADD COLUMN approval_artifact_digest TEXT,
    ADD COLUMN approval_ref TEXT,
    ADD COLUMN attested_by TEXT,
    ADD COLUMN attested_at TIMESTAMPTZ,
    ADD COLUMN restored_snapshot_digest TEXT;
UPDATE migration_directory_runs SET matched_restored_owners=restored_owners;
ALTER TABLE migration_directory_runs ALTER COLUMN matched_restored_owners SET NOT NULL,
    ADD CHECK (matched_restored_owners>=0 AND quarantined_owners>=0
               AND restored_owners=matched_restored_owners+quarantined_owners),
    ADD CHECK ((quarantined_owners=0 AND num_nonnulls(quarantine_version,quarantine_digest,
          approval_artifact_digest,approval_ref,attested_by,attested_at,restored_snapshot_digest)=0)
        OR (quarantined_owners>0 AND num_nonnulls(quarantine_version,quarantine_digest,
          approval_artifact_digest,approval_ref,attested_by,attested_at,restored_snapshot_digest)=7
          AND quarantine_version=1 AND quarantine_digest ~ '^[0-9a-f]{64}$'
          AND approval_artifact_digest ~ '^[0-9a-f]{64}$'
          AND restored_snapshot_digest ~ '^[0-9a-f]{64}$'
          AND length(btrim(approval_ref))>0 AND length(btrim(attested_by))>0));
CREATE TABLE migration_directory_quarantines (
    run_id UUID NOT NULL REFERENCES migration_directory_runs(id),
    owner_id UUID NOT NULL REFERENCES identities(id),
    subject TEXT NOT NULL CHECK (length(btrim(subject))>0),
    fixture_fingerprint TEXT NOT NULL CHECK (fixture_fingerprint ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY (run_id,owner_id), UNIQUE (run_id,subject)
);
REVOKE ALL ON migration_directory_quarantines FROM PUBLIC;
