"""Provider-neutral server-side authentication and resource authorization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Identity:
    """Internal identity; provider tokens and secrets are intentionally excluded."""

    user_id: UUID
    provider_subject: str


class AuthBackend(Protocol):
    """Turns a server-managed session into an internal identity."""

    def authenticate(self, session_token: str) -> Identity: ...

    def authorize(self, actor_id: UUID, owner_id: UUID) -> bool: ...
