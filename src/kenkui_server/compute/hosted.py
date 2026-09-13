"""Hosted worker composition using the same library execution adapter."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from kenkui_server.storage.postgres import PostgresHostedRepository
from kenkui_server.storage.postgres_database import PostgresDatabase
from kenkui_server.worker import LocalJobRunner


class HostedWorkerStore:
    def __init__(self, objects: Any, root: Path) -> None:
        self.objects = objects
        self.root = root

    def materialize_source(self, asset_id: str) -> Any:
        return self.objects.materialize_source(asset_id)

    def artifact_path(self, identifier: str) -> Path:
        return self.root / f"{identifier}.m4b"


class HostedJobRunner(LocalJobRunner):
    def __init__(
        self,
        database_url: str,
        objects: Any,
        root: Path,
        *,
        lease: tuple[str, str],
        render_workers: int = 1,
        fixture_mode: bool = False,
    ) -> None:
        super().__init__(
            root / "unused",
            root,
            lease=lease,
            render_workers=render_workers,
            fixture_mode=fixture_mode,
        )
        self.database_url = database_url
        self.objects = objects

    def _database(self) -> PostgresDatabase:
        return PostgresDatabase(self.database_url)

    def _repositories(self, database: Any) -> PostgresHostedRepository:
        return PostgresHostedRepository(database)

    def _store(self) -> HostedWorkerStore:
        return HostedWorkerStore(self.objects, self._assets_root)

    def _publish(self, store: Any, job_id: str, output: Path) -> str:
        identifier = output.stem
        from uuid import uuid4

        database = self._database()
        try:
            database.execute(
                (
                    "INSERT INTO stored_objects (id, object_kind, opaque_object_key, terminal_at, "
                    "resource_id) VALUES (%s, 'temporary', %s, now(), %s) ON "
                    "CONFLICT(object_kind, resource_id) DO NOTHING"
                ),
                (str(uuid4()), identifier, identifier),
            )
        finally:
            database.close()
        self.objects.upload_artifact_file(identifier, output)
        return identifier


def execute_hosted(
    database_url: str, objects: Any, dispatch_id: str, token: str, *, render_workers: int = 1
) -> None:
    with TemporaryDirectory(prefix="kenkui-hosted-job-") as directory:
        HostedJobRunner(
            database_url,
            objects,
            Path(directory),
            lease=(dispatch_id, token),
            render_workers=render_workers,
        ).run(dispatch_id)
