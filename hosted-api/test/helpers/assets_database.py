"""Synthetic-only TS/Python upload protocol and object-cleanup fixture."""

import json
import os
import sys
from pathlib import Path

from kenkui_server.jobs.models import Asset
from kenkui_server.storage.assets import FakeS3Client, R2AssetStore
from kenkui_server.storage.postgres_database import PostgresDatabase
from kenkui_server.storage.retention import PostgresRetention
from kenkui_server.storage.source_uploads import PostgresSourceUploads

assert os.environ["KENKUI_TEST_DATABASE_URL"] == "postgresql://postgres@127.0.0.1:55438/postgres"
schema, operation, identifier, directory, extra = sys.argv[1:]
assert schema.startswith("identity_test_") and schema.replace("_", "").isalnum()
db = PostgresDatabase(os.environ["KENKUI_TEST_DATABASE_URL"], schema=schema)
uploads = PostgresSourceUploads(db)
owner = "11111111-1111-4111-8111-111111111111"
try:
    if operation == "reserve":
        uploads.begin(Asset(identifier, identifier, "a" * 64, "epub"), owner)
        print(json.dumps({"id": identifier}))
    elif operation == "complete":
        asset = uploads.complete(identifier, owner)
        print(json.dumps({"id": asset.id}))
    elif operation == "cover_select":
        print(json.dumps({"id": uploads.cover_attempt(identifier, owner)}))
    elif operation == "key":
        store = R2AssetStore(FakeS3Client(), bucket="private", key_salt="salt")
        print(json.dumps({"key": store._key("artifact", identifier)}))
    elif operation in {"reap", "retention"}:

        class Objects:
            def delete_source(self, source_id):
                assert getattr(db._state, "connection", None) is None
                if extra == "fail":
                    raise RuntimeError("synthetic failed deletion")
                (Path(directory) / source_id).unlink(missing_ok=True)

        deleted = (
            PostgresRetention(db, Objects()).run()
            if operation == "retention"
            else uploads.reap(Objects())
        )
        print(json.dumps({"deleted": deleted}))
    else:
        raise ValueError("Unknown operation")
finally:
    db.close()
