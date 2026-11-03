"""Default app state isolation must not move normally provisioned voice assets."""

from fastapi.testclient import TestClient
from kenkui._tts.production import default_cache_root

from kenkui_server.app import create_app

ORIGINAL_VOICE_CACHE = default_cache_root()


def test_default_state_is_isolated_without_redirecting_voice_cache(tmp_path):
    with TestClient(create_app(voices=())):
        assert (tmp_path / ".local/share/kenkui-server/server.sqlite3").is_file()
    assert default_cache_root() == ORIGINAL_VOICE_CACHE
