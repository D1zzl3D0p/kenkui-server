"""Run explicit offline, disposable-integration, or release workspace checks."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DISPOSABLE_DATABASE = "postgresql://postgres@127.0.0.1:55438/postgres"


@dataclass(frozen=True)
class Check:
    directory: str
    command: tuple[str, ...]


def checks(tier: str, component: str) -> list[Check]:
    server = "kenkui-server"
    hosted = server + "/hosted-api"
    studio = "kenkui-studio"
    pytest = ("uv", "run", "--frozen", "--extra", "hosted", "pytest")
    python_tests = pytest if tier != "fast" else (*pytest, "--ignore=tests/postgres")
    result = [
        Check(server, python_tests),
        Check(server, ("uv", "run", "--frozen", "mypy")),
        Check(
            server,
            (
                "uv",
                "run",
                "--frozen",
                "ruff",
                "check",
                "src",
                "scripts/verify-workspace.py",
                "tests/test_verify_workspace.py",
            ),
        ),
        Check(
            server,
            ("uv", "run", "--frozen", "python", "-m", "kenkui_server.export_openapi", "--check"),
        ),
        Check(hosted, ("npm", "run", "typecheck")),
        Check(hosted, ("npm", "test")),
        Check(hosted, ("npm", "run", "build")),
        Check(studio, ("npm", "run", "check:api")),
        Check(studio, ("npm", "test")),
        Check(studio, ("npm", "run", "build:web")),
        Check(studio, ("npm", "run", "build:native")),
    ]
    if tier != "fast":
        result.append(Check(studio, ("npm", "run", "test:e2e")))
    if tier == "release":
        result.append(Check(hosted, ("npm", "run", "smoke")))
    directories = {"server": server, "hosted-api": hosted, "studio": studio}
    return (
        result
        if component == "all"
        else [c for c in result if c.directory == directories[component]]
    )


def validate_environment(tier: str, env: Mapping[str, str]) -> None:
    url = env.get("KENKUI_TEST_DATABASE_URL", "")
    if tier != "fast" and url != DISPOSABLE_DATABASE:
        raise ValueError(
            "Integration/release requires the disposable PostgreSQL fixture at "
            + DISPOSABLE_DATABASE
        )
    if tier != "fast":
        missing = [
            tool
            for tool in ("age", "age-keygen", "pg_dump", "pg_restore")
            if not shutil.which(tool)
        ]
        if missing:
            raise ValueError("Integration/release requires restore tools: " + ", ".join(missing))
    if tier == "fast" and url:
        raise ValueError(
            "Fast checks must run without a database URL; "
            "select integration for the disposable fixture"
        )


def run_checks(selected: Sequence[Check], root: Path = ROOT) -> None:
    for check in selected:
        print(f"Verifying {check.directory}: {' '.join(check.command)}", flush=True)
        subprocess.run(check.command, cwd=root / check.directory, check=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", choices=("fast", "integration", "release"), default="integration")
    parser.add_argument(
        "--component", choices=("all", "server", "hosted-api", "studio"), default="all"
    )
    parser.add_argument("--list", action="store_true", help="Print checks without running them")
    args = parser.parse_args(argv)
    selected = checks(args.tier, args.component)
    if args.list:
        for check in selected:
            print(f"{check.directory}: {' '.join(check.command)}")
        return 0
    try:
        validate_environment(args.tier, os.environ)
        run_checks(selected)
    except ValueError as error:
        parser.error(str(error))
    except subprocess.CalledProcessError as error:
        return error.returncode or 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
