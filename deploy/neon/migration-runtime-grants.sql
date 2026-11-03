-- Run as the release owner with the target application schema on search_path.
-- psql -v ON_ERROR_STOP=1 -1 -v api_role=... -v worker_role=... -f migration-runtime-grants.sql
-- Clear direct grants inherited from table-creation defaults before adding read-only access.
REVOKE ALL ON migration_directory_quarantines FROM PUBLIC, :"api_role", :"worker_role";
REVOKE ALL ON migration_directory_active,migration_directory_runs,
    migration_directory_rows,migration_directory_aliases FROM PUBLIC, :"api_role", :"worker_role";
GRANT SELECT (id,issuer,environment,invited_emails,mode,frozen,valid_until)
    ON migration_directory_runs TO :"api_role";
GRANT SELECT (id,issuer,mode,frozen,valid_until) ON migration_directory_runs TO :"worker_role";
GRANT SELECT ON migration_directory_active,
    migration_directory_rows,migration_directory_aliases TO :"api_role", :"worker_role";
GRANT SELECT,INSERT,UPDATE ON migration_challenges TO :"api_role";
GRANT SELECT,INSERT ON migration_rate_events TO :"api_role";
GRANT USAGE ON SEQUENCE migration_rate_events_id_seq TO :"api_role";
GRANT SELECT,DELETE ON migration_challenges,migration_rate_events TO :"worker_role";
GRANT UPDATE (claimed_at) ON migration_challenges TO :"worker_role";
-- SELECT ... FOR UPDATE SKIP LOCKED for bounded pruning also requires UPDATE privilege.
GRANT UPDATE (id) ON migration_rate_events TO :"worker_role";
GRANT USAGE ON SCHEMA neon_auth TO :"api_role";
GRANT SELECT (id,email,"emailVerified",banned) ON neon_auth."user" TO :"api_role";

-- Check effective grants both as the runtime role and after every permitted SET ROLE.
-- Compare run-column access to the original runtime role's allowlist throughout.
-- A zero denominator means the role grants must be reconciled.
WITH runtime(role_name) AS (VALUES (:'api_role'::name), (:'worker_role'::name)),
reachable(role_name, candidate_oid) AS (
    SELECT runtime.role_name,candidate.oid FROM runtime
    JOIN pg_roles candidate ON pg_has_role(runtime.role_name,candidate.oid,'SET')
),
directory(relation) AS (VALUES
    ('migration_directory_runs'::regclass), ('migration_directory_quarantines'::regclass),
    ('migration_directory_rows'::regclass), ('migration_directory_aliases'::regclass),
    ('migration_directory_active'::regclass))
SELECT 1 / (:'api_role'::name <> :'worker_role'::name AND NOT EXISTS (
    SELECT FROM reachable CROSS JOIN directory
    WHERE has_table_privilege(candidate_oid, relation, 'DELETE,TRUNCATE,TRIGGER')
       OR has_any_column_privilege(candidate_oid, relation, 'INSERT,UPDATE,REFERENCES')
       OR (relation='migration_directory_quarantines'::regclass
           AND has_any_column_privilege(candidate_oid, relation, 'SELECT'))
       OR (relation='migration_directory_runs'::regclass AND EXISTS (
           SELECT FROM pg_attribute WHERE attrelid=relation AND attnum>0 AND NOT attisdropped
             AND attname NOT IN ('id','issuer','mode','frozen','valid_until')
             AND (role_name<>:'api_role'::name OR attname NOT IN ('environment','invited_emails'))
             AND has_column_privilege(candidate_oid, relation, attnum, 'SELECT')))
))::int AS runtime_grants_verified;
