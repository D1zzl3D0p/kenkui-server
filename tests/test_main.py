from kenkui_server.main import create_uvicorn_config


def test_server_runtime_defaults_to_loopback() -> None:
    config = create_uvicorn_config()

    assert config.host == "127.0.0.1"
    assert config.port == 8000
