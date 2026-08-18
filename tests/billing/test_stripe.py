from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from kenkui_server.billing.models import InMemoryBillingRepository
from kenkui_server.billing.service import BillingService
from kenkui_server.billing.stripe import StripeWebhookHandler


def signed_event(secret: str, event: dict[str, object]) -> tuple[bytes, str]:
    payload = json.dumps(event, separators=(",", ":")).encode()
    signature = hmac.new(secret.encode(), b"123." + payload, hashlib.sha256).hexdigest()
    return payload, f"t=123,v1={signature}"


def test_duplicate_stripe_event_credits_account_once() -> None:
    repository = InMemoryBillingRepository()
    handler = StripeWebhookHandler(BillingService(repository), signing_secret="webhook-secret")
    payload, signature = signed_event(
        "webhook-secret",
        {
            "id": "evt-1",
            "type": "checkout.session.completed",
            "data": {"object": {"metadata": {"account_id": "account-1", "credits": "25"}}},
        },
    )

    handler.handle(payload, signature)
    handler.handle(payload, signature)

    assert repository.account("account-1").available_credits == 25


def test_stripe_handler_records_provider_event_before_crediting() -> None:
    class RecordingBilling:
        def __init__(self) -> None:
            self.payments: list[tuple[str, str, str, int]] = []

        def process_payment_event(
            self, provider: str, provider_event_id: str, account_id: str, credits: int
        ) -> None:
            self.payments.append((provider, provider_event_id, account_id, credits))

    billing = RecordingBilling()
    handler = StripeWebhookHandler(billing, signing_secret="webhook-secret")  # type: ignore[arg-type]
    payload, signature = signed_event(
        "webhook-secret",
        {
            "id": "evt-1",
            "type": "checkout.session.completed",
            "data": {"object": {"metadata": {"account_id": "account-1", "credits": "25"}}},
        },
    )

    handler.handle(payload, signature)

    assert billing.payments == [("stripe", "evt-1", "account-1", 25)]


def test_stripe_handler_rejects_an_unverified_event() -> None:
    handler = StripeWebhookHandler(
        BillingService(InMemoryBillingRepository()), signing_secret="secret"
    )

    with pytest.raises(ValueError, match="invalid_webhook_signature"):
        handler.handle(b'{"id":"evt-1"}', "t=123,v1=bad")
