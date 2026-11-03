"""Private-stdin, read-only operator capture. No provider calls or automatic repair."""

import hashlib
import json
import sys
from datetime import UTC, datetime

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from release_target import validate_target

from kenkui_server.storage.assets import R2AssetStore
from kenkui_server.storage.migration_allocation_evidence import allocation_exceptions
from kenkui_server.storage.migration_fixture_quarantine import INVENTORY, restored_snapshot_digest

USER_SCHEMA = "n.nspname<>'information_schema' AND left(n.nspname,3)<>'pg_'"


def require(ok):
    if not ok:
        raise ValueError("private inventory blocked")


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    ).hexdigest()


def validate(d):
    """Parse with libpq BEFORE connecting, rejecting all alternate routing/startup inputs."""
    require(d.get("environment") in ("staging", "production"))
    require(d.get("mode") in ("source", "restored-copy", "migrated-target"))
    require(d.get("managedAuth") in ("required", "absent-reviewed-source"))
    validate_target(d)
    require(isinstance(d.get("r2Keys"), list) and all(isinstance(k, str) for k in d["r2Keys"]))
    require(isinstance(d.get("keySalt"), str))


def identifier(c, schema, name):
    return sql.Identifier(schema, name).as_string(c)


def rows_digest(c, table, columns=None):
    value = sql.SQL("to_jsonb(t)")
    if columns is not None:
        value = sql.SQL("jsonb_build_object({})").format(
            sql.SQL(",").join(
                part
                for col in columns
                for part in (sql.Literal(col), sql.SQL("t.{}").format(sql.Identifier(col)))
            )
        )
    query = sql.SQL('SELECT {}::text AS line FROM {} t ORDER BY {}::text COLLATE "C"').format(
        value, table, value
    )
    h, count = hashlib.sha256(), 0
    with c.cursor(name="inventory_rows") as cur:
        cur.execute(query)
        for row in cur:
            h.update(row["line"].encode("utf8") + b"\n")
            count += 1
    return {"count": str(count), "sha256": h.hexdigest()}


def user_types(c):
    """Independent user types; table row types and generated arrays are represented elsewhere."""
    return c.execute(
        "SELECT n.nspname AS schema,t.typname AS name,t.typtype,t.oid "
        "FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace WHERE "
        + USER_SCHEMA
        + " AND t.typrelid=0 AND NOT EXISTS "
        "(SELECT 1 FROM pg_type owner_type WHERE owner_type.typarray=t.oid) "
        "ORDER BY n.nspname,t.typname"
    ).fetchall()


def database_collation(c):
    """Capture effective locale and ICU rules without guessing across catalog versions."""
    catalog = c.execute(
        "SELECT to_jsonb(d) AS catalog FROM pg_database d WHERE datname=current_database()"
    ).fetchone()["catalog"]
    require(
        all(k in catalog for k in ("datcollate", "datctype", "datcollversion", "datlocprovider"))
    )
    provider = catalog["datlocprovider"]
    require(provider in ("c", "i", "b"))
    if provider == "i":
        require("daticurules" in catalog)
        require(bool(catalog.get("datlocale") or catalog.get("daticulocale")))
    elif provider == "b":
        require(bool(catalog.get("datlocale")))
    fields = (
        "datcollate",
        "datctype",
        "datcollversion",
        "datlocprovider",
        "datlocale",
        "daticulocale",
        "daticurules",
    )
    return {key: catalog[key] for key in fields if key in catalog}


