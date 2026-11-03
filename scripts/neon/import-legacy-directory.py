"""Offline operator command. Output contains counts/digest only."""

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Never

import psycopg
from psycopg.rows import dict_row

from kenkui_server.storage.migration_directory import import_directory
from kenkui_server.storage.migration_fixture_quarantine import load_json


class PrivateParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        self.exit(2, '{"accepted":0}\n')


def main() -> int:
    parser = PrivateParser()
    parser.add_argument("--environment", choices=("staging", "production"), required=True)
    parser.add_argument("--source-file", type=Path, required=True)
    parser.add_argument("--attestation-file", type=Path, required=True)
    parser.add_argument("--quarantine-file", type=Path)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--dry-run", action="store_true")
    action.add_argument("--activate", action="store_true")
    args = parser.parse_args()
    try:
        if os.environ.get("KENKUI_ENV") != args.environment:
            raise ValueError
        url = os.environ[f"KENKUI_{args.environment.upper()}_RELEASE_DATABASE_URL"]
        evidence = load_json(args.attestation_file.read_bytes())
        with psycopg.connect(url, row_factory=dict_row) as conn:
            result = import_directory(
                conn,
                args.environment,
                args.source_file.read_bytes(),
                evidence,
                activate=args.activate,
                quarantine=args.quarantine_file.read_bytes() if args.quarantine_file else None,
            )
        print(json.dumps(asdict(result)))
        return 0
    except Exception:
        print('{"accepted":0}', file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
