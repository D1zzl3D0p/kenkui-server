"""Rendered completion mail. Copy lives here; delivery policy does not."""

from __future__ import annotations

import hashlib
import hmac
from html import escape
from urllib.parse import quote
from uuid import UUID

from kenkui_server.notifications.mailer import Message

# Bound the signature to one purpose so a token cannot be replayed elsewhere.
_UNSUBSCRIBE_LABEL = b"kenkui-notification-unsubscribe-v1:"


def unsubscribe_token(identity_id: UUID, secret: str) -> str:
    """Stateless proof that the mail's recipient asked to stop receiving it."""
    return hmac.new(
        secret.encode(), _UNSUBSCRIBE_LABEL + str(identity_id).encode(), hashlib.sha256
    ).hexdigest()


def verify_unsubscribe_token(identity_id: UUID, secret: str, token: str) -> bool:
    return hmac.compare_digest(unsubscribe_token(identity_id, secret), token)


def unsubscribe_url(api_origin: str, identity_id: UUID, secret: str) -> str:
    token = unsubscribe_token(identity_id, secret)
    return (
        f"{api_origin.rstrip('/')}/v1/notifications/unsubscribe"
        f"?identity={quote(str(identity_id))}&token={quote(token)}"
    )


def completion_email(
    *, to: str, job_id: str, title: str | None, web_origin: str, unsubscribe: str
) -> Message:
    """The one message a finished book sends. Subject names the book when known."""
    book = title.strip() if title and title.strip() else None
    subject = f"“{book}” is ready" if book else "Your audiobook is ready"
    link = f"{web_origin.rstrip('/')}/jobs/{quote(job_id)}"
    described = f"“{book}”" if book else "Your audiobook"
    text = (
        f"{described} has finished narrating and is ready to download.\n\n"
        f"{link}\n\n"
        "Stop these emails: "
        f"{unsubscribe}\n"
    )
    html = (
        "<html><body>"
        f"<p>{escape(described)} has finished narrating and is ready to download.</p>"
        f'<p><a href="{escape(link, quote=True)}">Open it in Kenkui Studio</a></p>'
        f'<p><a href="{escape(unsubscribe, quote=True)}">Stop these emails</a></p>'
        "</body></html>"
    )
    return Message(
        to=to,
        subject=subject,
        text=text,
        html=html,
        # One-click list management keeps transactional mail out of spam folders.
        headers={"List-Unsubscribe": f"<{unsubscribe}>"},
    )