def default_acls(c):
    """Portable future-object policy only; never infer existing-object access.

    PG17 pg_default_acl documents r/S/f/T/n. Global entries override built-in
    defaults; per-schema entries augment them. Keep empty ACLs as real objects.
    """
    allowed = {
        "r": {
            "SELECT",
            "INSERT",
            "UPDATE",
            "DELETE",
            "TRUNCATE",
            "REFERENCES",
            "TRIGGER",
            "MAINTAIN",
        },
        "S": {"USAGE", "SELECT", "UPDATE"},
        "f": {"EXECUTE"},
        "T": {"USAGE"},
        "n": {"USAGE", "CREATE"},
    }
    count = c.execute("SELECT count(*) AS count FROM pg_catalog.pg_default_acl").fetchone()["count"]
    require(type(count) is int and count >= 0)
    rows = c.execute("""
        SELECT d.oid,d.defaclrole AS owner_oid,d.defaclnamespace AS schema_oid,
               d.defaclobjtype AS object_type,d.defaclacl IS NULL AS acl_null,
               cardinality(d.defaclacl) AS acl_items,o.rolname AS owner,n.nspname AS schema,
               x.grantor AS grantor_oid,g.rolname AS grantor,
               x.grantee AS grantee_oid,r.rolname AS grantee,
               x.privilege_type AS privilege,x.is_grantable AS grant_option
        FROM pg_catalog.pg_default_acl d
        LEFT JOIN pg_catalog.pg_roles o ON o.oid=d.defaclrole
        LEFT JOIN pg_catalog.pg_namespace n ON n.oid=d.defaclnamespace
        LEFT JOIN LATERAL pg_catalog.aclexplode(d.defaclacl) x ON true
        LEFT JOIN pg_catalog.pg_roles g ON g.oid=x.grantor
        LEFT JOIN pg_catalog.pg_roles r ON r.oid=x.grantee
    """).fetchall()
    records = {}
    for row in rows:
        oid, owner_oid, schema_oid = row["oid"], row["owner_oid"], row["schema_oid"]
        owner, schema, kind = row["owner"], row["schema"], row["object_type"]
        require(type(oid) is int and oid > 0 and type(owner_oid) is int and owner_oid > 0)
        require(type(schema_oid) is int and schema_oid >= 0)
        require(isinstance(owner, str) and bool(owner))
        require(
            (schema_oid == 0 and schema is None)
            or (schema_oid > 0 and isinstance(schema, str) and bool(schema))
        )
        require(isinstance(kind, str) and kind in allowed)
        require(kind != "n" or schema is None)  # PostgreSQL schemas cannot be nested.
        require(row["acl_null"] is False)  # Unexpected SQL NULL has unspecified semantics here.
        require(type(row["acl_items"]) is int and row["acl_items"] >= 0)
        definition = {
            "kind": "default-acl",
            "owner": owner,
            "schema": schema,
            "objectType": kind,
            "privileges": [],
        }
        identity = (owner, schema, kind, owner_oid, schema_oid, row["acl_items"])
        if oid not in records:
            records[oid] = (identity, definition, set())
        require(records[oid][0] == identity)
        privileges = records[oid][1]["privileges"]
        if row["acl_items"] == 0:
            require(
                not privileges
                and all(
                    row[k] is None
                    for k in (
                        "grantor_oid",
                        "grantor",
                        "grantee_oid",
                        "grantee",
                        "privilege",
                        "grant_option",
                    )
                )
            )
            # LEFT JOIN must yield exactly one sentinel for a present empty array.
            require(not records[oid][2])
            records[oid][2].add(None)
            continue
        grantor, grantee = row["grantor"], row["grantee"]
        require(type(row["grantor_oid"]) is int and row["grantor_oid"] > 0)
        require(isinstance(grantor, str) and bool(grantor))
        require(type(row["grantee_oid"]) is int and row["grantee_oid"] >= 0)
        require(
            (row["grantee_oid"] == 0 and grantee is None)
            or (row["grantee_oid"] > 0 and isinstance(grantee, str) and bool(grantee))
        )
        require(isinstance(row["privilege"], str) and row["privilege"] in allowed[kind])
        require(type(row["grant_option"]) is bool)
        require(not (row["grantee_oid"] == 0 and row["grant_option"]))
        privilege = {
            "grantor": grantor,
            "grantee": {"kind": "public"}
            if row["grantee_oid"] == 0
            else {"kind": "role", "name": grantee},
            "privilege": row["privilege"],
            "grantOption": row["grant_option"],
        }
        require(privilege not in privileges)
        privileges.append(privilege)
        records[oid][2].add((row["grantor_oid"], row["grantee_oid"]))
    require(len(records) == count)
    objects = {}
    for identity, definition, items in records.values():
        require(items == {None} if identity[-1] == 0 else len(items) == identity[-1])
        definition["privileges"].sort(
            key=lambda p: (
                p["grantor"],
                p["grantee"]["kind"],
                p["grantee"].get("name", ""),
                p["privilege"],
                p["grantOption"],
            )
        )
        key = "@pg_default_acl:" + json.dumps(
            [definition["owner"], definition["schema"], definition["objectType"]],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        require(key not in objects)
        objects[key] = definition
    return dict(sorted(objects.items()))


def capture(d):
    validate(d)
    with (
        psycopg.connect(d["databaseUrl"], row_factory=dict_row, connect_timeout=15) as c,
        c.transaction(),
    ):
        c.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED, READ ONLY")
        c.execute(
            "SET LOCAL lock_timeout='15s'; SET LOCAL statement_timeout='120s'; SET LOCAL "
            "row_security=off; SET LOCAL TIME ZONE 'UTC'; SET LOCAL extra_float_digits=3;"
            " SET LOCAL search_path=public"
        )
        # Large objects live outside user relations. Read the unfiltered catalog directly.
        require(
            c.execute("SELECT 1 FROM pg_catalog.pg_largeobject_metadata LIMIT 1").fetchone() is None
        )
        # Inspect the PG17 definition catalogs themselves: dependencies on pinned built-ins
        # (including the bootstrap owner) need no pg_depend or pg_shdepend row.
        # FirstNormalObjectId (16384) separates initdb definitions from user-created ones.
        unsupported = c.execute("""
            SELECT 1 FROM (
                SELECT oid FROM pg_catalog.pg_cast
                UNION ALL SELECT oid FROM pg_catalog.pg_transform
                UNION ALL SELECT oid FROM pg_catalog.pg_am
                UNION ALL SELECT oid FROM pg_catalog.pg_language
                UNION ALL SELECT oid FROM pg_catalog.pg_event_trigger
                UNION ALL SELECT oid FROM pg_catalog.pg_collation
                UNION ALL SELECT oid FROM pg_catalog.pg_conversion
                UNION ALL SELECT oid FROM pg_catalog.pg_operator
                UNION ALL SELECT oid FROM pg_catalog.pg_opclass
                UNION ALL SELECT oid FROM pg_catalog.pg_opfamily
                UNION ALL SELECT oid FROM pg_catalog.pg_amop
                UNION ALL SELECT oid FROM pg_catalog.pg_amproc
                UNION ALL SELECT oid FROM pg_catalog.pg_ts_parser
                UNION ALL SELECT oid FROM pg_catalog.pg_ts_template
                UNION ALL SELECT oid FROM pg_catalog.pg_ts_dict
                UNION ALL SELECT oid FROM pg_catalog.pg_ts_config
                UNION ALL SELECT oid FROM pg_catalog.pg_statistic_ext
            ) definitions WHERE oid >= 16384
            UNION ALL SELECT 1 FROM (
                SELECT objid,classid FROM pg_catalog.pg_depend
                UNION ALL SELECT objid,classid FROM pg_catalog.pg_shdepend
                    WHERE dbid=(SELECT oid FROM pg_catalog.pg_database
                                WHERE datname=current_database())
            ) deps WHERE objid >= 16384 AND classid NOT IN (
              'pg_class'::regclass,'pg_namespace'::regclass,'pg_type'::regclass,
              'pg_proc'::regclass,'pg_constraint'::regclass,'pg_attrdef'::regclass,
              'pg_trigger'::regclass,'pg_policy'::regclass,'pg_rewrite'::regclass,
              'pg_default_acl'::regclass)
            UNION ALL SELECT 1 FROM pg_catalog.pg_rewrite
                WHERE oid >= 16384 AND rulename <> '_RETURN'
            UNION ALL SELECT 1 FROM pg_catalog.pg_extension WHERE extname <> 'plpgsql'
            UNION ALL SELECT 1 FROM pg_catalog.pg_foreign_data_wrapper
            UNION ALL SELECT 1 FROM pg_catalog.pg_publication
            UNION ALL SELECT 1 FROM pg_catalog.pg_subscription WHERE subdbid=(
              SELECT oid FROM pg_catalog.pg_database WHERE datname=current_database())
            LIMIT 1""").fetchone()
        require(unsupported is None)
        schemas = {
            r["nspname"]
            for r in c.execute("SELECT n.nspname FROM pg_namespace n WHERE " + USER_SCHEMA)
        }
        relations = c.execute(
            (
                "SELECT n.nspname AS schema,c.relname AS name,c.oid,c.relkind FROM pg_class c"
                " JOIN pg_namespace n ON n.oid=c.relnamespace WHERE "
            )
            + USER_SCHEMA
            + ' ORDER BY n.nspname COLLATE "C",c.relname COLLATE "C"'
        ).fetchall()
        functions = c.execute(
            (
                "SELECT p.oid,n.nspname,p.proname,pg_get_function_identity_arguments(p.oid) "
                "AS args,p.prokind FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace"
                " WHERE "
            )
            + USER_SCHEMA
            + " ORDER BY n.nspname,p.proname,args"
        ).fetchall()
        types = user_types(c)
        default_policies = default_acls(c)
        if d.get("emptyOnly"):
            require(
                schemas == {"public"}
                and not relations
                and not functions
                and not types
                and not default_policies
            )
            return {}
        require(
            relations and all(r["relkind"] in ("r", "p", "S", "v", "i", "I") for r in relations)
        )
        require(all(t["typtype"] == "e" for t in types))
        base = [r for r in relations if r["relkind"] in ("r", "p")]
        require(base)
        require(
            not any(r["schema"] == "neon_auth" for r in relations) or d["managedAuth"] == "required"
        )
        if d["managedAuth"] == "required":
            require(any(r["schema"] == "neon_auth" for r in base))
        represented = {
            "public",
            *(r["schema"] for r in relations),
            *(f["nspname"] for f in functions),
            *(t["schema"] for t in types),
        }
        require(schemas == represented)
        for r in base:
            require(
                c.execute(
                    (
                        "SELECT has_schema_privilege(%s,'USAGE') AND "
                        "has_table_privilege(%s,'SELECT')"
                        " AS ok"
                    ),
                    (r["schema"], r["oid"]),
                ).fetchone()["ok"]
            )
        c.execute(
            sql.SQL("LOCK TABLE {} IN SHARE MODE").format(
                sql.SQL(",").join(sql.Identifier(r["schema"], r["name"]) for r in base)
            )
        )
        current = c.execute(
            "SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE "
            + USER_SCHEMA
            + " ORDER BY c.oid"
        ).fetchall()
        require(sorted(r["oid"] for r in relations) == [r["oid"] for r in current])
        require(types == user_types(c))
        type_definitions = {
            identifier(c, t["schema"], t["name"]): {
                "kind": "enum",
                "labels": [
                    row["enumlabel"]
                    for row in c.execute(
                        "SELECT enumlabel FROM pg_enum WHERE enumtypid=%s ORDER BY enumsortorder",
                        (t["oid"],),
                    )
                ],
            }
            for t in types
        }
        tables, projection, objects, sequences = {}, {}, {}, {}
        for r in base:
            key, table = (
                identifier(c, r["schema"], r["name"]),
                sql.Identifier(r["schema"], r["name"]),
            )
            columns = c.execute(
                (
                    "SELECT a.attname AS name,format_type(a.atttypid,a.atttypmod) AS "
                    "type,a.attnotnull AS not_null,\n                "
                    "pg_get_expr(v.adbin,v.adrelid) AS default,a.attidentity AS "
                    "identity,a.attgenerated AS generated,\n                CASE WHEN co.oid IS "
                    "NULL THEN NULL ELSE "
                    "jsonb_build_object('schema',cn.nspname,'definition',to_jsonb(co)-'oid'-'collnamespace'-'collowner')"
                    " END AS collation\n                FROM pg_attribute a LEFT JOIN pg_attrdef v"
                    " ON v.adrelid=a.attrelid AND v.adnum=a.attnum\n                LEFT JOIN "
                    "pg_collation co ON co.oid=a.attcollation LEFT JOIN pg_namespace cn ON "
                    "cn.oid=co.collnamespace\n                WHERE a.attrelid=%s AND a.attnum>0 "
                    "AND NOT a.attisdropped ORDER BY a.attnum"
                ),
                (r["oid"],),
            ).fetchall()
            constraints = c.execute(
                (
                    "SELECT conname,pg_get_constraintdef(oid) AS definition,convalidated FROM "
                    "pg_constraint WHERE conrelid=%s ORDER BY conname"
                ),
                (r["oid"],),
            ).fetchall()
            indexes = c.execute(
                (
                    "SELECT pg_get_indexdef(indexrelid) AS definition FROM pg_index WHERE "
                    "indrelid=%s ORDER BY pg_get_indexdef(indexrelid)"
                ),
                (r["oid"],),
            ).fetchall()
            security = c.execute(
                "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=%s", (r["oid"],)
            ).fetchone()
            policies = c.execute(
                (
                    "SELECT policyname,permissive,roles,cmd,qual,with_check FROM pg_policies "
                    "WHERE schemaname=%s AND tablename=%s ORDER BY policyname"
                ),
                (r["schema"], r["name"]),
            ).fetchall()
            triggers = c.execute(
                (
                    "SELECT pg_get_triggerdef(t.oid) AS "
                    "definition,t.tgenabled,pg_get_functiondef(t.tgfoid) AS function FROM "
                    "pg_trigger t WHERE t.tgrelid=%s AND NOT t.tgisinternal ORDER BY "
                    "t.tgname"
                ),
                (r["oid"],),
            ).fetchall()
            tables[key] = {
                **rows_digest(c, table),
                "columns": [x["name"] for x in columns],
                "definitionDigest": digest(
                    [columns, constraints, indexes, security, policies, triggers]
                ),
            }
            old = d.get("sourceProjection", {}).get("tables", {}).get(key)
            if old:
                projection[key] = rows_digest(c, table, old["columns"])
        for r in relations:
            key = identifier(c, r["schema"], r["name"])
            if r["relkind"] == "S":
                definition = c.execute(
                    (
                        "SELECT format_type(seqtypid,NULL) AS "
                        "type,seqstart::text,seqincrement::text,"
                        "seqmax::text,seqmin::text,seqcache::text,seqcycle"
                        " FROM pg_sequence WHERE seqrelid=%s"
                    ),
                    (r["oid"],),
                ).fetchone()
                ownership = c.execute(
                    (
                        "SELECT n.nspname AS schema,t.relname AS table,"
                        "a.attname AS column,d.deptype"
                        "\n                    FROM pg_depend d JOIN pg_class t ON "
                        "t.oid=d.refobjid "
                        "JOIN pg_namespace n ON n.oid=t.relnamespace\n                    JOIN "
                        "pg_attribute a ON a.attrelid=t.oid AND a.attnum=d.refobjsubid"
                        "\n                    WHERE d.classid='pg_class'::regclass AND "
                        "d.objid=%s AND"
                        " d.refclassid='pg_class'::regclass AND d.deptype IN ('a','i') ORDER BY "
                        "n.nspname,t.relname,a.attname"
                    ),
                    (r["oid"],),
                ).fetchall()
                state = c.execute(
                    sql.SQL("SELECT last_value::text,is_called FROM {}").format(
                        sql.Identifier(r["schema"], r["name"])
                    )
                ).fetchone()
                sequences[key] = {"definition": definition, "ownership": ownership, "state": state}
            elif r["relkind"] == "v":
                objects[key] = {
                    "kind": "view",
                    "definition": c.execute(
                        "SELECT pg_get_viewdef(%s,true) AS definition", (r["oid"],)
                    ).fetchone()["definition"],
                    "options": c.execute(
                        "SELECT reloptions FROM pg_class WHERE oid=%s", (r["oid"],)
                    ).fetchone()["reloptions"],
                    "columns": c.execute(
                        "SELECT a.attname,format_type(a.atttypid,a.atttypmod) AS type, "
                        "pg_get_expr(d.adbin,d.adrelid) AS default, "
                        "n.nspname AS collation_schema,co.collname FROM pg_attribute a "
                        "LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid "
                        "AND d.adnum=a.attnum LEFT JOIN pg_collation co "
                        "ON co.oid=a.attcollation LEFT JOIN pg_namespace n "
                        "ON n.oid=co.collnamespace WHERE a.attrelid=%s "
                        "AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum",
                        (r["oid"],),
                    ).fetchall(),
                    "triggers": c.execute(
                        (
                            "SELECT pg_get_triggerdef(t.oid) AS "
                            "definition,t.tgenabled,pg_get_functiondef(t.tgfoid) AS function FROM "
                            "pg_trigger t WHERE t.tgrelid=%s AND NOT t.tgisinternal ORDER BY "
                            "t.tgname"
                        ),
                        (r["oid"],),
                    ).fetchall(),
                }
        for f in functions:
            require(f["prokind"] in ("f", "p", "w"))
            objects[json.dumps([f["nspname"], f["proname"], f["args"]])] = {
                "kind": "function",
                "definition": c.execute(
                    "SELECT pg_get_functiondef(%s) AS definition", (f["oid"],)
                ).fetchone()["definition"],
            }
        require(default_policies == default_acls(c))
        require(not (objects.keys() & default_policies.keys()))
        objects.update(default_policies)
        names = {(r["schema"], r["name"]) for r in base}
        require(
            all(
                ("public", n) in names
                for n in (
                    "schema_migrations",
                    "identities",
                    "assets",
                    "jobs",
                    "artifacts",
                    "credit_accounts",
                    "credit_lots",
                    "credit_authorizations",
                    "credit_ledger_entries",
                    "credit_allocations",
                    "payment_events",
                    "stored_objects",
                )
            )
        )
        migrations = [
            r["name"]
            for r in c.execute('SELECT name FROM schema_migrations ORDER BY name COLLATE "C"')
        ]
        invariants = credit_invariants(c, base)
        r2 = r2_inventory(c, d, names)
        snapshot = (
            restored_snapshot_digest(c) if all(("public", n) in names for n in INVENTORY) else None
        )
        body = {
            "databaseCollation": database_collation(c),
            "types": type_definitions,
            "tables": tables,
            "schemaObjects": objects,
            "sequences": sequences,
            "migrationIds": migrations,
            "creditInvariants": invariants,
            "r2": r2,
            "sourceProjection": projection,
            "restoredSnapshotDigest": snapshot,
        }
        result = {
            "format": "kenkui-restore-inventory-v2",
            "environment": d["environment"],
            "target": d["target"],
            "mode": d["mode"],
            "capturedAt": datetime.now(UTC).isoformat(),
            "managedAuth": d["managedAuth"],
            "sourceProjectionCaptureDigest": d.get("sourceProjection", {}).get("captureDigest"),
            **body,
            "fullDigest": digest(body),
        }
        result["captureDigest"] = digest(
            {
                k: result[k]
                for k in (
                    "format",
                    "environment",
                    "target",
                    "mode",
                    "capturedAt",
                    "managedAuth",
                    "sourceProjectionCaptureDigest",
                    "fullDigest",
                )
            }
        )
        return result


def credit_invariants(c, base):
    orphans = 0
    for fk in c.execute(
        (
            "SELECT conrelid,confrelid,conkey,confkey FROM pg_constraint WHERE "
            "contype='f' AND conrelid=ANY(%s)"
        ),
        ([r["oid"] for r in base],),
    ).fetchall():

        def table(oid):
            r = c.execute(
                (
                    "SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON "
                    "n.oid=c.relnamespace WHERE c.oid=%s"
                ),
                (oid,),
            ).fetchone()
            return sql.Identifier(r["nspname"], r["relname"])

        def column(oid, num):
            return c.execute(
                "SELECT attname FROM pg_attribute WHERE attrelid=%s AND attnum=%s", (oid, num)
            ).fetchone()["attname"]

        pairs = [
            (column(fk["conrelid"], a), column(fk["confrelid"], b))
            for a, b in zip(fk["conkey"], fk["confkey"], strict=True)
        ]
        condition = sql.SQL(" AND ").join(
            sql.SQL("p.{}=t.{}").format(sql.Identifier(b), sql.Identifier(a)) for a, b in pairs
        )
        notnull = sql.SQL(" AND ").join(
            sql.SQL("t.{} IS NOT NULL").format(sql.Identifier(a)) for a, _ in pairs
        )
        orphans += c.execute(
            sql.SQL(
                "SELECT count(*) AS n FROM {} t WHERE {} AND NOT EXISTS (SELECT 1 FROM {} p "
                "WHERE {})"
            ).format(table(fk["conrelid"]), notnull, table(fk["confrelid"]), condition)
        ).fetchone()["n"]

    def count(query):
        return c.execute(query).fetchone()["n"]

    accounts = c.execute(
        'SELECT a.id::text AS "accountId",a.available_credits::text AS '
        '"availableCredits",\n        COALESCE((SELECT '
        "sum(l.credited-l.reserved-l.consumed) FROM credit_lots l WHERE "
        'l.account_id=a.id),0)::text AS "lotAvailableCredits",\n        '
        "COALESCE((SELECT sum(e.credits) FROM credit_ledger_entries e WHERE "
        'e.account_id=a.id),0)::text AS "netLedgerCredits",\n        '
        "(a.available_credits-COALESCE((SELECT sum(e.credits) FROM "
        "credit_ledger_entries e WHERE e.account_id=a.id),0))::text AS "
        '"openingResidualCredits"\n        FROM credit_accounts a ORDER BY a.id'
    ).fetchall()
    invalid_allocations, historical = allocation_exceptions(c)
    return {
        "accounts": accounts,
        "orphanReferences": orphans,
        "duplicatePayments": count(
            "SELECT count(*) AS n FROM (SELECT 1 FROM payment_events GROUP BY "
            "provider,provider_event_id HAVING count(*)>1) x"
        ),
        "invalidLots": count(
            "SELECT count(*) AS n FROM credit_lots WHERE credited<0 OR reserved<0 OR "
            "consumed<0 OR reserved+consumed>credited"
        ),
        "crossOwnerAuthorizations": count(
            "SELECT count(*) AS n FROM credit_authorizations z JOIN credit_accounts a ON "
            "a.id=z.account_id JOIN jobs j ON j.id=z.job_id WHERE "
            "a.identity_id<>j.owner_id"
        ),
        "invalidAllocations": invalid_allocations,
        "historicalSettlementCandidates": historical,
        "invalidLotAllocations": count(
            "SELECT count(*) AS n FROM credit_lots l WHERE l.reserved<>COALESCE((SELECT "
            "sum(a.credits) FROM credit_allocations a JOIN credit_authorizations z ON "
            "z.id=a.authorization_id WHERE a.lot_id=l.id AND z.status='reserved'),0) OR "
            "l.consumed<>COALESCE((SELECT sum(a.credits) FROM credit_allocations a JOIN "
            "credit_authorizations z ON z.id=a.authorization_id WHERE a.lot_id=l.id AND "
            "z.status='settled'),0)"
        ),
    }


def r2_inventory(c, d, names):
    store = R2AssetStore(None, bucket="inventory-only", key_salt=d["keySalt"])
    refs = []
    for row in c.execute('SELECT id FROM assets WHERE deleted_at IS NULL ORDER BY id COLLATE "C"'):
        refs.append(
            {"kind": "source", "resourceId": row["id"], "key": store._key("source", row["id"])}
        )
    for row in c.execute('SELECT path FROM artifacts ORDER BY path COLLATE "C"'):
        refs.append(
            {
                "kind": "artifact",
                "resourceId": row["path"],
                "key": store._key("artifact", row["path"]),
            }
        )
    if ("public", "job_checkpoints") in names:
        for row in c.execute('SELECT id FROM job_checkpoints WHERE ready ORDER BY id COLLATE "C"'):
            refs.append(
                {
                    "kind": "checkpoint",
                    "resourceId": row["id"],
                    "key": store._key("checkpoint", row["id"]),
                }
            )
    resources = {
        "source": {r["id"] for r in c.execute("SELECT id FROM assets")},
        "artifact": {r["path"] for r in c.execute("SELECT path FROM artifacts")},
    }
    orphans = 0
    for row in c.execute(
        "SELECT object_kind,resource_id,opaque_object_key FROM stored_objects WHERE "
        'deleted_at IS NULL ORDER BY opaque_object_key COLLATE "C"'
    ):
        if row["object_kind"] in resources and (
            row["resource_id"] not in resources[row["object_kind"]]
            or store._key(row["object_kind"], row["resource_id"]) != row["opaque_object_key"]
        ):
            orphans += 1
        refs.append(
            {
                "kind": row["object_kind"],
                "resourceId": row["resource_id"],
                "key": row["opaque_object_key"],
            }
        )
    refs.sort(key=lambda r: (r["kind"], r["key"], r["resourceId"] or ""))
    keys = set(d["r2Keys"])
    return {
        "references": refs,
        "missingObjects": sum(r["key"] not in keys for r in refs),
        "orphanReferences": orphans,
    }


if __name__ == "__main__":
    try:
        request = json.load(sys.stdin)
        if request.get("action") == "validate":
            validate(request)
            print("{}")
        else:
            print(json.dumps(capture(request), separators=(",", ":")))
    except Exception:
        print("Private inventory request failed", file=sys.stderr)
        sys.exit(1)
