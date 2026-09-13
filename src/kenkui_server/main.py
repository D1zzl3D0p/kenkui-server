"""Executable entry point for the local Kenkui server."""

import argparse
from pathlib import Path

import uvicorn

from kenkui_server.app import create_app
from kenkui_server.config import ServerConfig
from kenkui_server.observability import configure_logging


def create_uvicorn_config(server_config: ServerConfig | None = None) -> uvicorn.Config:
    """Build the Uvicorn configuration with its loopback-only default."""
    config = server_config or ServerConfig()
    return uvicorn.Config(
        create_app(
            web_build_path=config.web_build_path,
            data_dir=config.data_dir,
            model_allowlist=config.model_allowlist,
            max_jobs=config.max_jobs,
            render_workers=config.render_workers,
        ),
        host=config.host,
        port=config.port,
    )


def main() -> None:
    """Run the local ASGI server."""
    parser = argparse.ArgumentParser(description="Run the local Kenkui server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--web-build-path", type=Path)
    parser.add_argument("--model", action="append", default=[])
    parser.add_argument("--max-jobs", type=int, default=2)
    parser.add_argument("--render-workers", type=int, default=1)
    args = parser.parse_args()
    config = ServerConfig(
        host=args.host,
        port=args.port,
        data_dir=args.data_dir,
        web_build_path=args.web_build_path,
        model_allowlist=tuple(args.model),
        max_jobs=args.max_jobs,
        render_workers=args.render_workers,
    )
    configure_logging()
    uvicorn.Server(create_uvicorn_config(config)).run()
