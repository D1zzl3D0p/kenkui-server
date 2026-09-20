"""Deciding whether a finished job mails its owner, and doing so at most once."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from kenkui_server.jobs.models import Job
from kenkui_server.notifications.mailer import Mailer
from kenkui_server.notifications.messages import completion_email, unsubscribe_url

EMAIL_CHANNEL = "email"


@dataclass(frozen=True, slots=True)
class Recipient:
    """Who a finished job belongs to, and whether they want to hear about it."""

    identity_id: UUID
    email: str | None
    notify_by_email: bool


class NotificationRepository(Protocol):
    def recipient_for_job(self, job_id: str) -> Recipient | None: ...

    def claim(self, job_id: str, channel: str) -> bool:
        """True exactly once per job and channel, for the attempt that may send."""
        ...

    def mark_delivered(self, job_id: str, channel: str) -> None: ...


class CompletionNotifier:
    """Mails a job's owner when it succeeds.

    Delivery is deliberately at-most-once. The claim is taken before the send,
    so an attempt that dies mid-submission stays silent rather than risking a
    duplicate: a finished book is worth one email, never two.
    """

    def __init__(
        self,
        repository: NotificationRepository,
        mailer: Mailer,
        *,
        web_origin: str,
        api_origin: str,
        unsubscribe_secret: str,
    ) -> None:
        self._repository = repository
        self._mailer = mailer
        self._web_origin = web_origin
        self._api_origin = api_origin
        self._unsubscribe_secret = unsubscribe_secret

    def completed(self, job: Job) -> None:
        """Never raises. A job is finished whether or not its mail leaves."""
        try:
            self._send(job)
        except Exception:
            logging.getLogger(__name__).exception(
                "completion_notification_failed", extra={"job_id": job.id}
            )

    def _send(self, job: Job) -> None:
        recipient = self._repository.recipient_for_job(job.id)
        if recipient is None or not recipient.email or not recipient.notify_by_email:
            return
        if not self._repository.claim(job.id, EMAIL_CHANNEL):
            return
        self._mailer.send(
            completion_email(
                to=recipient.email,
                job_id=job.id,
                title=job.spec.output.title,
                web_origin=self._web_origin,
                unsubscribe=unsubscribe_url(
                    self._api_origin, recipient.identity_id, self._unsubscribe_secret
                ),
            )
        )
        self._repository.mark_delivered(job.id, EMAIL_CHANNEL)
