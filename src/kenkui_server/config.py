"""Local-only server configuration and public capability DTOs."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class ServerConfig(BaseModel):
    """Network configuration for the local ASGI server."""

    host: str = "127.0.0.1"
    port: int = 8000
    web_build_path: Path | None = None



class HostedConfig(BaseModel):
    """Deployment-only provider configuration; serialization always masks secrets."""

    database_url: SecretStr
    r2_bucket: str
    r2_endpoint: str
    r2_access_key_id: SecretStr
    r2_secret_access_key: SecretStr
    workos_api_key: SecretStr
    stripe_webhook_secret: SecretStr

class AuthCapabilities(BaseModel):
    """Authentication modes supported by this server."""

    mode: Literal["none"] = "none"


class BillingCapabilities(BaseModel):
    """Billing modes supported by this server."""

    mode: Literal["unmetered"] = "unmetered"


class CastingCapabilities(BaseModel):
    """Casting modes supported by this server."""

    mode: Literal["single"] = "single"


class Capabilities(BaseModel):
    """Public, versioned declaration of local server features."""

    model_config = ConfigDict(populate_by_name=True)

    api_version: Literal["1"] = Field("1", serialization_alias="apiVersion")
    auth: AuthCapabilities = AuthCapabilities()
    billing: BillingCapabilities = BillingCapabilities()
    source_formats: list[Literal["epub"]] = Field(
        default_factory=lambda: ["epub"], serialization_alias="sourceFormats"
    )
    output_formats: list[Literal["m4b"]] = Field(
        default_factory=lambda: ["m4b"], serialization_alias="outputFormats"
    )
    casting: CastingCapabilities = CastingCapabilities()


def local_capabilities() -> Capabilities:
    """Return the immutable capability declaration for local mode."""
    return Capabilities()
