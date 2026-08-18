"""Explicit unmetered local admission declaration."""

from fastapi import APIRouter

router = APIRouter(prefix="/v1/billing", tags=["billing"])


@router.get("")
def billing() -> dict[str, str]:
    """Local execution has no credits, provider, or reservation workflow."""
    return {"mode": "unmetered"}
