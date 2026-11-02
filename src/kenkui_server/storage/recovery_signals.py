"""Private, per-job wake hints; PostgreSQL remains the authority for job state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID


class RecoverySignals(Protocol):
    def arm(self, job_id: str) -> None: ...

    def list_job_ids(self) -> tuple[str, ...]: ...

    def clear(self, job_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class RecoverySignal:
    job_id: str
    modified_at: datetime


class TimestampedRecoverySignals(RecoverySignals, Protocol):
    """Daily orphan cleanup needs timestamps; minute recovery only needs IDs."""

    def list_records(self) -> tuple[RecoverySignal, ...]: ...


def operations_prefix(kind: str, environment: str) -> str:
    if environment not in {"staging", "production"}:
        raise ValueError("environment must be staging or production")
    return f"operations/{kind}/{environment}/"


class R2RecoverySignals:
    def __init__(self, client: Any, *, bucket: str, environment: str) -> None:
        self._client = client
        self._bucket = bucket
        self._prefix = operations_prefix("recovery", environment)

    def _key(self, job_id: str) -> str:
        if str(UUID(job_id)) != job_id:
            raise ValueError("job ID must be a canonical UUID")
        return self._prefix + job_id

    def arm(self, job_id: str) -> None:
        self._client.put_object(Bucket=self._bucket, Key=self._key(job_id), Body=b"")

    def clear(self, job_id: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=self._key(job_id))

    def list_records(self) -> tuple[RecoverySignal, ...]:
        records = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=self._prefix):
            for item in page.get("Contents", []):
                key = item["Key"]
                job_id = key.removeprefix(self._prefix)
                if not key.startswith(self._prefix) or self._key(job_id) != key:
                    raise ValueError("invalid recovery object key")
                modified = item["LastModified"]
                if not isinstance(modified, datetime) or modified.tzinfo is None:
                    raise ValueError("recovery object timestamp must be timezone aware")
                records.append(RecoverySignal(job_id, modified))
        return tuple(sorted(records, key=lambda record: record.job_id))

    def list_job_ids(self) -> tuple[str, ...]:
        return tuple(record.job_id for record in self.list_records())
