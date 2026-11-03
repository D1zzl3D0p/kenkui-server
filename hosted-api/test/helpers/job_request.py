"""No-network oracle for the actual public Python request schema."""

import json
import sys

from pydantic import ValidationError

from kenkui_server.api.schemas import JobRequest

try:
    model = JobRequest.model_validate(json.loads(sys.stdin.read()), from_attributes=True)
    print(json.dumps({"ok": model.model_dump(by_alias=True, exclude_unset=True)}))
except ValidationError as error:
    print(json.dumps({"errors": error.errors(include_url=False)}))
