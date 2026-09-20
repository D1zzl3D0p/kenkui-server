"""Server-managed WorkOS/AuthKit session adapter."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID, uuid4

from kenkui_server.auth.base import Identity


class WorkOSClient(Protocol):
    """Small boundary over the WorkOS SDK; browser credentials never reach this API."""

    def subject_for_session(self, session_token: str) -> str | None: ...


class IdentityRepository(Protocol):
    """Persists the provider-to-internal-ID mapping and its notifiable address."""

    def user_id_for_subject(self, provider_subject: str, email: str | None = None) -> UUID: ...

    def notification_settings(self, identity_id: UUID) -> tuple[str | None, bool]: ...

    def set_notify_by_email(self, identity_id: UUID, enabled: bool) -> None: ...


class InMemoryIdentityRepository:
    """Fake identity mapping for tests and local dependency injection."""

    def __init__(self) -> None:
        self._identities: dict[str, UUID] = {}
        self._emails: dict[UUID, str | None] = {}
        self._notify: dict[UUID, bool] = {}

    def user_id_for_subject(self, provider_subject: str, email: str | None = None) -> UUID:
        identity_id = self._identities.setdefault(provider_subject, uuid4())
        if email is not None or identity_id not in self._emails:
            self._emails[identity_id] = email or self._emails.get(identity_id)
        self._notify.setdefault(identity_id, True)
        return identity_id

    def notification_settings(self, identity_id: UUID) -> tuple[str | None, bool]:
        if identity_id not in self._notify:
            raise RuntimeError("unknown_identity")
        return self._emails.get(identity_id), self._notify[identity_id]

    def set_notify_by_email(self, identity_id: UUID, enabled: bool) -> None:
        if identity_id not in self._notify:
            raise RuntimeError("unknown_identity")
        self._notify[identity_id] = enabled


class FakeWorkOSClient:
    """Test-only session verifier; values are opaque server-side session tokens."""

    def __init__(self, subjects_by_session: dict[str, str]) -> None:
        self._subjects_by_session = subjects_by_session

    def subject_for_session(self, session_token: str) -> str | None:
        return self._subjects_by_session.get(session_token)


class WorkOSAuthBackend:
    """Maps verified WorkOS sessions to stable internal users."""

    def __init__(self, client: WorkOSClient, identities: IdentityRepository) -> None:
        self._client = client
        self._identities = identities

    def authenticate(self, session_token: str) -> Identity:
        subject = self._client.subject_for_session(session_token)
        if subject is None:
            raise PermissionError("unauthenticated")
        return Identity(self._identities.user_id_for_subject(subject), subject)

    def authorize(self, actor_id: UUID, owner_id: UUID) -> bool:
        return actor_id == owner_id
