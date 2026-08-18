"""Normalized error response contracts for the public API."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ErrorDetail(BaseModel):
    """Stable client-facing description of a failed request."""

    model_config = ConfigDict(populate_by_name=True)

    code: str
    message: str
    request_id: str = Field(serialization_alias="requestId")
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    """The sole error envelope exposed by the versioned API."""

    error: ErrorDetail
