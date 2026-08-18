# kenkui-server

Local-first FastAPI server for Kenkui.

This repository currently exposes only the versioned server foundation:
`/v1/health`, `/v1/capabilities`, and the checked OpenAPI contract. It does
not implement jobs, assets, billing, workers, authentication providers, or
audiobook processing.

## Normative contracts

- [Task 4 server-contract brief](.superpowers/sdd/2026-08-18-spec-completion/task-4-brief.md)
- [OpenAPI v1 contract](openapi/v1.json)

## Development

```sh
uv sync --all-groups
uv run pytest tests/test_health.py tests/test_capabilities.py tests/test_errors.py tests/test_openapi.py
uv run mypy
uv run ruff check .
```

Run the local server (it binds to `127.0.0.1` by default):

```sh
uv run kenkui-server
```

## License

Kenkui Server is licensed under [AGPL-3.0-only](LICENSE).
