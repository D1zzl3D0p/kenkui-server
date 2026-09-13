ALTER TABLE assets ADD COLUMN created_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE assets ADD COLUMN deleted_at TIMESTAMPTZ;
ALTER TABLE jobs ADD COLUMN terminal_at TIMESTAMPTZ;
ALTER TABLE stored_objects ADD COLUMN resource_id TEXT;
CREATE UNIQUE INDEX stored_objects_resource_idx ON stored_objects(object_kind, resource_id);
CREATE INDEX jobs_source_idx ON jobs ((spec_json::jsonb ->> 'source_id'));
