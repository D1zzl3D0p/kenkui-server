from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest

from kenkui_server.billing.models import InMemoryBillingRepository
from kenkui_server.billing.service import BillingService
from kenkui_server.billing.stripe import StripeWebhookHandler


def paid_session():
    return {
        "id": "cs_1",
        "mode": "payment",
        "client_reference_id": "account-1",
        "payment_status": "paid",
        "currency": "usd",
        "amount_total": 500,
        "amount_subtotal": 500,
        "total_details": {"amount_tax": 0, "amount_discount": 0, "amount_shipping": 0},
        "metadata": {"purpose": "kenkui_credits_v1", "account_id": "account-1", "credits": "500"},
    }


def signed_event(secret: str, event: dict[str, object]) -> tuple[bytes, str]:
    payload = json.dumps(event, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    signature = hmac.new(
        secret.encode(), timestamp.encode() + b"." + payload, hashlib.sha256
    ).hexdigest()
    return payload, f"t={timestamp},v1={signature}"


def test_duplicate_stripe_event_credits_account_once() -> None:
    repository = InMemoryBillingRepository()
    handler = StripeWebhookHandler(BillingService(repository), signing_secret="webhook-secret")
    payload, signature = signed_event(
        "webhook-secret",
        {
            "id": "evt-1",
            "type": "checkout.session.completed",
            "data": {"object": paid_session()},
        },
    )

    handler.handle(payload, signature)
    handler.handle(payload, signature)

    assert repository.account("account-1").available_credits == 500


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
            "data": {"object": paid_session()},
        },
    )

    handler.handle(payload, signature)

    assert billing.payments == [("stripe", "cs_1", "account-1", 500)]


def test_stripe_handler_rejects_an_unverified_event() -> None:
    handler = StripeWebhookHandler(
        BillingService(InMemoryBillingRepository()), signing_secret="secret"
    )

    with pytest.raises(ValueError, match="invalid_webhook_signature"):
        handler.handle(b'{"id":"evt-1"}', "t=123,v1=bad")


@pytest.mark.parametrize(
    "field,value",
    [
        ("currency", "eur"),
        ("amount_total", 499),
        ("mode", "subscription"),
        ("client_reference_id", "other-account"),
        ("id", "bad"),
    ],
)
def test_mismatched_paid_session_never_grants(field, value):
    repo = InMemoryBillingRepository()
    handler = StripeWebhookHandler(BillingService(repo), signing_secret="secret")
    session = paid_session()
    session[field] = value
    payload, signature = signed_event(
        "secret",
        {
            "type": "checkout.session.completed",
            "data": {"object": session},
        },
    )
    with pytest.raises(ValueError):
        handler.handle(payload, signature)
    assert repo.account("account-1").available_credits == 0


def test_unpaid_and_unrelated_sessions_are_ignored():
    repo = InMemoryBillingRepository()
    handler = StripeWebhookHandler(BillingService(repo), signing_secret="secret")
    for changes in [{"payment_status": "unpaid"}, {"metadata": {}}]:
        session = {**paid_session(), **changes}
        payload, signature = signed_event(
            "secret",
            {
                "type": "checkout.session.completed",
                "data": {"object": session},
            },
        )
        assert handler.handle(payload, signature) is None
    assert repo.account("account-1").available_credits == 0


def test_multiple_events_for_same_session_credit_once():
    repo = InMemoryBillingRepository()
    handler = StripeWebhookHandler(BillingService(repo), signing_secret="secret")
    for event_type in ["checkout.session.completed", "checkout.session.async_payment_succeeded"]:
        payload, signature = signed_event(
            "secret",
            {
                "id": event_type,
                "type": event_type,
                "data": {"object": paid_session()},
            },
        )
        handler.handle(payload, signature)
    assert repo.account("account-1").available_credits == 500


def test_old_signature_rejected_and_rotated_signatures_accepted(monkeypatch):
    repo = InMemoryBillingRepository()
    handler = StripeWebhookHandler(BillingService(repo), signing_secret="secret")
    payload, signature = signed_event(
        "secret",
        {
            "type": "checkout.session.completed",
            "data": {"object": paid_session()},
        },
    )
    handler.handle(payload, signature + ",v1=another-secret-signature")
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now + 301)
    with pytest.raises(ValueError, match="invalid_webhook_signature"):
        handler.handle(payload, signature)


def test_checkout_creates_server_owned_card_purchase(monkeypatch):
    import io
    from urllib.parse import parse_qs

    from kenkui_server.billing.stripe import StripeCheckout

    requests = []

    def open_request(request, timeout):
        requests.append(request)
        return io.BytesIO(b'{"url":"https://checkout.stripe.com/c/pay/cs_test_1"}')

    monkeypatch.setattr("kenkui_server.billing.stripe.urlopen", open_request)
    checkout = StripeCheckout(secret_key="sk_test_fake", web_origin="https://app.kenkui.fm")
    assert checkout.create("account-1", 500).startswith("https://checkout.stripe.com/")
    form = parse_qs(requests[0].data.decode())
    assert form["line_items[0][price_data][unit_amount]"] == ["500"]
    assert form["metadata[credits]"] == ["500"]
    assert form["metadata[account_id]"] == ["account-1"]
    assert "payment_method_types[0]" not in form
    assert form["managed_payments[enabled]"] == ["true"]
    assert form["line_items[0][price_data][tax_behavior]"] == ["exclusive"]
    assert form["line_items[0][price_data][product_data][tax_code]"] == ["txcd_10105001"]
    with pytest.raises(ValueError):
        checkout.create("account-1", 1)
    assert len(requests) == 1


def test_managed_tax_and_local_currency_do_not_change_credit_pack():
    repo = InMemoryBillingRepository()
    handler = StripeWebhookHandler(BillingService(repo), signing_secret="secret")
    session = paid_session()
    session["amount_total"] = 540
    session["total_details"]["amount_tax"] = 40
    session["presentment_details"] = {"presentment_currency": "cad", "presentment_amount": 730}
    payload, signature = signed_event(
        "secret",
        {
            "type": "checkout.session.completed",
            "data": {"object": session},
        },
    )
    handler.handle(payload, signature)
    assert repo.account("account-1").available_credits == 500


@pytest.mark.parametrize(
    "changes",
    [
        {"amount_subtotal": 450},
        {"total_details": {"amount_tax": -1, "amount_discount": 0, "amount_shipping": 0}},
        {"total_details": {"amount_tax": 40, "amount_discount": 0, "amount_shipping": 0}},
        {"total_details": {"amount_tax": 0, "amount_discount": 10, "amount_shipping": 0}},
    ],
)
def test_managed_checkout_rejects_inconsistent_amounts(changes):
    repo = InMemoryBillingRepository()
    handler = StripeWebhookHandler(BillingService(repo), signing_secret="secret")
    payload, signature = signed_event(
        "secret",
        {
            "type": "checkout.session.completed",
            "data": {"object": {**paid_session(), **changes}},
        },
    )
    with pytest.raises(ValueError):
        handler.handle(payload, signature)
    assert repo.account("account-1").available_credits == 0
