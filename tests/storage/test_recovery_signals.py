from datetime import UTC, datetime
from uuid import uuid4

import pytest

from kenkui_server.storage.recovery_signals import R2RecoverySignals


class ObjectClient:
    def __init__(self):
        self.objects = {}
        self.pages = None

    def put_object(self, *, Bucket, Key, Body):
        self.objects[Key] = (Body, datetime.now(UTC))

    def delete_object(self, *, Bucket, Key):
        self.objects.pop(Key, None)

    def get_paginator(self, operation):
        assert operation == "list_objects_v2"
        return self

    def paginate(self, *, Bucket, Prefix):
        if self.pages is not None:
            yield from self.pages
        else:
            yield {
                "Contents": [
                    {"Key": key, "LastModified": modified}
                    for key, (_, modified) in self.objects.items()
                    if key.startswith(Prefix)
                ]
            }


def test_signal_is_private_opaque_and_clearing_one_preserves_another():
    client = ObjectClient()
    signals = R2RecoverySignals(client, bucket="private", environment="staging")
    first, second = str(uuid4()), str(uuid4())
    signals.arm(first)
    signals.arm(second)
    assert client.objects[f"operations/recovery/staging/{first}"][0] == b""
    signals.clear(first)
    assert signals.list_job_ids() == (second,)
    assert signals.list_records()[0].job_id == second


def test_listing_finds_signal_on_later_page_and_keeps_timestamp():
    client = ObjectClient()
    job_id = str(uuid4())
    modified = datetime(2026, 11, 1, tzinfo=UTC)
    client.pages = [
        {},
        {
            "Contents": [
                {"Key": f"operations/recovery/production/{job_id}", "LastModified": modified}
            ]
        },
    ]
    signals = R2RecoverySignals(client, bucket="private", environment="production")
    assert signals.list_job_ids() == (job_id,)
    assert signals.list_records()[0].modified_at == modified


@pytest.mark.parametrize("environment", ["", "../production", "production/", "dev"])
def test_invalid_environment_cannot_escape_prefix(environment):
    with pytest.raises(ValueError):
        R2RecoverySignals(ObjectClient(), bucket="private", environment=environment)


@pytest.mark.parametrize("job_id", ["", "../job", "identity@example.com", "a/b"])
def test_invalid_job_id_cannot_write_or_delete(job_id):
    client = ObjectClient()
    signals = R2RecoverySignals(client, bucket="private", environment="staging")
    with pytest.raises(ValueError):
        signals.arm(job_id)
    with pytest.raises(ValueError):
        signals.clear(job_id)
    assert client.objects == {}


def test_listing_error_is_visible():
    class BrokenClient(ObjectClient):
        def paginate(self, **kwargs):
            raise RuntimeError("storage unavailable")

    with pytest.raises(RuntimeError, match="storage unavailable"):
        R2RecoverySignals(BrokenClient(), bucket="private", environment="staging").list_job_ids()
