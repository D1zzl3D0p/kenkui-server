"""Offline operator command. Output contains counts/digest only."""

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Never

import psycopg
from psycopg.rows import dict_row
from release_target import release_url

from kenkui_server.storage.migration_directory import backfill_directory, import_directory
from kenkui_server.storage.migration_fixture_quarantine import load_json


class PrivateParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        self.exit(2, '{"accepted":0}\n')


def private_bytes(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError
        if info.st_size > 64 * 1024 * 1024:
            raise ValueError
        return stream.read()


def main() -> int:
    parser = PrivateParser()
    parser.add_argument("--environment", choices=("staging", "production"), required=True)
    parser.add_argument("--target-file", type=Path, required=True)
    parser.add_argument("--source-file", type=Path, required=True)
    parser.add_argument("--attestation-file", type=Path, required=True)
    parser.add_argument("--quarantine-file", type=Path)
    parser.add_argument("--pair-review-file", type=Path)
    parser.add_argument("--restore-review-file", type=Path)
    parser.add_argument("--original-inventory-file", type=Path)
    parser.add_argument("--restored-inventory-file", type=Path)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--dry-run", action="store_true")
    action.add_argument("--activate", action="store_true")
    action.add_argument("--backfill-dry-run", action="store_true")
    action.add_argument("--backfill-apply", action="store_true")
    args = parser.parse_args()
    try:
        if os.environ.get("KENKUI_ENV") != args.environment:
            raise ValueError
        target = load_json(private_bytes(args.target_file))
        url = release_url(target, args.environment)
        evidence = load_json(args.attestation_file.read_bytes())
        backfill = args.backfill_dry_run or args.backfill_apply
        if (backfill and (args.quarantine_file or args.restore_review_file)) or (
            not backfill and args.pair_review_file
        ):
            raise ValueError
        # Original-source evidence comes only from independently reviewed restore bytes.
        if "historicalSettlements" in evidence:
            raise ValueError
        review_inputs = (
            args.original_inventory_file,
            args.restored_inventory_file,
            args.restore_review_file,
        )
        if any(review_inputs) or "restoreReviewDigest" in evidence:
            if backfill or not all(review_inputs):
                raise ValueError
            review_bytes = private_bytes(args.restore_review_file)
            review = load_json(review_bytes)
            if not (
                hashlib.sha256(review_bytes).hexdigest() == evidence["restoreReviewDigest"]
                and review["format"] == "restore-review-v1"
                and review["purpose"] in ("migrated-target", "restored-copy")
                and review["environment"] == args.environment
                and review["destination"]["target"] == target["target"]
                and review["source"]["captureDigest"] == evidence["originalSourceCaptureDigest"]
            ):
                raise ValueError
            # The verifier checks both actual captures and the independent source chronology.
            verifier = Path(__file__).with_name("restore-verify.mjs")
            with NamedTemporaryFile() as pinned:
                pinned.write(review_bytes)
                pinned.flush()
                subprocess.run(
                    [
                        "node",
                        str(verifier),
                        "compare",
                        str(args.original_inventory_file),
                        str(args.restored_inventory_file),
                        pinned.name,
                    ],
                    capture_output=True,
                    check=True,
                    timeout=120,
                    env={"PATH": os.environ["PATH"]},
                )
            evidence["historicalSettlements"] = review["historicalSettlements"]
        with (
            NamedTemporaryFile() as empty_passfile,
            psycopg.connect(
                url, row_factory=dict_row, passfile=empty_passfile.name, connect_timeout=15
            ) as conn,
        ):
            conn.execute("SET LOCAL search_path TO public, pg_temp")
            identity = conn.execute(
                "SELECT current_database() AS db, current_schema() AS schema"
            ).fetchone()
            if identity != {"db": target["target"]["database"], "schema": "public"}:
                raise ValueError
            if backfill:
                result = backfill_directory(
                    conn,
                    args.environment,
                    args.source_file.read_bytes(),
                    evidence,
                    private_bytes(args.pair_review_file) if args.pair_review_file else None,
                    apply=args.backfill_apply,
                )
            else:
                result = asdict(
                    import_directory(
                        conn,
                        args.environment,
                        args.source_file.read_bytes(),
                        evidence,
                        activate=args.activate,
                        quarantine=args.quarantine_file.read_bytes()
                        if args.quarantine_file
                        else None,
                    )
                )
        print(json.dumps(result))
        return 0
    except Exception:
        print('{"accepted":0}', file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
