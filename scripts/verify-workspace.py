"""Run the beta gates against the checked-out sibling repositories."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
commands = [
    ("kenkui-server", ["uv", "run", "--extra", "hosted", "pytest"]),
    ("kenkui-server", ["uv", "run", "mypy"]),
    ("kenkui-server", ["uv", "run", "ruff", "check", "src"]),
    ("kenkui-server", ["uv", "run", "python", "-m", "kenkui_server.export_openapi", "--check"]),
    ("kenkui-studio", ["npm", "run", "check:api"]),
    ("kenkui-studio", ["npm", "test"]),
    ("kenkui-studio", ["npm", "run", "build"]),
    ("kenkui-studio", ["npm", "run", "test:e2e"]),
]
for repository, command in commands:
    print(f"Verifying {repository}: {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=ROOT / repository, check=True)
