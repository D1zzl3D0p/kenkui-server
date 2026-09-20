-- Completion notifications: a recipient, a preference, and an at-most-once send record.
ALTER TABLE identities ADD COLUMN email TEXT;

ALTER TABLE identities ADD COLUMN notify_by_email BOOLEAN NOT NULL DEFAULT true;

-- Claimed before a send is attempted, so a replayed worker attempt cannot mail twice.
CREATE TABLE job_notifications (
    job_id TEXT NOT NULL REFERENCES jobs(id),
    channel TEXT NOT NULL,
    claimed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivered_at TIMESTAMPTZ,
    PRIMARY KEY (job_id, channel)
);
