"""Shared source PUT intents, atomic completion and durable late-PUT tombstones."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid5

from kenkui_server.jobs.models import Asset


class SourceUploadConflict(ValueError):
    """Expired or inconsistent upload; retry must not publish this source."""


class PostgresSourceUploads:
    def __init__(self, database: Any) -> None:
        self.database = database

    def _lock(self, source_id: str) -> None:
        self.database.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (source_id,))

    def _existing(self, source_id: str, owner_id: str) -> Asset | None:
        row = self.database.execute(
            "SELECT id,path,sha256,format,owner_id,deleted_at FROM assets WHERE id=%s",
            (source_id,),
        ).fetchone()
        if row is None:
            return None
        if str(row["owner_id"]) != owner_id or row["deleted_at"] is not None:
            raise SourceUploadConflict("source ownership or retention conflict")
        return Asset(row["id"], row["path"], row["sha256"], row["format"])

    def cover_attempt(self, semantic_id: str, owner_id: str) -> str:
        """Choose one stable live generation; expired snapshots never reuse keys."""

        def completed() -> str | None:
            row = self.database.execute(
                """SELECT id,owner_id FROM assets WHERE deleted_at IS NULL
                AND (cover_operation_id=%s OR id=%s) LIMIT 1""",
                (semantic_id, semantic_id),
            ).fetchone()
            if row is None:
                return None
            if str(row["owner_id"]) != owner_id:
                raise SourceUploadConflict("cover operation ownership conflict")
            return str(row["id"])

        with self.database.transaction():
            # Semantic lock serializes generation selection; actual PUT/completion
            # and cleanup use the distinct chosen source-ID lock, without network.
            self._lock(semantic_id)
            existing = completed()
            if existing is not None:
                return existing
            latest = self.database.execute(
                """SELECT source_id,owner_id,cover_generation,
                NOT cleanup_started AND clock_timestamp()<expires_at AS eligible
                FROM source_upload_intents WHERE cover_operation_id=%s
                ORDER BY cover_generation DESC LIMIT 1 FOR UPDATE""",
                (semantic_id,),
            ).fetchone()
            # A concurrent completion may have deleted the row while SELECT waited.
            existing = completed()
            if existing is not None:
                return existing
            if latest is not None:
                if str(latest["owner_id"]) != owner_id:
                    raise SourceUploadConflict("cover operation ownership conflict")
                if latest["eligible"]:
                    return str(latest["source_id"])
            previous = self.database.execute(
                """SELECT max(generation) AS generation FROM (
                    SELECT COALESCE(cover_generation,0) AS generation FROM assets
                    WHERE cover_operation_id=%s OR id=%s
                    UNION ALL
                    SELECT cover_generation AS generation FROM source_upload_intents
                    WHERE cover_operation_id=%s
                ) history""",
                (semantic_id, semantic_id, semantic_id),
            ).fetchone()["generation"]
            generation = 0 if previous is None else int(previous) + 1
            identifier = (
                semantic_id
                if generation == 0
                else str(uuid5(UUID(semantic_id), f"kenkui-cover-attempt-v1:{generation}"))
            )
            self.database.execute(
                """INSERT INTO source_upload_intents
                (source_id,owner_id,cover_operation_id,cover_generation)
                VALUES(%s,%s,%s,%s)""",
                (identifier, owner_id, semantic_id, generation),
            )
            return identifier

    def begin(self, asset: Asset, owner_id: str) -> Asset | None:
        with self.database.transaction():
            self._lock(asset.id)
            existing = self._existing(asset.id, owner_id)
            if existing is not None:
                if existing.sha256 != asset.sha256 or existing.format != asset.format:
                    raise SourceUploadConflict("source digest conflict")
                return existing
            self.database.execute(
                """INSERT INTO source_upload_intents(source_id,owner_id,sha256)
                VALUES(%s,%s,%s) ON CONFLICT(source_id) DO NOTHING""",
                (asset.id, owner_id, asset.sha256),
            )
            self._intent(asset.id, owner_id, asset.sha256)
            return None

    def _intent(self, source_id: str, owner_id: str, sha256: str | None = None) -> Any:
        row = self.database.execute(
            """SELECT source_id,owner_id,sha256,cover_operation_id,cover_generation,
            NOT cleanup_started AND clock_timestamp()<expires_at AS eligible
            FROM source_upload_intents WHERE source_id=%s FOR UPDATE""",
            (source_id,),
        ).fetchone()
        if row is None or str(row["owner_id"]) != owner_id or not row["eligible"]:
            raise SourceUploadConflict("upload expired or owned by another account")
        if row["sha256"] is None:
            if sha256 is None or row["cover_operation_id"] is None:
                raise SourceUploadConflict("source digest is not ready")
            self.database.execute(
                "UPDATE source_upload_intents SET sha256=%s WHERE source_id=%s", (sha256, source_id)
            )
            row["sha256"] = sha256
        if sha256 is not None and row["sha256"] != sha256:
            raise SourceUploadConflict("source digest conflict")
        return row

    def complete(self, source_id: str, owner_id: str) -> Asset:
        with self.database.transaction():
            self._lock(source_id)
            existing = self._existing(source_id, owner_id)
            if existing is not None:
                return existing
            row = self._intent(source_id, owner_id)
            self.database.execute(
                """INSERT INTO assets
                (id,path,sha256,format,owner_id,cover_operation_id,cover_generation)
                VALUES(%s,%s,%s,'epub',%s,%s,%s)""",
                (
                    source_id,
                    source_id,
                    row["sha256"],
                    owner_id,
                    row["cover_operation_id"],
                    row["cover_generation"],
                ),
            )
            self.database.execute(
                "DELETE FROM source_upload_intents WHERE source_id=%s", (source_id,)
            )
            return Asset(source_id, source_id, row["sha256"], "epub")

    def reap(self, objects: Any, *, limit: int = 100) -> int:
        """One daily bounded pass; never forget a possibly in-flight provider PUT.

        Claims and completion share the same advisory lock. Claim permanently
        fences completion BEFORE releasing the lock and deleting object bytes.
        Tombstones rotate to the back of the daily queue after every attempt,
        including failures. A PUT that finishes after deletion is caught later.
        """
        if not 1 <= limit <= 100:
            raise ValueError("invalid upload cleanup batch")
        candidates = self.database.execute(
            """SELECT source_id FROM (
                (SELECT source_id,next_cleanup_at AS due FROM source_upload_intents
                WHERE next_cleanup_at<=clock_timestamp() AND expires_at<=clock_timestamp()
                ORDER BY next_cleanup_at,source_id LIMIT %s)
                UNION ALL
                (SELECT id AS source_id,COALESCE(next_source_cleanup_at,deleted_at) AS due
                FROM assets WHERE deleted_at IS NOT NULL
                AND COALESCE(next_source_cleanup_at,deleted_at)<=clock_timestamp()
                ORDER BY COALESCE(next_source_cleanup_at,deleted_at),id LIMIT %s)
            ) candidates ORDER BY due,source_id LIMIT %s""",
            (limit, limit, limit),
        ).fetchall()
        deleted = 0
        for candidate in candidates:
            source_id = candidate["source_id"]
            with self.database.transaction():
                locked = self.database.execute(
                    "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0)) AS locked",
                    (source_id,),
                ).fetchone()["locked"]
                if not locked:
                    continue
                # Completed asset rows become the durable lifecycle record.
                # A live asset always suppresses cleanup, even for stale intent
                # candidates selected before its finalization committed.
                completed = self.database.execute(
                    "SELECT id,deleted_at FROM assets WHERE id=%s FOR UPDATE", (source_id,)
                ).fetchone()
                if completed is not None:
                    self.database.execute(
                        "DELETE FROM source_upload_intents WHERE source_id=%s", (source_id,)
                    )
                    if completed["deleted_at"] is None:
                        continue
                    claimed = self.database.execute(
                        """UPDATE assets
                        SET next_source_cleanup_at=clock_timestamp()+interval '24 hours'
                        WHERE id=%s AND deleted_at IS NOT NULL
                        AND COALESCE(next_source_cleanup_at,deleted_at)<=clock_timestamp()
                        RETURNING id""",
                        (source_id,),
                    ).fetchone()
                    if (
                        claimed is not None
                        and self.database.execute(
                            """SELECT 1 FROM jobs WHERE spec_json::jsonb->>'source_id'=%s
                        AND (terminal_at IS NULL OR terminal_at>now()-interval '24 hours')
                        LIMIT 1""",
                            (source_id,),
                        ).fetchone()
                        is not None
                    ):
                        claimed = None
                else:
                    claimed = self.database.execute(
                        """UPDATE source_upload_intents SET cleanup_started=TRUE,
                        next_cleanup_at=clock_timestamp()+interval '24 hours'
                        WHERE source_id=%s AND next_cleanup_at<=clock_timestamp()
                        AND expires_at<=clock_timestamp() RETURNING source_id""",
                        (source_id,),
                    ).fetchone()
            if claimed is None:
                continue
            try:
                # No transaction spans this provider call. Missing-key DELETE is
                # idempotent; on failure the durable tombstone remains retryable.
                objects.delete_source(source_id)
            except Exception:
                continue
            deleted += 1
        return deleted
