# kenkui-server

Local-first FastAPI server for Kenkui.

The server owns durable local Jobs, source assets, execution, and output
artifacts. It exposes the versioned `/v1` API without hosted authentication,
providers, or billing.

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
uv run pytest tests/test_local_api.py tests/test_local_job_api.py tests/test_sqlite_repositories.py tests/test_transitions.py tests/test_openapi.py tests/test_main.py
```

Run the local server (it binds to `127.0.0.1` by default):

```sh
uv run kenkui-server
```

## License

Kenkui Server is licensed under [AGPL-3.0-only](LICENSE).
