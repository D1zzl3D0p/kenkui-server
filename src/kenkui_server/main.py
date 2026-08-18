"""Executable entry point for the local Kenkui server."""

import uvicorn

from kenkui_server.app import create_app
from kenkui_server.config import ServerConfig
from kenkui_server.observability import configure_logging


def create_uvicorn_config(server_config: ServerConfig | None = None) -> uvicorn.Config:
    """Build the Uvicorn configuration with its loopback-only default."""
    config = server_config or ServerConfig()
    return uvicorn.Config(
        create_app(web_build_path=config.web_build_path),
        host=config.host,
        port=config.port,
    )


def main() -> None:
    """Run the local ASGI server."""
    configure_logging()
    uvicorn.Server(create_uvicorn_config()).run()
