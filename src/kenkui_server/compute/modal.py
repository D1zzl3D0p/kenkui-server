"""Modal submission with database-owned admission and attempt identity."""

import hashlib
import json
import logging
import os
import re
import sys
from typing import Any


class ModalProcessRunner:
    def __init__(
        self, repositories: Any, *, app_name: str = "kenkui-beta", max_jobs: int = 2
    ) -> None:
        self.repositories = repositories
        self.app_name = app_name
        self.max_jobs = max_jobs

    def start(self, dispatch_id: str) -> None:
        import modal

        # Include cold-start time before the worker starts renewing its lease.
        token = self.repositories.claim_execution(dispatch_id, limit=self.max_jobs, ttl=900)
        if token is None:
            return
        try:
            call = modal.Function.from_name(
                self.app_name,
                "render_job",
                environment_name=os.environ.get("MODAL_ENVIRONMENT"),
            ).spawn(dispatch_id, token)
        except Exception:
            # An ambiguous submission may have reached Modal: keep its lease until
            # expiry so a retry cannot launch an overlapping attempt immediately.
            logging.getLogger(__name__).exception(
                "hosted_dispatch_submission_failed", extra={"dispatch_id": dispatch_id}
            )
            return

        # Modal captures stdout even when the submitting process has no INFO handler.
        try:
            if not isinstance(call, modal.FunctionCall):
                return
            call_id = call.object_id
            if not isinstance(call_id, str) or not re.fullmatch(r"fc-[A-Za-z0-9]+", call_id):
                return
            event = {
                "event": "hosted_dispatch_spawned",
                "dispatch_id": dispatch_id,
                "function_call_id": call_id,
                "lease_token_sha256": hashlib.sha256(token.encode("utf-8")).hexdigest(),
            }
            sys.stdout.write(json.dumps(event, separators=(",", ":")) + "\n")
            sys.stdout.flush()
        except Exception:
            # The call was submitted. Observability must never trigger a second attempt.
            return
