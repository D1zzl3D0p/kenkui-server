from __future__ import annotations

from kenkui_server.storage.assets import FakeS3Client, R2AssetStore


def test_r2_store_keeps_private_keys_internal_and_deletes_idempotently() -> None:
    client = FakeS3Client()
    store = R2AssetStore(client, bucket="private-audio")

    store.put_source("source-1", b"epub")
    store.put_artifact("job-1", b"m4b")

    assert store.read_source("source-1") == b"epub"
    assert store.read_artifact("job-1") == b"m4b"
    assert all("source-1" not in key and "job-1" not in key for key in client.public_keys)

    store.delete_artifact("job-1")
    store.delete_artifact("job-1")
    assert not store.has_artifact("job-1")
