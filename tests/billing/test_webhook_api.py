from __future__ import annotations

import hashlib
import hmac
import json
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kenkui_server.api.billing import stripe_webhook_router
from kenkui_server.billing.models import InMemoryBillingRepository
from kenkui_server.billing.service import BillingService
from kenkui_server.billing.stripe import StripeWebhookHandler


def test_stripe_webhook_route_requires_signature_and_replays_safely() -> None:
    repository = InMemoryBillingRepository()
    secret = "secret"
    app = FastAPI()
    app.include_router(
        stripe_webhook_router(
            StripeWebhookHandler(BillingService(repository), signing_secret=secret)
        )
    )
    payload = json.dumps(
        {
            "id": "evt-1",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_1",
                    "mode": "payment",
                    "client_reference_id": "account-1",
                    "payment_status": "paid",
                    "currency": "usd",
                    "amount_total": 500,
                    "amount_subtotal": 500,
                    "total_details": {"amount_tax": 0, "amount_discount": 0, "amount_shipping": 0},
                    "metadata": {
                        "purpose": "kenkui_credits_v1",
                        "account_id": "account-1",
                        "credits": "500",
                    },
                }
            },
        },
        separators=(",", ":"),
    ).encode()
    timestamp = str(int(time.time()))
    digest = hmac.new(
        secret.encode(), timestamp.encode() + b"." + payload, hashlib.sha256
    ).hexdigest()
    headers = {"Stripe-Signature": f"t={timestamp},v1={digest}"}

    client = TestClient(app)
    assert (
        client.post("/v1/billing/webhooks/stripe", content=payload, headers=headers).status_code
        == 204
    )
    assert (
        client.post("/v1/billing/webhooks/stripe", content=payload, headers=headers).status_code
        == 204
    )
    assert client.post("/v1/billing/webhooks/stripe", content=payload).status_code == 400
    assert repository.account("account-1").available_credits == 500
