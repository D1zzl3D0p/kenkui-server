from __future__ import annotations

from uuid import UUID

import pytest

from kenkui_server.auth.workos import (
    FakeWorkOSClient,
    InMemoryIdentityRepository,
    WorkOSAuthBackend,
)


def test_workos_identity_maps_stably_to_internal_uuid() -> None:
    identities = InMemoryIdentityRepository()
    backend = WorkOSAuthBackend(FakeWorkOSClient({"session": "workos-user-1"}), identities)

    first = backend.authenticate("session")
    second = backend.authenticate("session")

    assert first == second
    assert isinstance(first.user_id, UUID)
    assert first.provider_subject == "workos-user-1"


def test_workos_backend_rejects_unknown_session_and_enforces_owner() -> None:
    backend = WorkOSAuthBackend(FakeWorkOSClient({}), InMemoryIdentityRepository())

    with pytest.raises(PermissionError, match="unauthenticated"):
        backend.authenticate("missing")

    owner = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    stranger = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    assert backend.authorize(owner, owner)
    assert not backend.authorize(stranger, owner)
