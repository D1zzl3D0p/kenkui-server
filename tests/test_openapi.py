import json
from pathlib import Path

from kenkui_server.app import create_app


OPENAPI_ARTIFACT = Path(__file__).parents[1] / "openapi" / "v1.json"


def test_checked_openapi_contract_matches_the_v1_application() -> None:
    document = json.loads(OPENAPI_ARTIFACT.read_text())

    assert document == create_app().openapi()
    assert set(document["paths"]) == {
        "/v1/assets",
        "/v1/assets/{asset_id}/book",
        "/v1/billing",
        "/v1/capabilities",
        "/v1/health",
        "/v1/jobs",
        "/v1/jobs/preflight",
        "/v1/jobs/{job_id}",
        "/v1/jobs/{job_id}/artifact",
        "/v1/jobs/{job_id}/cancel",
        "/v1/jobs/{job_id}/events",
        "/v1/voices",
    }
    assert all(path.startswith("/v1/") for path in document["paths"])
    assert document["components"]["schemas"]["ErrorResponse"] == {
        "description": "The sole error envelope exposed by the versioned API.",
        "properties": {
            "error": {"$ref": "#/components/schemas/ErrorDetail"},
        },
        "required": ["error"],
        "title": "ErrorResponse",
        "type": "object",
    }
    assert document["components"]["schemas"]["JobResponse"]["properties"]["status"]["enum"] == [
        "queued",
        "running",
        "cancel_requested",
        "succeeded",
        "failed",
        "cancelled",
    ]
