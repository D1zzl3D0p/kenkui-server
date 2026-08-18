"""Private local filesystem storage for uploaded sources and published artifacts."""

from __future__ import annotations

from pathlib import Path


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
