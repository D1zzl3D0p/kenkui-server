"""Verified Stripe webhook adapter; payment details never enter core billing state."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any, Protocol

from kenkui_server.billing.service import BillingService


@dataclass(frozen=True, slots=True)
class PaymentEvent:
    """Provider-neutral credit purchase extracted from a verified payment event."""

    id: str
    account_id: str
    credits: int


class PaymentProvider(Protocol):
    """Maps an authenticated provider delivery to a credit purchase."""

    def verify(self, payload: bytes, signature: str) -> PaymentEvent | None: ...


class StripeWebhookHandler:
    """Accept signed checkout completion events and persist them idempotently."""

    def __init__(self, billing: BillingService, *, signing_secret: str) -> None:
        self._billing = billing
        self._signing_secret = signing_secret.encode()

    def handle(self, payload: bytes, signature: str) -> PaymentEvent | None:
        timestamp, provided = _signature_parts(signature)
        expected = hmac.new(
            self._signing_secret, timestamp.encode() + b"." + payload, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, provided):
            raise ValueError("invalid_webhook_signature")
        event: dict[str, Any] = json.loads(payload)
        if event.get("type") != "checkout.session.completed":
            return None
        metadata = event["data"]["object"].get("metadata", {})
        account_id = metadata.get("account_id")
        credits = metadata.get("credits")
        event_id = event.get("id")
        if not isinstance(account_id, str) or not isinstance(credits, str) or not isinstance(event_id, str):
            raise ValueError("invalid_payment_event")
        try:
            parsed_credits = int(credits)
        except ValueError as error:
            raise ValueError("invalid_payment_event") from error
        if parsed_credits < 1:
            raise ValueError("invalid_payment_event")
        payment = PaymentEvent(event_id, account_id, parsed_credits)
        self._billing.grant_credits(payment.account_id, payment.credits, reference=payment.id)
        return payment


def _signature_parts(signature: str) -> tuple[str, str]:
    values = dict(part.split("=", 1) for part in signature.split(",") if "=" in part)
    timestamp = values.get("t")
    digest = values.get("v1")
    if timestamp is None or digest is None:
        raise ValueError("invalid_webhook_signature")
    return timestamp, digest
