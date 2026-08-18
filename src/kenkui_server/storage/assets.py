"""Private local filesystem storage for uploaded sources and published artifacts."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol


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

    def artifact_path(self, job_id: str) -> Path:
        """Return the private output location assigned to one job."""
        return self._artifacts / f"{job_id}.m4b"

    def read_artifact(self, job_id: str) -> bytes:
        """Read a published artifact only after its job has authorized access."""
        return self.artifact_path(job_id).read_bytes()


class S3Body(Protocol):
    """Streaming response body returned by production S3-compatible clients."""

    def read(self) -> bytes: ...


class S3CompatibleClient(Protocol):
    """Subset shared by S3-compatible R2 clients."""

    def put_object(self, *, Bucket: str, Key: str, Body: bytes) -> None: ...

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, bytes | S3Body]: ...

    def delete_object(self, *, Bucket: str, Key: str) -> None: ...


class R2AssetStore:
    """Private R2-backed bytes addressed only by server-owned IDs."""

    def __init__(self, client: S3CompatibleClient, *, bucket: str, key_salt: str = "") -> None:
        self._client = client
        self._bucket = bucket
        self._key_salt = key_salt

    def put_source(self, asset_id: str, payload: bytes) -> None:
        self._put("source", asset_id, payload)

    def put_artifact(self, job_id: str, payload: bytes) -> None:
        """Upload the worker's finalized bytes straight to object storage."""
        self._put("artifact", job_id, payload)

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
        return body if isinstance(body, bytes) else body.read()

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
