"""Reader control over completion mail, including one-click unsubscribe."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, StrictBool

from kenkui_server.notifications.messages import verify_unsubscribe_token

router = APIRouter(prefix="/v1/notifications", tags=["notifications"])


class NotificationPreferenceResponse(BaseModel):
    """What completion mail would do for the signed-in reader right now."""

    email: str | None = None
    emailOnCompletion: bool = False


class NotificationPreferenceRequest(BaseModel):
    emailOnCompletion: StrictBool


def _identities(request: Request) -> Any:
    services = request.app.state.hosted_services
    identities = getattr(services, "identities", None) if services is not None else None
    if identities is None:
        raise HTTPException(404, "notifications_unavailable")
    return identities


@router.get("", response_model=NotificationPreferenceResponse)
def preference(request: Request) -> NotificationPreferenceResponse:
    identities = _identities(request)
    identity = request.state.hosted_identity
    email, enabled = identities.notification_settings(identity.user_id)
    return NotificationPreferenceResponse(email=email, emailOnCompletion=enabled)


@router.patch("", response_model=NotificationPreferenceResponse)
def set_preference(
    request: Request, body: NotificationPreferenceRequest
) -> NotificationPreferenceResponse:
    identities = _identities(request)
    identity = request.state.hosted_identity
    identities.set_notify_by_email(identity.user_id, body.emailOnCompletion)
    email, enabled = identities.notification_settings(identity.user_id)
    return NotificationPreferenceResponse(email=email, emailOnCompletion=enabled)


@router.get("/unsubscribe", include_in_schema=False)
def unsubscribe(request: Request, identity: str, token: str) -> HTMLResponse:
    """Honour the link in a sent email. The signature is the only credential.

    Mail clients follow these without a session, so this deliberately accepts
    no cookie and can only ever turn a preference off.
    """
    config = request.app.state.hosted_config
    secret = config.unsubscribe_secret.get_secret_value() if config is not None else ""
    try:
        identity_id = UUID(identity)
    except ValueError as error:
        raise HTTPException(400, "invalid_unsubscribe_link") from error
    if not secret or not verify_unsubscribe_token(identity_id, secret, token):
        raise HTTPException(400, "invalid_unsubscribe_link")
    _identities(request).set_notify_by_email(identity_id, False)
    return HTMLResponse(
        "<html><body><p>You will no longer receive email when a book finishes."
        " You can turn this back on in Kenkui Studio.</p></body></html>"
    )
