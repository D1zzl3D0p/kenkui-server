# Task 4 completion report — kenkui-server-v2

## Scope delivered

Created the local-only, versioned FastAPI server foundation. The public API
contains only `/v1/health`, `/v1/capabilities`, and versioned OpenAPI/docs
paths. It provides Pydantic capability and error DTOs, request-ID propagation,
structured request logging, normalized JSON error responses, a loopback Uvicorn
entry point, and a checked OpenAPI contract.

No durable jobs, assets, billing implementation, workers, authentication
providers, or audiobook-processing functionality was added.

## Changed files

- `.github/workflows/ci.yml` — Python 3.11–3.13 CI gates for focused tests,
  mypy, and Ruff.
- `.gitignore` — local Python artifact exclusions.
- `LICENSE` — complete GNU AGPL v3 license text.
- `README.md` — local-server scope, normative contract links, and development
  commands.
- `pyproject.toml` and `uv.lock` — Python 3.11–3.13 package/tooling setup;
  FastAPI, Pydantic, Uvicorn, pytest, Alembic, mypy, and Ruff configuration.
- `src/kenkui_server/__init__.py` — package marker.
- `src/kenkui_server/app.py` — app factory, `/v1` routes, request-ID middleware,
  normalized exception handlers, and structured request events.
- `src/kenkui_server/config.py` — loopback server configuration and capability
  DTOs.
- `src/kenkui_server/errors.py` — normalized error-envelope DTOs.
- `src/kenkui_server/main.py` — loopback Uvicorn runtime entry point.
- `src/kenkui_server/observability.py` — JSON event logging helpers.
- `openapi/v1.json` — checked OpenAPI 3.1 contract artifact.
- `tests/conftest.py` — reusable ASGI client fixture.
- `tests/test_health.py` — health and request-ID propagation contracts.
- `tests/test_capabilities.py` — local capability contract.
- `tests/test_errors.py` — missing-route, validation, and unhandled-error
  envelope contracts.
- `tests/test_observability.py` — structured success and failure log events.
- `tests/test_main.py` — loopback runtime default.
- `tests/test_openapi.py` — checked artifact parity and `/v1`-only contract.

## TDD red-test proof

Each implementation behavior was written as a test before the related
production change and observed failing:

| Contract | Focused red command | Observed failure before implementation |
| --- | --- | --- |
| Health route and request-ID propagation | `uv run pytest tests/test_health.py -q` | `ModuleNotFoundError: No module named 'kenkui_server'` |
| Capabilities route | `uv run pytest tests/test_capabilities.py -q` | `404 != 200` |
| Normalized HTTP, validation, and internal errors | `uv run pytest tests/test_errors.py -q` | Framework `detail` payload / plain `Internal Server Error`, rather than `error` envelope |
| Structured request logging | `uv run pytest tests/test_observability.py -q` | No captured log records (`IndexError`) |
| Loopback entry point | `uv run pytest tests/test_main.py -q` | `ModuleNotFoundError: No module named 'kenkui_server.main'` |
| Checked OpenAPI artifact | `uv run pytest tests/test_openapi.py -q` | `FileNotFoundError` for `openapi/v1.json` |
| Empty request-ID handling | `uv run pytest tests/test_health.py -q` | Empty `X-Request-ID` response header |

## Final verification

Focused contract tests were run after implementation:

```sh
uv run pytest tests/test_health.py tests/test_capabilities.py tests/test_errors.py tests/test_observability.py tests/test_main.py tests/test_openapi.py -q
```

Result: `10 passed in 0.06s`.

A live smoke test also launched `uv run kenkui-server`; Uvicorn reported
`http://127.0.0.1:8000`, and live requests returned `{"status":"ok"}` from
`/v1/health` plus the versioned local capability declaration from
`/v1/capabilities`.

Per task direction, formatter, linter, mypy, and project-wide test commands
were not run locally. The configured CI workflow runs mypy and Ruff.

## Commit

Implementation commit: `7bfdb00` (`feat: scaffold versioned kenkui server contract`).

## Self-review

- Confirmed the checked OpenAPI document equals the app-generated document and
  declares only health and capability paths under `/v1`.
- Confirmed all tested framework failures and unexpected application failures
  use the one `{ "error": { "code", "message", "requestId", "details" } }`
  envelope and expose the same request ID in the response header.
- Confirmed local defaults advertise unauthenticated, unmetered EPUB-to-M4B
  single-cast operation and bind the runtime to loopback.
- Confirmed no private `kenkui` modules or later-phase services are imported.

## Concerns

None.
