"""Environment-driven assembly of completion mail.

An unconfigured deployment is valid and simply stays silent, so every factory
here returns None rather than raising when mail was never set up.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from kenkui_server.notifications.mailer import (
    Mailer,
    SmtpMailer,
    smtp_config_from_environment,
)
from kenkui_server.notifications.service import CompletionNotifier, NotificationRepository


def api_origin_from(environment: Mapping[str, str]) -> str:
    """Where unsubscribe links point. The auth callback already names this host."""
    explicit = environment.get("KENKUI_API_ORIGIN", "").strip()
    if explicit:
        return explicit.rstrip("/")
    parts = urlsplit(environment.get("WORKOS_REDIRECT_URI", "").strip())
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else ""


@dataclass(frozen=True, slots=True)
class NotificationSettings:
    """Everything completion mail needs except the database it will read."""

    mailer: Mailer
    web_origin: str
    api_origin: str
    unsubscribe_secret: str = field(repr=False)

    def notifier(self, repository: NotificationRepository) -> CompletionNotifier:
        return CompletionNotifier(
            repository,
            self.mailer,
            web_origin=self.web_origin,
            api_origin=self.api_origin,
            unsubscribe_secret=self.unsubscribe_secret,
        )


def notification_settings_from_environment(
    environment: Mapping[str, str],
) -> NotificationSettings | None:
    """None whenever mail, the links it carries, or its signing key is missing."""
    config = smtp_config_from_environment(environment)
    web_origin = environment.get("KENKUI_WEB_ORIGIN", "").strip().rstrip("/")
    # Deliberately not the session secret: workers sign these links, and that
    # key seals browser sessions. Sharing it would widen its blast radius.
    unsubscribe_secret = environment.get("KENKUI_UNSUBSCRIBE_SECRET", "").strip()
    api_origin = api_origin_from(environment)
    if config is None or not web_origin or not unsubscribe_secret or not api_origin:
        return None
    return NotificationSettings(
        SmtpMailer(config), web_origin, api_origin, unsubscribe_secret
    )
