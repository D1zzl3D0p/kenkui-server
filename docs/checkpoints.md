# Hosted render checkpoints

Hosted workers persist completed chapter PCM and casting data in the existing
private R2 bucket. PostgreSQL migration `0005_checkpoints.sql` tracks immutable
objects and selects the committed version of each job checkpoint. Local workers
retain their existing behavior.

Every successful casting-store transaction creates a consistent SQLite backup.
This includes individual validated model responses, identity decisions, final
attribution, and casting. A replacement worker restores that job's private
casting store before resolving the pipeline. Attribution threads inherit the
job's checkpoint context explicitly; unrelated jobs never share it.

Each chapter is uploaded after all its segments and pauses have been rendered,
before reporting chapter completion. Keys include the semantic plan, selected
voice assets, engine configuration, and checkpoint schema. Downloads are checked
against the stored byte count and SHA-256, then chapter metadata is checked
against the current plan. Restored chapters do not enter the synthesis pool.
If all chapters exist, execution resumes assembly. Assembly itself may repeat;
this version does not checkpoint a partially encoded M4B.

Uploads are synchronous and bounded to one chapter at a time. This deliberately
keeps acknowledgement and durability together; background upload overlap is a
future optimization. No full-book audio buffer is introduced.

## Failure behavior

1. Register an uncommitted object under the current worker lease.
2. Upload to a new, opaque R2 key without holding a database transaction.
3. Recheck the lease and running job state, then atomically select the new
   checkpoint. An earlier committed version stays available until this succeeds.

A stale worker cannot commit. Partial uploads never become reusable. Storage
outages or corrupt payloads stop the attempt rather than silently repeating
paid synthesis. Existing three-attempt recovery and billing settlement remain
in force; checkpointing does not increase the retry budget.

Hourly retention removes uncommitted/superseded objects after 24 hours, successful
job checkpoints after 24 hours, and failed/cancelled job checkpoints after seven
days. Ready checkpoints for active jobs are retained regardless of age. The
seven-day policy preserves diagnostic/recovery material; it does not itself
enable retrying a terminal job under a new job ID.

## Rollout

Deploy matching core and server revisions. Apply the database migration before
deploying workers or retention. The database owner must grant the worker role
`SELECT, INSERT, UPDATE, DELETE` on `job_checkpoints` (or use existing equivalent
default privileges). The R2 credential must allow read/write/delete for the
`checkpoints/` prefix. No public URLs are issued for checkpoint objects.

Verify an interrupted two-chapter canary: the replacement must synthesize only
the missing chapter and publish the same output. Verify interruption immediately
before assembly starts zero synthesis workers on recovery. Do not interrupt an
old active render to roll this out: it has no durable checkpoints to recover.

This change cannot recover audio already lost by workers running older code.
Bump the chapter checkpoint schema if synthesis semantics change without changing
the plan/render schema or engine configuration.
