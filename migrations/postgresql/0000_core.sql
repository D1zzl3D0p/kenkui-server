-- Bootstrap the shared job schema before hosted ownership and billing.
CREATE TABLE assets (id TEXT PRIMARY KEY, path TEXT NOT NULL, sha256 TEXT NOT NULL, format TEXT NOT NULL);
CREATE TABLE inspections (source_id TEXT PRIMARY KEY REFERENCES assets(id), title TEXT NOT NULL, author TEXT NOT NULL, chapters_json TEXT NOT NULL);
CREATE TABLE jobs (id TEXT PRIMARY KEY, spec_json TEXT NOT NULL, status TEXT NOT NULL, version INTEGER NOT NULL CHECK(version >= 0), progress_json TEXT NOT NULL);
CREATE TABLE job_events (job_id TEXT NOT NULL REFERENCES jobs(id), sequence INTEGER NOT NULL CHECK(sequence > 0), event_type TEXT NOT NULL, progress_json TEXT NOT NULL, PRIMARY KEY(job_id, sequence));
CREATE TABLE dispatches (id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), status TEXT NOT NULL, version INTEGER NOT NULL CHECK(version >= 0));
CREATE TABLE artifacts (id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), path TEXT NOT NULL, format TEXT NOT NULL);
