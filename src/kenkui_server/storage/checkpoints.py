"""Lease-fenced checkpoint manifests in PostgreSQL, immutable payloads in R2."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from kenkui_server.storage.postgres_execution import PostgresExecution
from kenkui_server.storage.repositories import StaleWriteError


def file_digest(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            size += len(block)
            digest.update(block)
    return size, digest.hexdigest()


class HostedCheckpointStore:
    """A single job's checkpoints; never reuse or publish another lease's writes."""

    def __init__(self, database: Any, objects: Any, job_id: str, lease: tuple[str, str]):
        self.database = database
        self.objects = objects
        self.job_id = job_id
        self.lease = lease

    def _check(self) -> None:
        # Caller owns a transaction. Match the job as well as the lease, and reject
        # cancellation before uploading or committing any more durable work.
        PostgresExecution.check_lease(self.database, self.lease)
        row = self.database.execute(
            "SELECT j.status FROM jobs j JOIN dispatches d ON d.job_id=j.id "
            "WHERE d.id=%s AND j.id=%s",
            (self.lease[0], self.job_id),
        ).fetchone()
        if row is None or row["status"] != "running":
            raise StaleWriteError()

    def restore(self, key: str, destination: Path) -> dict[str, Any] | None:
        with self.database.transaction():
            self._check()
            row = self.database.execute(
                "SELECT id, sha256, size_bytes, metadata_json FROM job_checkpoints "
                "WHERE job_id=%s AND checkpoint_key=%s AND ready",
                (self.job_id, key),
            ).fetchone()
        if row is None:
            return None
        # Missing objects and outages fail explicitly, rather than silently paying
        # to synthesize a book again. Corrupt bytes are never handed to assembly.
        self.objects.download_checkpoint(row["id"], destination)
        if file_digest(destination) != (row["size_bytes"], row["sha256"]):
            destination.unlink(missing_ok=True)
            raise RuntimeError("checkpoint_integrity_failed")
        metadata: dict[str, Any] = json.loads(row["metadata_json"])
        return metadata

    def save(self, key: str, source: Path, metadata: dict[str, Any]) -> None:
        size, digest = file_digest(source)
        identifier = str(uuid4())
        with self.database.transaction():
            self._check()
            # Track before upload, so a crash anywhere leaves a reclaimable object.
            self.database.execute(
                "INSERT INTO job_checkpoints "
                "(id,job_id,checkpoint_key,sha256,size_bytes,metadata_json) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (identifier, self.job_id, key, digest, size, json.dumps(metadata)),
            )
        self.objects.upload_checkpoint(identifier, source)
        with self.database.transaction():
            self._check()
            self.database.execute(
                "UPDATE job_checkpoints SET ready=FALSE "
                "WHERE job_id=%s AND checkpoint_key=%s AND ready",
                (self.job_id, key),
            )
            self.database.execute(
                "UPDATE job_checkpoints SET ready=TRUE WHERE id=%s",
                (identifier,),
            )
