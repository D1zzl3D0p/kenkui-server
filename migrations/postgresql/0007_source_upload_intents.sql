-- Complete source records stay invisible until object PUT and metadata commit.
-- Expired intents remain tombstones: a timed-out/crashed PUT may complete later.
ALTER TABLE assets ADD COLUMN next_source_cleanup_at TIMESTAMPTZ;
ALTER TABLE assets ADD COLUMN cover_operation_id UUID;
ALTER TABLE assets ADD COLUMN cover_generation INTEGER CHECK (cover_generation >= 0);
CREATE UNIQUE INDEX assets_live_cover_operation_idx ON assets(cover_operation_id)
    WHERE deleted_at IS NULL AND cover_operation_id IS NOT NULL;
CREATE INDEX assets_cover_generation_idx ON assets(cover_operation_id, cover_generation DESC)
    WHERE cover_operation_id IS NOT NULL;
CREATE INDEX assets_source_cleanup_idx ON assets ((COALESCE(next_source_cleanup_at,deleted_at)), id) WHERE deleted_at IS NOT NULL;
CREATE TABLE source_upload_intents (
    source_id TEXT PRIMARY KEY,
    owner_id UUID NOT NULL REFERENCES identities(id),
    sha256 TEXT CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    cover_operation_id UUID,
    cover_generation INTEGER CHECK (cover_generation >= 0),
    CHECK ((cover_operation_id IS NULL AND cover_generation IS NULL AND sha256 IS NOT NULL)
        OR (cover_operation_id IS NOT NULL AND cover_generation IS NOT NULL)),
    UNIQUE (cover_operation_id, cover_generation),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp() + interval '24 hours',
    cleanup_started BOOLEAN NOT NULL DEFAULT FALSE,
    next_cleanup_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp() + interval '24 hours'
);
CREATE INDEX source_upload_cleanup_idx ON source_upload_intents (next_cleanup_at, source_id)
    WHERE sha256 IS NOT NULL;
