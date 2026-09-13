"""Export the checked API contract without touching operator data."""

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from kenkui_server.app import create_app


def main() -> None:
    with TemporaryDirectory(prefix="kenkui-openapi-") as directory:
        app = create_app(data_dir=directory, voices=())
        document = app.openapi()
        app.state.services.repositories.database.close()
    target = Path(__file__).resolve().parents[2] / "openapi" / "v1.json"
    content = json.dumps(document, indent=2) + "\n"
    if "--check" in sys.argv:
        if target.read_text() != content:
            raise SystemExit("OpenAPI is stale. Run python -m kenkui_server.export_openapi")
    else:
        target.write_text(content)


if __name__ == "__main__":
    main()
