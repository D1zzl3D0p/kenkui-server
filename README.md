# kenkui-server

Local-first FastAPI server for Kenkui.

The server owns durable Jobs, source assets, execution, and output artifacts.
Local mode is unmetered and binds to loopback. The optional hosted composition
adds PostgreSQL, R2, AuthKit browser sessions, beta allowances and Modal workers.
See [the private beta release runbook](deploy/README.md) for configuration and
acceptance requirements.

## Local API behavior

- `GET /v1/jobs` returns an `{"items": [...]}` envelope of authoritative,
  local Job snapshots. Snapshots contain only the Job ID, status, and
  progress—never filesystem paths.
- Cancelling a queued Job immediately returns terminal `cancelled`. Cancelling
  a running Job returns non-terminal `cancel_requested`; the worker later
  publishes terminal `cancelled` after it observes the durable request.
- Configure `create_app(web_build_path=...)` (or pass the same
  `ServerConfig.web_build_path` to `create_uvicorn_config`) with an installed
  bundle containing `index.html` to serve its static assets and SPA fallback
  from the server origin. `/v1` requests always remain API requests.

## Normative contracts

- [Task 4 server-contract brief](.superpowers/sdd/2026-08-18-spec-completion/task-4-brief.md)
- [OpenAPI v1 contract](openapi/v1.json)

## Development

```sh
uv sync --all-groups
uv run pytest
uv run mypy
uv run ruff check src
```

Run the local server (it binds to `127.0.0.1` by default):

```sh
uv run kenkui-server
```

## License

Kenkui Server is licensed under [AGPL-3.0-only](LICENSE).

## Workspace verification

From a compatible sibling checkout, run `python scripts/verify-workspace.py --tier fast`
for deterministic Python/API/Studio checks without a database URL. `--component`
selects `server`, `hosted-api`, or `studio`; `--list` prints commands without running them.
Install frozen dependencies in each selected component first.

The default `integration` tier also runs PostgreSQL and browser journeys. It requires
the synthetic loopback fixture `postgresql://postgres@127.0.0.1:55438/postgres` in
`KENKUI_TEST_DATABASE_URL`; it rejects other targets before running a command.
The `release` tier adds the existing built-artifact smoke. These tiers complement
the dedicated Neon workflow's archive, ingress, TLS and release-provenance checks;
they do not replace real staging acceptance. Integration skips remain visible in
test output and are not evidence of live provider acceptance.

Integration/release also requires `age`, `age-keygen`, `pg_dump`, and `pg_restore`
on PATH. Missing clients fail preflight rather than silently skipping the encrypted
backup/independent-restore check. CI installs age and PostgreSQL 17 clients; local
clients must be compatible with the disposable PostgreSQL 17 server. Real TTS remains
a separate opt-in requiring an explicitly provisioned voice.
