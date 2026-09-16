-- Stop old API and worker writers before applying this migration and restart
-- both on the lot-aware release. Historical balances cannot establish pack usage.
LOCK TABLE credit_accounts, credit_authorizations, credit_ledger_entries IN ACCESS EXCLUSIVE MODE;

CREATE TABLE credit_lots (
    id TEXT PRIMARY KEY,
    sequence BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE,
    account_id UUID NOT NULL REFERENCES credit_accounts(id),
    kind TEXT NOT NULL CHECK (kind IN ('purchase', 'grant', 'legacy')),
    reference TEXT NOT NULL UNIQUE,
    credited BIGINT NOT NULL CHECK (credited >= 0),
    reserved BIGINT NOT NULL DEFAULT 0 CHECK (reserved >= 0),
    consumed BIGINT NOT NULL DEFAULT 0 CHECK (consumed >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (reserved + consumed <= credited),
    UNIQUE (id, account_id)
);

ALTER TABLE credit_authorizations ADD CONSTRAINT credit_authorization_account_unique
    UNIQUE (id, account_id);

CREATE TABLE credit_allocations (
    authorization_id UUID NOT NULL,
    lot_id TEXT NOT NULL,
    account_id UUID NOT NULL,
    credits BIGINT NOT NULL CHECK (credits > 0),
    PRIMARY KEY (authorization_id, lot_id),
    FOREIGN KEY (authorization_id, account_id) REFERENCES credit_authorizations(id, account_id),
    FOREIGN KEY (lot_id, account_id) REFERENCES credit_lots(id, account_id)
);

CREATE INDEX credit_lots_account_sequence ON credit_lots (account_id, sequence);

INSERT INTO credit_lots (id, account_id, kind, reference, credited, reserved)
SELECT 'legacy:' || a.id, a.id, 'legacy', 'legacy:' || a.id,
    a.available_credits + COALESCE(SUM(c.credits), 0), COALESCE(SUM(c.credits), 0)
FROM credit_accounts a
LEFT JOIN credit_authorizations c ON c.account_id = a.id AND c.status = 'reserved'
GROUP BY a.id, a.available_credits;

INSERT INTO credit_allocations (authorization_id, lot_id, account_id, credits)
SELECT id, 'legacy:' || account_id, account_id, credits
FROM credit_authorizations WHERE status = 'reserved';
