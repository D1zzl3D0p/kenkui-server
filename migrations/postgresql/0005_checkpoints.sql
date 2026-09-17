CREATE TABLE job_checkpoints (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    checkpoint_key TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes BIGINT NOT NULL CHECK (size_bytes > 0),
    metadata_json TEXT NOT NULL,
    ready BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX job_checkpoints_ready ON job_checkpoints(job_id, checkpoint_key) WHERE ready;
CREATE INDEX job_checkpoints_retention ON job_checkpoints(created_at);
