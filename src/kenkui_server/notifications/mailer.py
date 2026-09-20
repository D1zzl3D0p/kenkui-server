"""Outbound mail as a narrow port, so the provider stays one swappable class."""

from __future__ import annotations

import smtplib
import ssl
from collections.abc import Mapping
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Message:
    """One rendered message. Rendering decisions never reach the transport."""

    to: str
    subject: str
    text: str
    html: str | None = None
    headers: Mapping[str, str] = field(default_factory=dict)


class Mailer(Protocol):
    """Delivers one rendered message, or raises. Callers own retry policy."""

    def send(self, message: Message) -> None: ...


class NullMailer:
    """Accepts and discards mail. The default wherever no provider is configured."""

    def send(self, message: Message) -> None:
        return None


class RecordingMailer:
    """Test double retaining every accepted message in order."""

    def __init__(self) -> None:
        self.sent: list[Message] = []

    def send(self, message: Message) -> None:
        self.sent.append(message)


@dataclass(frozen=True, slots=True)
class SmtpConfig:
    """Submission credentials. The login account and the visible sender differ.

    iCloud+ authenticates as the Apple ID while mail leaves as a custom-domain
    alias, so username and sender are separate fields rather than one address.
    """

    host: str
    port: int
    username: str
    password: str = field(repr=False)
    sender: str
    sender_name: str = "Kenkui"
    timeout: float = 20.0


ICLOUD_SMTP_HOST = "smtp.mail.me.com"
ICLOUD_SMTP_PORT = 587


def smtp_config_from_environment(environment: Mapping[str, str]) -> SmtpConfig | None:
    """Build submission config, or None when mail is deliberately unconfigured."""
    password = environment.get("KENKUI_SMTP_PASSWORD", "").strip()
    sender = environment.get("KENKUI_SMTP_SENDER", "").strip()
    username = environment.get("KENKUI_SMTP_USERNAME", "").strip() or sender
    if not password or not sender:
        return None
    return SmtpConfig(
        host=environment.get("KENKUI_SMTP_HOST", "").strip() or ICLOUD_SMTP_HOST,
        port=int(environment.get("KENKUI_SMTP_PORT", "").strip() or ICLOUD_SMTP_PORT),
        username=username,
        password=password,
        sender=sender,
        sender_name=environment.get("KENKUI_SMTP_SENDER_NAME", "").strip() or "Kenkui",
    )


class SmtpMailer:
    """STARTTLS submission against a single provider account."""

    def __init__(self, config: SmtpConfig) -> None:
        self._config = config

    def send(self, message: Message) -> None:
        mime = EmailMessage()
        mime["From"] = formataddr((self._config.sender_name, self._config.sender))
        mime["To"] = message.to
        mime["Subject"] = message.subject
        # Transactional mail must not provoke auto-replies or vacation responders.
        mime["Auto-Submitted"] = "auto-generated"
        for name, value in message.headers.items():
            mime[name] = value
        mime.set_content(message.text)
        if message.html is not None:
            mime.add_alternative(message.html, subtype="html")
        context = ssl.create_default_context()
        with smtplib.SMTP(
            self._config.host, self._config.port, timeout=self._config.timeout
        ) as smtp:
            smtp.starttls(context=context)
            smtp.login(self._config.username, self._config.password)
            smtp.send_message(mime)
