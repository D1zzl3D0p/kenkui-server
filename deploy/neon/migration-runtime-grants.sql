-- Run as the release owner with the target application schema on search_path.
-- psql -v api_role=... -v worker_role=... -f migration-runtime-grants.sql
-- Clear direct grants inherited from table-creation defaults before adding read-only access.
REVOKE ALL ON migration_directory_active,migration_directory_runs,
    migration_directory_rows,migration_directory_aliases FROM :"api_role", :"worker_role";
GRANT SELECT ON migration_directory_active,migration_directory_runs,
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
