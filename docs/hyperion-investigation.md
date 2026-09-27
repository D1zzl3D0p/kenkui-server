# Hyperion failure and diagnostic replay

Original production job: `dbe4cc04-0447-4382-b786-45276b2b1f2a`.
It failed on 2026-09-26 at 19:52:49 UTC after processing 14 attribution chapters.
The worker logged `attribution_incomplete`, then `write_cast` failed with
`sqlite3.IntegrityError: FOREIGN KEY constraint failed`: incomplete attribution
was returned without being persisted, but casting attempted to reference it.
The job's credit authorization was released.

The original model failure is not established. Retrieved logs omit model-call
INFO events and structured warning fields. The worker never configured the
application logger; the existing message-only formatter also omitted library
fields supplied through `LogRecord.extra`.

## Logging change

`configure_logging()` now gives the `kenkui` and `kenkui_server` namespaces
INFO handlers that write JSON to stdout, independently of host/root logging.
Repeated setup is idempotent. Model events retain their fields, existing HTTP
JSON events retain theirs, and worker exceptions retain tracebacks.
`execute_hosted()` initializes logging before starting the worker.

Library model failures now include model, attempt, elapsed time, exception type,
and numeric HTTP status when supplied by the provider SDK. Prompts, response
text, and provider exception messages are not added to these diagnostic events.
Retry behavior and the attribution/casting bug are unchanged.

Focused validation: 36 server tests and 21 model-boundary tests passed, with
lint, formatting, and type checks on changed implementation files. The focused core tests ran with
`--no-cov`; they do not establish whole-project coverage.

## Live replay (2026-09-27)

- Modal app: <https://modal.com/apps/d1zzl3d0p/production/ap-dwnmO3DPFCTZiYmKmzlsoC>
- Diagnostic run: `debaee17-1e5d-45aa-8256-5d15a74c1e22`.
- Started at 15:05 UTC; fresh attribution and casting, stopping before synthesis.
- Original persisted job spec, source, selected chapters, model, and voice settings.
- Baseline core: `d71e48f`; baseline server: `9fc72f7`, with logging instrumentation.
- Separate app and checkpoint volume; reads the production job and source but
  does not change its state, billing, checkpoints, or send completion mail.
- Diagnostic volume: `kenkui-hyperion-diagnostics-20260927`, environment `production`.
  Its run directory contains `events.jsonl`, `progress.json`, `casting-v1.snapshot`,
  and `result.json` once finished. Checkpoints contain private source-derived data.
- Local launch script and log: `/tmp/kenkui-hyperion-replay/replay.py` and
  `/tmp/kenkui-hyperion-replay/run.log`.

INFO events were verified in live Modal output, including successful OpenRouter
requests and chapter progress. All 14 discovery and attribution chapters
finished, and casting succeeded at 15:13 UTC in 523.01 seconds. No
`model_call_failed`, `model_response_invalid`, or `attribution_incomplete` event
occurred. The original crash was not reproduced. The slowest model request
took 326.406 seconds and succeeded. This does not establish the original
failure's cause or mean the foreign-key bug is fixed. No audio was synthesized.

One response to chapter
`ch-v1-fe958a414492277c96c81de0` contains only quote IDs 0–72 of 409 expected:
8 assigned, 65 unknown, 336 omitted. It passed structural JSON validation and
was checkpointed; this coverage defect is distinct from an exhausted model
request and does not by itself set the current `failed` flag. The regular
production worker deployment has not been changed by this diagnostic run.

The local Modal client printed `Timed out waiting for final app logs` after
receiving the successful function result. That is a log-stream shutdown message,
not a model timeout or replay failure; the durable `result.json` records success.

Read current logs:

```sh
.venv/bin/modal app logs ap-dwnmO3DPFCTZiYmKmzlsoC --env production --tail 100 --timestamps
```
