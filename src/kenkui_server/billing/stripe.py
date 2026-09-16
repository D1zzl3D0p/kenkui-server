"""Stripe-hosted card checkout and verified, idempotent credit fulfillment."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from kenkui_server.billing.service import BillingService

# Credits and purchase price are separate: bonus credits must not change the
# amount charged, and webhooks must validate the actual server-owned pack price.
CREDIT_PACKS = {500: 500, 1100: 1000, 2400: 2000}  # credits: USD cents
# Keep old sessions fulfillable after a catalog change, without offering them
# for new purchases. Never rewrite the meaning of a previously issued version.
PURCHASE_CATALOGS = {
    "kenkui_credits_v1": {500: 500, 1000: 1000, 2000: 2000},
    "kenkui_credits_v2": CREDIT_PACKS,
}


class StripeCheckout:
    """Create server-priced sessions; card details stay on Stripe's hosted page."""

    def __init__(self, *, secret_key: str, web_origin: str) -> None:
        origin = urlsplit(web_origin)
        if origin.scheme != "https" or not origin.netloc or origin.query or origin.fragment:
            raise ValueError("checkout requires an HTTPS web origin")
        self._secret_key = secret_key
        self._web_origin = web_origin.rstrip("/")

    def create(self, account_id: str, credits: int) -> str:
        if credits not in CREDIT_PACKS:
            raise ValueError("invalid_credit_pack")
        form = {
            "mode": "payment",
            "managed_payments[enabled]": "true",
            "success_url": f"{self._web_origin}/billing?checkout=success",
            "cancel_url": f"{self._web_origin}/billing?checkout=cancelled",
            "client_reference_id": account_id,
            "metadata[account_id]": account_id,
            "metadata[credits]": str(credits),
            "metadata[purpose]": "kenkui_credits_v2",
            "line_items[0][price_data][currency]": "usd",
            "line_items[0][price_data][unit_amount]": str(CREDIT_PACKS[credits]),
            "line_items[0][price_data][tax_behavior]": "exclusive",
            "line_items[0][price_data][product_data][tax_code]": os.environ.get(
                "KENKUI_STRIPE_TAX_CODE", "txcd_10105001"
            ),
            "line_items[0][price_data][product_data][name]": f"{credits:,} Kenkui credits",
            "line_items[0][quantity]": "1",
        }
        request = Request(
            "https://api.stripe.com/v1/checkout/sessions",
            data=urlencode(form).encode(),
            headers={
                "Authorization": f"Bearer {self._secret_key}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        with urlopen(request, timeout=20) as response:
            session = json.load(response)
        url = session.get("url")
        if not isinstance(url, str) or not url.startswith("https://checkout.stripe.com/"):
            raise ValueError("invalid_checkout_response")
        return url


@dataclass(frozen=True, slots=True)
class PaymentEvent:
    """Provider-neutral purchase, keyed by Checkout Session for deduplication."""

    id: str
    account_id: str
    credits: int


class StripeWebhookHandler:
    """Grant only paid USD purchases with a recent authenticated signature."""

    def __init__(self, billing: BillingService, *, signing_secret: str) -> None:
        self._billing = billing
        self._signing_secret = signing_secret.encode()

    def handle(self, payload: bytes, signature: str) -> PaymentEvent | None:
        parts = [part.strip().split("=", 1) for part in signature.split(",") if "=" in part]
        timestamps = [value for key, value in parts if key == "t"]
        digests = [value for key, value in parts if key == "v1"]
        if len(timestamps) != 1 or not digests:
            raise ValueError("invalid_webhook_signature")
        timestamp = timestamps[0]
        try:
            recent = abs(time.time() - int(timestamp)) <= 300
        except ValueError:
            recent = False
        expected = hmac.new(
            self._signing_secret, timestamp.encode() + b"." + payload, hashlib.sha256
        ).hexdigest()
        if not recent or not any(
            hmac.compare_digest(expected.encode(), digest.encode()) for digest in digests
        ):
            raise ValueError("invalid_webhook_signature")
        try:
            event = json.loads(payload)
            if event.get("type") not in {
                "checkout.session.completed",
                "checkout.session.async_payment_succeeded",
            }:
                return None
            session = event["data"]["object"]
            metadata = session.get("metadata") or {}
            catalog = PURCHASE_CATALOGS.get(metadata.get("purpose"))
            if catalog is None:
                return None
            if session.get("payment_status") != "paid":
                return None
            account_id = metadata["account_id"]
            credits = int(metadata["credits"])
            session_id = session["id"]
            totals = session.get("total_details") or {}
            tax = totals.get("amount_tax")
            price = catalog.get(credits)
            # Tax and local-currency presentment do not change the pack's credits.

            if (
                not isinstance(account_id, str)
                or not account_id
                or not isinstance(session_id, str)
                or not session_id.startswith("cs_")
                or credits not in catalog
                or price is None
                or session.get("client_reference_id") != account_id
                or session.get("mode") != "payment"
                or session.get("currency") != "usd"
                or session.get("amount_subtotal") != price
                or type(tax) is not int
                or tax < 0
                or totals.get("amount_discount") != 0
                or totals.get("amount_shipping") != 0
                or session.get("amount_total") != price + tax
            ):
                raise ValueError("invalid_payment_event")
        except (KeyError, TypeError, AttributeError, ValueError) as error:
            raise ValueError("invalid_payment_event") from error
        payment = PaymentEvent(session_id, account_id, credits)
        self._billing.process_payment_event(
            "stripe", payment.id, payment.account_id, payment.credits
        )
        return payment
