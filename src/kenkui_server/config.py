"""Local-only server configuration and public capability DTOs."""

import os
from pathlib import Path
from typing import Literal

from kenkui import limits as kk_limits
from pydantic import BaseModel, ConfigDict, Field, SecretStr

DEFAULT_CHARACTER_MODEL = "openrouter/deepseek/deepseek-v4-flash"


def _allowed_origins_from_env() -> list[str]:
    """Read the comma-separated KENKUI_ALLOWED_ORIGINS allowlist."""
    raw = os.environ.get("KENKUI_ALLOWED_ORIGINS", "")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


class ServerConfig(BaseModel):
    """Network configuration for the local ASGI server."""

    host: str = "127.0.0.1"
    port: int = 8000
    web_build_path: Path | None = None
    data_dir: Path | None = None
    model_allowlist: tuple[str, ...] = ()
    max_jobs: int = Field(2, ge=1)
    render_workers: int = Field(1, ge=1)
    allowed_origins: list[str] = Field(default_factory=_allowed_origins_from_env)


class HostedConfig(BaseModel):
    """Deployment-only provider configuration; serialization always masks secrets."""

    database_url: SecretStr
    r2_bucket: str
    r2_endpoint: str
    r2_access_key_id: SecretStr
    r2_secret_access_key: SecretStr
    workos_api_key: SecretStr
    stripe_webhook_secret: SecretStr
    stripe_secret_key: SecretStr = SecretStr("")
    web_origin: str = ""


class AuthCapabilities(BaseModel):
    """Authentication modes supported by this server."""

    mode: Literal["none", "session", "bearer"] = "none"


class BillingCapabilities(BaseModel):
    """Billing modes supported by this server."""

    mode: Literal["unmetered", "credits"] = "unmetered"


class CastingCapabilities(BaseModel):
    """Casting modes supported by this server.

    A list rather than one value: character casting is advertised only where a
    deployment has configured an LLM allowlist, so a browser can tell the two
    apart without trying and failing.
    """

    modes: list[Literal["single", "characters"]] = Field(default=["single"])
    models: list[str] = Field(default_factory=list)


class CoverCapabilities(BaseModel):
    read: bool = True
    upload: bool = True
    max_upload_bytes: int = Field(8 * 1024 * 1024, serialization_alias="maxUploadBytes")


class NarrationCapabilities(BaseModel):
    """How this server's renderer turns text into time.

    Published so a browser can show what a chapter will cost before anyone
    submits it, using the same numbers the renderer reports against rather than
    a copy that can drift from them.
    """

    model_config = ConfigDict(populate_by_name=True)

    characters_per_second: int = Field(
        kk_limits.TYPICAL_SPEECH_CHARACTERS_PER_SECOND,
        serialization_alias="charactersPerSecond",
    )
    long_chapter_hours: float = Field(
        kk_limits.LONG_CHAPTER_HOURS, serialization_alias="longChapterHours"
    )


class Capabilities(BaseModel):
    """Public, versioned declaration of local server features."""

    model_config = ConfigDict(populate_by_name=True)

    api_version: Literal["1"] = Field("1", serialization_alias="apiVersion")
    auth: AuthCapabilities = AuthCapabilities()
    billing: BillingCapabilities = BillingCapabilities()
    source_formats: list[Literal["epub"]] = Field(
        default=["epub"], serialization_alias="sourceFormats"
    )
    output_formats: list[Literal["m4b"]] = Field(
        default=["m4b"], serialization_alias="outputFormats"
    )
    casting: CastingCapabilities = CastingCapabilities()
    covers: CoverCapabilities = CoverCapabilities()
    narration: NarrationCapabilities = NarrationCapabilities()
    pause_lengths: bool = Field(True, serialization_alias="pauseLengths")
    scene_pauses: bool = Field(True, serialization_alias="scenePauses")
    speech_settings: bool = Field(True, serialization_alias="speechSettings")
    max_upload_bytes: int = Field(50 * 1024 * 1024, serialization_alias="maxUploadBytes")


def local_capabilities(model_allowlist: tuple[str, ...] = ()) -> Capabilities:
    """Return the capability declaration for local mode.

    Character casting is advertised only when the deployment has named the
    models it will accept. Offering it without one would let a browser build a
    job the server must then refuse.
    """
    modes: list[Literal["single", "characters"]] = ["single"]
    if model_allowlist:
        modes.append("characters")
    return Capabilities(casting=CastingCapabilities(modes=modes, models=list(model_allowlist)))
