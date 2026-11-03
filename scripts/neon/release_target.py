"""Shared offline libpq destination and transport checks for operator tools."""

import os
import re
from urllib.parse import parse_qsl, unquote, urlsplit

from psycopg.conninfo import conninfo_to_dict


def require(ok):
    if not ok:
        raise ValueError("invalid private database target")


def validate_target(d):
    require(d.get("environment") in ("staging", "production"))
    pin = d.get("target", {})
    require(set(pin) == {"host", "port", "database", "projectId", "branchId"})
    require(
        all(
            isinstance(pin[k], str) and pin[k].strip() == pin[k] and pin[k]
            for k in ("host", "database", "projectId", "branchId")
        )
    )
    require(type(pin["port"]) is int and 1 <= pin["port"] <= 65535)
    raw = d.get("databaseUrl")
    require(isinstance(raw, str) and raw and not any(ord(c) <= 32 for c in raw))
    uri = urlsplit(raw)
    require(uri.scheme in ("postgres", "postgresql") and not uri.fragment)
    require(bool(re.fullmatch(r"[a-z0-9.-]+", uri.hostname or "")))
    require(uri.hostname == pin["host"] and (uri.port or 5432) == pin["port"])
    require(
        unquote(uri.path) == "/" + pin["database"]
        and bool(re.fullmatch(r"[A-Za-z0-9_.-]+", pin["database"]))
    )
    pairs = parse_qsl(uri.query, keep_blank_values=True, strict_parsing=True)
    require(len(pairs) == len({k for k, _ in pairs}))
    require(all(k in ("sslmode", "sslrootcert", "channel_binding") and v for k, v in pairs))
    effective = conninfo_to_dict(raw)
    require(
        effective.get("host") == pin["host"]
        and int(effective.get("port", 5432)) == pin["port"]
        and effective.get("dbname") == pin["database"]
    )
    synthetic = d.get("synthetic") is True
    if synthetic:
        require(
            d["environment"] == "staging" and pin["host"] == "127.0.0.1" and pin["port"] == 55438
        )
        require(bool(re.fullmatch(r"rehearsal_test_[a-f0-9]{32}(?:_empty)?", pin["database"])))
        require(
            pin["projectId"] == "synthetic-project" and pin["branchId"].startswith("synthetic-")
        )
        require(effective.get("sslmode", "disable") == "disable")
    else:
        require(effective.get("sslmode") == "verify-full")


def release_url(review, environment):
    require(set(review) in ({"environment", "target"}, {"environment", "target", "synthetic"}))
    require(review["environment"] == environment == os.environ.get("KENKUI_ENV"))
    require(not any(k.startswith("PG") for k in os.environ))
    url = os.environ[f"KENKUI_{environment.upper()}_RELEASE_DATABASE_URL"]
    validate_target({**review, "databaseUrl": url})
    return url
