CREATE TABLE migration_directory_runs (
    id UUID PRIMARY KEY,
    environment TEXT NOT NULL CHECK (environment IN ('staging','production')),
    issuer TEXT NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    watermark TEXT NOT NULL,
    digest TEXT NOT NULL CHECK (digest ~ '^[a-f0-9]{64}$'),
    mode TEXT NOT NULL CHECK (mode IN ('provisional','final')),
    valid_until TIMESTAMPTZ,
    frozen BOOLEAN NOT NULL,
    invited_emails TEXT[] NOT NULL,
    restored_owners INTEGER NOT NULL CHECK (restored_owners >= 0),
    source_only INTEGER NOT NULL CHECK (source_only >= 0),
    CHECK ((mode='final' AND frozen AND valid_until IS NULL) OR
           (mode='provisional' AND valid_until IS NOT NULL AND valid_until <= captured_at + interval '24 hours')),
    UNIQUE (environment,id)
);
CREATE TABLE migration_directory_active (
    environment TEXT PRIMARY KEY,
    run_id UUID NOT NULL,
    FOREIGN KEY (environment,run_id) REFERENCES migration_directory_runs(environment,id)
);
CREATE TABLE migration_directory_rows (
    run_id UUID NOT NULL REFERENCES migration_directory_runs(id),
    subject TEXT NOT NULL,
    owner_id UUID REFERENCES identities(id),
    address TEXT NOT NULL,
    address_key TEXT NOT NULL,
    verified BOOLEAN NOT NULL,
    banned BOOLEAN NOT NULL,
    PRIMARY KEY (run_id,subject),
    UNIQUE (run_id,owner_id)
);
CREATE INDEX migration_source_address ON migration_directory_rows(run_id,address_key);
CREATE TABLE migration_directory_aliases (
    run_id UUID NOT NULL,
    owner_id UUID NOT NULL,
    address_key TEXT NOT NULL,
    PRIMARY KEY (run_id,owner_id,address_key),
    FOREIGN KEY (run_id,owner_id) REFERENCES migration_directory_rows(run_id,owner_id)
);
CREATE INDEX migration_alias_address ON migration_directory_aliases(run_id,address_key);
CREATE TABLE migration_challenges (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES migration_directory_runs(id),
    issuer TEXT NOT NULL,
    subject TEXT NOT NULL,
    old_subject TEXT NOT NULL,
    owner_id UUID NOT NULL REFERENCES identities(id),
    digest BYTEA NOT NULL CHECK (octet_length(digest)=32),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    expires_at TIMESTAMPTZ NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 5),
    claimed_at TIMESTAMPTZ,
    consumed_at TIMESTAMPTZ,
    superseded_at TIMESTAMPTZ,
    FOREIGN KEY (run_id,old_subject) REFERENCES migration_directory_rows(run_id,subject)
);
CREATE INDEX migration_pair ON migration_challenges(issuer,subject,owner_id);
CREATE INDEX migration_expiry ON migration_challenges(expires_at);
CREATE TABLE migration_rate_events (
    id BIGSERIAL PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('issue','owner','verify')),
    rate_key TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX migration_rate_window ON migration_rate_events(kind,rate_key,created_at);
CREATE INDEX migration_rate_expiry ON migration_rate_events(created_at);
REVOKE ALL ON migration_directory_runs,migration_directory_active,migration_directory_rows,
    migration_directory_aliases,migration_challenges,migration_rate_events FROM PUBLIC;
