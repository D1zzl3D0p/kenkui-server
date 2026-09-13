"""Private local filesystem storage for uploaded sources and published artifacts."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Protocol


class AssetStore:
    """Own byte locations without exposing them through transport models."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._sources = self.root / "sources"
        self._artifacts = self.root / "artifacts"
        self._sources.mkdir(parents=True, exist_ok=True)
        self._artifacts.mkdir(parents=True, exist_ok=True)

    def put_source(self, asset_id: str, payload: bytes) -> Path:
        """Atomically persist a validated EPUB payload under its server ID."""
        destination = self._sources / f"{asset_id}.epub"
        temporary = destination.with_suffix(".upload")
        temporary.write_bytes(payload)
        temporary.replace(destination)
        return destination

    def source_path(self, asset_id: str) -> Path:
        """Return the private location for a persisted source asset."""
        return self._sources / f"{asset_id}.epub"

    @contextmanager
    def materialize_source(self, asset_id: str) -> Iterator[Path]:
        yield self.source_path(asset_id)

    def artifact_path(self, job_id: str) -> Path:
        """Return the private output location assigned to one job."""
        return self._artifacts / f"{job_id}.m4b"

    def read_artifact(self, job_id: str) -> bytes:
        """Read a published artifact only after its job has authorized access."""
        return self.artifact_path(job_id).read_bytes()


class S3Body(Protocol):
    """Streaming response body returned by production S3-compatible clients."""

    def read(self) -> bytes: ...

    def close(self) -> None: ...


class S3CompatibleClient(Protocol):
    """Subset shared by S3-compatible R2 clients."""

    def put_object(self, *, Bucket: str, Key: str, Body: bytes) -> None: ...

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, bytes | S3Body]: ...

    def delete_object(self, *, Bucket: str, Key: str) -> None: ...

    def upload_file(self, Filename: str, Bucket: str, Key: str) -> None: ...

    def download_file(self, Bucket: str, Key: str, Filename: str) -> None: ...

    def generate_presigned_url(
        self, ClientMethod: str, *, Params: dict[str, Any], ExpiresIn: int
    ) -> str: ...


class R2AssetStore:
    """Private R2-backed bytes addressed only by server-owned IDs."""

    def __init__(self, client: S3CompatibleClient, *, bucket: str, key_salt: str = "") -> None:
        self._client = client
        self._bucket = bucket
        self._key_salt = key_salt

    def put_source(self, asset_id: str, payload: bytes) -> None:
        self._put("source", asset_id, payload)

    @contextmanager
    def materialize_source(self, asset_id: str) -> Iterator[Path]:
        with TemporaryDirectory(prefix="kenkui-source-") as directory:
            source = Path(directory) / "source.epub"
            self._client.download_file(self._bucket, self._key("source", asset_id), str(source))
            yield source

    def put_artifact(self, job_id: str, payload: bytes) -> None:
        """Upload the worker's finalized bytes straight to object storage."""
        self._put("artifact", job_id, payload)

    def upload_artifact_file(self, identifier: str, path: Path) -> None:
        self._client.upload_file(str(path), self._bucket, self._key("artifact", identifier))

    def artifact_url(self, identifier: str, *, filename: str) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self._bucket,
                "Key": self._key("artifact", identifier),
                "ResponseContentDisposition": f'attachment; filename="{filename}"',
            },
            ExpiresIn=300,
        )

    def read_source(self, asset_id: str) -> bytes:
        return self._get("source", asset_id)

    def read_artifact(self, job_id: str) -> bytes:
        return self._get("artifact", job_id)

    def delete_source(self, asset_id: str) -> None:
        self._delete("source", asset_id)

    def delete_artifact(self, job_id: str) -> None:
        self._delete("artifact", job_id)

    def has_artifact(self, job_id: str) -> bool:
        try:
            self.read_artifact(job_id)
        except KeyError:
            return False
        return True

    def _put(self, kind: str, identifier: str, payload: bytes) -> None:
        self._client.put_object(Bucket=self._bucket, Key=self._key(kind, identifier), Body=payload)

    def _get(self, kind: str, identifier: str) -> bytes:
        body = self._client.get_object(Bucket=self._bucket, Key=self._key(kind, identifier))["Body"]
        if isinstance(body, bytes):
            return body
        try:
            return body.read()
        finally:
            body.close()

    def _delete(self, kind: str, identifier: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=self._key(kind, identifier))

    def _key(self, kind: str, identifier: str) -> str:
        digest = hashlib.sha256(f"{self._key_salt}:{kind}:{identifier}".encode()).hexdigest()
        return f"{kind}s/{digest}"


class FakeS3Client:
    """In-process S3-compatible fake used by hosted adapter tests."""

    def __init__(self) -> None:
        self._objects: dict[tuple[str, str], bytes] = {}

    @property
    def public_keys(self) -> tuple[str, ...]:
        return tuple(key for _, key in self._objects)

    def put_object(self, *, Bucket: str, Key: str, Body: bytes) -> None:
        self._objects[(Bucket, Key)] = Body

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, bytes]:
        try:
            return {"Body": self._objects[(Bucket, Key)]}
        except KeyError:
            raise KeyError(Key) from None

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        self._objects.pop((Bucket, Key), None)

    def upload_file(self, Filename: str, Bucket: str, Key: str) -> None:
        self.put_object(Bucket=Bucket, Key=Key, Body=Path(Filename).read_bytes())

    def download_file(self, Bucket: str, Key: str, Filename: str) -> None:
        Path(Filename).write_bytes(self.get_object(Bucket=Bucket, Key=Key)["Body"])
