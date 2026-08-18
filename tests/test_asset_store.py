from pathlib import Path


def test_asset_store_keeps_source_and_artifact_paths_private(tmp_path: Path) -> None:
    from kenkui_server.storage.assets import AssetStore

    store = AssetStore(tmp_path / "private")

    source = store.put_source("asset-1", b"epub bytes")
    artifact = store.artifact_path("job-1")
    artifact.write_bytes(b"m4b bytes")

    assert source.read_bytes() == b"epub bytes"
    assert store.read_artifact("job-1") == b"m4b bytes"
    assert source.parent == tmp_path / "private" / "sources"
    assert artifact.parent == tmp_path / "private" / "artifacts"
