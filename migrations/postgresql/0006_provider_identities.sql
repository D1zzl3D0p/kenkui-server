-- Compatible with existing Python workers and WorkOS rollback reads.
-- Keep every legacy subject and its UNIQUE constraint. Neon-only accounts have NULL.
ALTER TABLE identities ALTER COLUMN workos_subject DROP NOT NULL;

CREATE TABLE provider_identities (
    issuer TEXT NOT NULL CHECK (length(btrim(issuer)) > 0),
    subject TEXT NOT NULL CHECK (length(btrim(subject)) > 0),
    identity_id UUID NOT NULL REFERENCES identities(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (issuer, subject)
);
CREATE INDEX provider_identities_owner ON provider_identities (identity_id);

-- Only a server-verified legacy-session or completed one-use challenge flow may
-- INSERT these records. There is deliberately no public proof creation endpoint.
-- The challenge address must come from the verified old account, never the caller.
CREATE TABLE identity_link_proofs (
    id UUID PRIMARY KEY,
    identity_id UUID NOT NULL REFERENCES identities(id),
    issuer TEXT NOT NULL CHECK (length(btrim(issuer)) > 0),
    subject TEXT NOT NULL CHECK (length(btrim(subject)) > 0),
    verification_method TEXT NOT NULL CHECK (
        verification_method IN ('legacy_session', 'verified_legacy_email_challenge')
    ),
    verified_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    CHECK (expires_at > verified_at)
);
CREATE INDEX identity_link_proofs_owner ON identity_link_proofs (identity_id);
