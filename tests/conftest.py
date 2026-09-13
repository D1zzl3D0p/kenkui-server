from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kenkui_server.app import create_app


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(data_dir=tmp_path / "server")) as test_client:
        yield test_client
