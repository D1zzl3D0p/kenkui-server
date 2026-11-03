# Local boundary experiment (not a release)

Run using the migration's pinned core/Python environment and Node 24.21.0:

```sh
rtk proxy env KENKUI_SERVER_PYTHON=/absolute/server/.venv/bin/python node spikes/shared-local-api/smoke.mjs
```

The Python environment must import this server revision and the selected compatible core. The experiment uses only a bundled synthetic EPUB copied to a fresh temporary directory, fixture engine/assembler, stdin/stdout control, and no cloud calls. The Node Hono adapter wraps seven typed operations around the existing Python app in process; it deliberately tests whether retaining Python state ownership reduces duplication. Public auth, storage, billing, HTTP streaming, real synthesis and packaging parity are not implemented. Never expose this harness or install it as a server.

Logs use stderr and protocol messages use a dedicated stdout duplicate. Frames are bounded to 1 MiB and requests correlate by protocolVersion/requestId. Large content is a server-selected source file, not a path accepted in a message. EOF drains the TestClient and worker exit rejects pending requests. This is sequential RPC, not a production cancellation/control channel.

See report.md for measured outcomes, failures and the explicit decision to retain the Python local API.
