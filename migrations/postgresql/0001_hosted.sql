-- Hosted durable state.  Apply after the core job schema migration.
CREATE TABLE identities (
    id UUID PRIMARY KEY,
    workos_subject TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE credit_accounts (
    id UUID PRIMARY KEY,
    identity_id UUID NOT NULL REFERENCES identities(id),
    available_credits BIGINT NOT NULL DEFAULT 0 CHECK (available_credits >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE credit_authorizations (
    id UUID PRIMARY KEY,
    job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id),
    account_id UUID NOT NULL REFERENCES credit_accounts(id),
    credits INTEGER NOT NULL CHECK (credits > 0),
    status TEXT NOT NULL CHECK (status IN ('reserved', 'settled', 'released')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finalized_at TIMESTAMPTZ
);

CREATE TABLE credit_ledger_entries (
    id UUID PRIMARY KEY,
    account_id UUID NOT NULL REFERENCES credit_accounts(id),
    authorization_id UUID REFERENCES credit_authorizations(id),
    kind TEXT NOT NULL CHECK (kind IN ('purchase', 'reservation', 'settlement', 'release')),
    credits INTEGER NOT NULL,
    reference TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE payment_events (
    provider TEXT NOT NULL,
    provider_event_id TEXT NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (provider, provider_event_id)
);

CREATE TABLE stored_objects (
    id UUID PRIMARY KEY,
    object_kind TEXT NOT NULL CHECK (object_kind IN ('temporary', 'source', 'artifact')),
    opaque_object_key TEXT NOT NULL UNIQUE,
    terminal_at TIMESTAMPTZ NOT NULL,
    all_dependent_jobs_terminal BOOLEAN NOT NULL DEFAULT TRUE,
    deleted_at TIMESTAMPTZ
);

CREATE INDEX stored_objects_retention_idx
    ON stored_objects (object_kind, terminal_at)
    WHERE deleted_at IS NULL;
