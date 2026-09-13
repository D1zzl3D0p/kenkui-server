"""Hosted execution claims and stale-attempt fencing."""

import time
from typing import Any
from uuid import uuid4

from kenkui_server.billing.models import AuthorizationStatus
from kenkui_server.storage.repositories import StaleWriteError


class PostgresExecution:
    _connection: Any
    billing: Any

    @staticmethod
    def check_lease(connection: Any, lease: tuple[str, str] | None) -> None:
        if lease is None:
            return
        row = connection.execute(
            "SELECT token, expires_at FROM execution_leases WHERE dispatch_id=%s FOR UPDATE",
            (lease[0],),
        ).fetchone()
        if row is None or row["token"] != lease[1] or row["expires_at"] <= time.time():
            raise StaleWriteError()

    def claim_execution(
        self, dispatch_id: str, *, limit: int, ttl: float = 30, max_attempts: int = 3
    ) -> str | None:
        now = time.time()
        with self._connection.transaction() as connection:
            connection.execute("SELECT pg_advisory_xact_lock(1262833236)", ())
            row = connection.execute(
                (
                    "SELECT d.job_id, j.status FROM dispatches d JOIN jobs j ON j.id=d.job_id "
                    "WHERE d.id=%s"
                ),
                (dispatch_id,),
            ).fetchone()
            if row is None or row["status"] in {"succeeded", "failed", "cancelled"}:
                return None
            previous = connection.execute(
                "SELECT expires_at, attempts FROM execution_leases WHERE dispatch_id=%s FOR UPDATE",
                (dispatch_id,),
            ).fetchone()
            if previous is not None and previous["expires_at"] > now:
                return None
            active = connection.execute(
                (
                    "SELECT COUNT(*) AS count FROM execution_leases l JOIN dispatches d ON "
                    "d.id=l.dispatch_id JOIN jobs j ON j.id=d.job_id WHERE l.expires_at>%s AND "
                    "j.status NOT IN ('succeeded','failed','cancelled')"
                ),
                (now,),
            ).fetchone()["count"]
            if active >= limit:
                return None
            if previous is not None and previous["attempts"] >= max_attempts:
                status = "cancelled" if row["status"] == "cancel_requested" else "failed"
                connection.execute(
                    "UPDATE jobs SET status=%s, version=version+1, terminal_at=now() WHERE id=%s",
                    (status, row["job_id"]),
                )
                connection.execute(
                    "UPDATE dispatches SET status='done', version=version+1 WHERE id=%s",
                    (dispatch_id,),
                )
                connection.execute(
                    "INSERT INTO job_failures VALUES (%s,%s,%s) ON CONFLICT(job_id) DO NOTHING",
                    (
                        row["job_id"],
                        "worker_lost",
                        "The worker repeatedly stopped. Please retry the job.",
                    ),
                )
                connection.execute(
                    (
                        "INSERT INTO job_events SELECT id, (SELECT "
                        "COALESCE(MAX(sequence),0)+1 FROM "
                        "job_events WHERE job_id=%s), %s, progress_json FROM jobs WHERE id=%s"
                    ),
                    (row["job_id"], status, row["job_id"]),
                )
                self.billing.finalize(row["job_id"], AuthorizationStatus.RELEASED)
                return None
            token = str(uuid4())
            connection.execute(
                (
                    "INSERT INTO execution_leases VALUES (%s,%s,%s,1) ON CONFLICT(dispatch_id) DO "
                    "UPDATE SET token=EXCLUDED.token, expires_at=EXCLUDED.expires_at, "
                    "attempts=execution_leases.attempts+1"
                ),
                (dispatch_id, token, now + ttl),
            )
            connection.execute(
                "UPDATE dispatches SET status='pending', version=version+1 WHERE id=%s",
                (dispatch_id,),
            )
            return token

    def renew_execution(self, dispatch_id: str, token: str, *, ttl: float = 30) -> bool:
        result = self._connection.execute(
            (
                "UPDATE execution_leases SET expires_at=%s WHERE dispatch_id=%s AND token=%s "
                "AND expires_at>%s"
            ),
            (time.time() + ttl, dispatch_id, token, time.time()),
        )
        return bool(result.rowcount == 1)

    def release_execution(self, dispatch_id: str, token: str) -> None:
        self._connection.execute(
            "UPDATE execution_leases SET expires_at=0 WHERE dispatch_id=%s AND token=%s",
            (dispatch_id, token),
        )

    def failure_for_job(self, job_id: str) -> dict[str, str] | None:
        row = self._connection.execute(
            "SELECT code, message FROM job_failures WHERE job_id=%s", (job_id,)
        ).fetchone()
        return dict(row) if row else None
