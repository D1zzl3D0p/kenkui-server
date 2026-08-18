from __future__ import annotations

import hashlib
import hmac
import json

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
    app.include_router(stripe_webhook_router(StripeWebhookHandler(BillingService(repository), signing_secret=secret)))
    payload = json.dumps(
        {
            "id": "evt-1",
            "type": "checkout.session.completed",
            "data": {"object": {"metadata": {"account_id": "account-1", "credits": "4"}}},
        },
        separators=(",", ":"),
    ).encode()
    digest = hmac.new(secret.encode(), b"123." + payload, hashlib.sha256).hexdigest()
    headers = {"Stripe-Signature": f"t=123,v1={digest}"}

    client = TestClient(app)
    assert client.post("/v1/billing/webhooks/stripe", content=payload, headers=headers).status_code == 204
    assert client.post("/v1/billing/webhooks/stripe", content=payload, headers=headers).status_code == 204
    assert client.post("/v1/billing/webhooks/stripe", content=payload).status_code == 400
    assert repository.account("account-1").available_credits == 4
