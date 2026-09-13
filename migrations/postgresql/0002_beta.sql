-- Preserve admission ordering while checking the job FK at transaction commit.
ALTER TABLE credit_authorizations ALTER CONSTRAINT credit_authorizations_job_id_fkey DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE execution_leases (dispatch_id TEXT PRIMARY KEY REFERENCES dispatches(id), token TEXT NOT NULL, expires_at DOUBLE PRECISION NOT NULL, attempts INTEGER NOT NULL CHECK(attempts > 0));
CREATE TABLE job_failures (job_id TEXT PRIMARY KEY REFERENCES jobs(id), code TEXT NOT NULL, message TEXT NOT NULL);
