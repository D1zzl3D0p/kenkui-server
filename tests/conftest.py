from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from kenkui_server.app import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client
