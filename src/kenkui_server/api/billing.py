"""Explicit unmetered local admission declaration."""

from fastapi import APIRouter, Request, Response

from kenkui_server.billing.stripe import StripeWebhookHandler

router = APIRouter(prefix="/v1/billing", tags=["billing"])


@router.get("")
def billing(request: Request) -> dict[str, str]:
    """Local execution has no credits, provider, or reservation workflow."""
    services = request.app.state.hosted_services
    if services is None:
        return {"mode": "unmetered"}
    identity = request.state.hosted_identity
    account = services.repositories.billing.account(
        services.account_id_for_identity(identity.user_id)
    )
    return {"mode": "credits", "availableCredits": str(account.available_credits)}


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
