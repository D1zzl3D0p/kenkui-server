"""Explicit unmetered local admission declaration."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, StrictInt

from kenkui_server.billing.stripe import CREDIT_PACKS, StripeWebhookHandler

router = APIRouter(prefix="/v1/billing", tags=["billing"])


class CreditPackResponse(BaseModel):
    credits: int
    priceUsdCents: int


class BillingResponse(BaseModel):
    mode: str
    availableCredits: str | None = None
    checkoutEnabled: str | None = None
    packs: list[CreditPackResponse] | None = None
    creditHistoryAvailable: bool | None = None


class CreditLotResponse(BaseModel):
    id: str
    source: Literal["purchase", "grant", "legacy"]
    reference: str
    credited: int
    available: int
    reserved: int
    consumed: int
    recordedAt: datetime
    usageStatus: Literal["unused", "reserved", "used", "manual_review", "not_purchased"]


class CreditHistoryResponse(BaseModel):
    items: list[CreditLotResponse]


@router.get("/history", response_model=CreditHistoryResponse)
def credit_history(request: Request) -> CreditHistoryResponse:
    services = request.app.state.hosted_services
    if services is None:
        return CreditHistoryResponse(items=[])
    identity = request.state.hosted_identity
    account_id = services.account_id_for_identity(identity.user_id)
    return CreditHistoryResponse(items=[CreditLotResponse(
        id=lot.id, source=lot.kind, reference=lot.reference, credited=lot.credited,
        available=lot.available, reserved=lot.reserved, consumed=lot.consumed,
        recordedAt=lot.created_at, usageStatus=lot.usage_status,
    ) for lot in services.repositories.billing.credit_lots(account_id)])


@router.get("", response_model=BillingResponse, response_model_exclude_none=True)
def billing(request: Request) -> BillingResponse:
    """Local execution has no credits, provider, or reservation workflow."""
    services = request.app.state.hosted_services
    if services is None:
        return BillingResponse(mode="unmetered")
    identity = request.state.hosted_identity
    account = services.repositories.billing.account(
        services.account_id_for_identity(identity.user_id)
    )
    return BillingResponse(
        mode="credits",
        availableCredits=str(account.available_credits),
        checkoutEnabled="true" if request.app.state.stripe_checkout else "false",
        creditHistoryAvailable=True,
        packs=[CreditPackResponse(credits=credits, priceUsdCents=price)
               for credits, price in CREDIT_PACKS.items()],
    )


def stripe_webhook_router(handler: StripeWebhookHandler) -> APIRouter:
    """Build the hosted webhook endpoint without configuring local mode."""
    hosted_router = APIRouter(prefix="/v1/billing", tags=["billing"])

    @hosted_router.post("/webhooks/stripe", status_code=204)
    async def stripe_webhook(request: Request) -> Response:
        signature = request.headers.get("Stripe-Signature")
        if signature is None:
            return Response(status_code=400)
        try:
            handler.handle(await request.body(), signature)
        except ValueError:
            return Response(status_code=400)
        return Response(status_code=204)

    return hosted_router


class CheckoutRequest(BaseModel):
    """Only a pack size comes from the browser; account and price are server-owned."""

    model_config = ConfigDict(extra="forbid")
    credits: StrictInt


@router.post("/checkout")
def checkout(payload: CheckoutRequest, request: Request) -> dict[str, str]:
    from kenkui_server.billing.stripe import CREDIT_PACKS

    services = request.app.state.hosted_services
    provider = request.app.state.stripe_checkout
    identity = getattr(request.state, "hosted_identity", None)
    if services is None or provider is None:
        raise HTTPException(503, "Card payments are not configured yet.")
    if identity is None:
        raise HTTPException(401, "Sign in to buy credits.")
    if payload.credits not in CREDIT_PACKS:
        raise HTTPException(422, "invalid_credit_pack")
    account_id = services.account_id_for_identity(identity.user_id)
    try:
        return {"url": provider.create(account_id, payload.credits)}
    except (OSError, ValueError) as error:
        raise HTTPException(502, "Unable to start checkout. Please try again.") from error
