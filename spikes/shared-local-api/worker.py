"""Throwaway local-boundary experiment. Never a production worker entry point."""
import argparse
import json
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    # Keep protocol traffic separate even when libraries/native children print.
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), "w", buffering=1)
    os.set_inheritable(protocol.fileno(), False)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    import kenkui as kk
    from fastapi.testclient import TestClient
    from kenkui_server.app import create_app

    app = create_app(data_dir=args.state, fixture_mode=True,
                     voices=(kk.Voice("narrator", "Narrator", True, "local", "test", True),))
    with TestClient(app) as client:
        protocol.write(json.dumps({"type": "ready", "protocolVersion": 1}) + "\n")
        for raw in iter(lambda: sys.stdin.buffer.readline(1048577), b""):
            request_id = None
            try:
                if len(raw) > 1048576:
                    raise ValueError("frame_limit")
                request = json.loads(raw)
                request_id = request["requestId"]
                if request.get("protocolVersion") != 1:
                    raise ValueError("protocol_version")
                op, value = request["operation"], request.get("input", {})
                routes = {
                    "inspect": ("GET", f"/v1/assets/{value.get('sourceId', '')}/book"),
                    "preflight": ("POST", "/v1/jobs/preflight"),
                    "start": ("POST", "/v1/jobs"),
                    "snapshot": ("GET", f"/v1/jobs/{value.get('jobId', '')}"),
                    "cancel": ("POST", f"/v1/jobs/{value.get('jobId', '')}/cancel"),
                    "events": ("GET", f"/v1/jobs/{value.get('jobId', '')}/events"),
                }
                if op == "upload":
                    response = client.post("/v1/assets", content=args.source.read_bytes(),
                                           headers={"Content-Type": "application/epub+zip"})
                elif op in routes:
                    method, path = routes[op]
                    if op in {"inspect", "snapshot", "cancel", "events"}:
                        from uuid import UUID
                        UUID(value["sourceId"] if op == "inspect" else value["jobId"])
                    response = client.request(method, path, json=value if op in {"preflight", "start"} else None)
                else:
                    raise ValueError("operation")
                try:
                    data = response.json()
                except ValueError:
                    data = response.text
                result = {"protocolVersion": 1, "requestId": request_id,
                          "status": response.status_code, "result": data}
                encoded = json.dumps(result)
                if len(encoded.encode()) > 1048576:
                    raise ValueError("response_limit")
            except (KeyError, ValueError, TypeError):
                encoded = json.dumps({"protocolVersion": 1, "requestId": request_id,
                                      "status": 400, "error": "invalid_operation"})
            protocol.write(encoded + "\n")


if __name__ == "__main__":
    main()
