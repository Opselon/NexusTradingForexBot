#!/usr/bin/env python
"""Materialize the CI runner's ``nexusdb``: a provisioned PostgreSQL database.

WHY THIS EXISTS
===============
``tests/unit/test_pg_schema_convergence.py`` contains a comparison that is
only meaningful against a MIGRATED database named ``nexusdb``: it diffs the
live schema against a from-scratch provision to prove the two converge, and
asserts a recorded divergence set (``LIVE_ONLY_TABLES``). On a developer box
that database exists because the application has been run against it for
months. On a CI runner the PostgreSQL service container creates exactly one
bare database and nothing ever produces ``nexusdb``, so the comparison had no
target and the test could not pass there.

The alternative — skipping the comparison when ``nexusdb`` is missing — is
worse: it would convert a genuine schema-convergence assertion into a
permanent green-by-absence, which is the exact failure mode this whole CI lane
exists to eliminate (an empty live database behind a green suite).

So CI provisions the target instead. The database is built through the
APPLICATION'S OWN provisioning path (``database.fabric.provision_domain`` plus
the same statement replay the boot path uses), never a hand-written schema, so
the comparison stays a real check of the shipped provisioner.

The result is deterministic and equivalent, not a copy of a developer's box:
every domain the provisioner knows is bootstrapped, the replay is applied, and
the store-owned tables that no domain authors are created by the stores that
own them. That is precisely the schema a fresh install converges to — which is
what the test is for.

EXTRA DOMAINS
=============
``--also`` names extra databases created the same way. ``test_database_portability``
and the store-level arms connect to the database named in
``NSE_PG_TEST_URL`` (``nse_audit`` in CI), so that one is provisioned too.

EXIT CODES
==========
0 — the database(s) exist and are provisioned (or ``--check`` confirms so).
1 — provisioning failed; the reason is printed. Never a silent success.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))


def _dsn_for(base_dsn: str, dbname: str) -> str:
    """Re-point a libpq DSN at ``dbname``."""
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    parts = conninfo_to_dict(base_dsn)
    parts["dbname"] = dbname
    parts.pop("database", None)
    return make_conninfo("", **parts)


def _admin_dsn(base_dsn: str) -> str:
    """The instance-level DSN (``postgres``), for CREATE/DROP DATABASE."""
    return _dsn_for(base_dsn, "postgres")


def _provision(dbname: str, base_dsn: str, *, reset: bool) -> list[str]:
    """Create ``dbname`` and provision it through the app's own path."""
    import psycopg

    from nexus_scalp.database.fabric import provision_domain
    from nexus_scalp.database.migration import _DOMAIN_STATEMENTS, sqlite_ddl_statements
    from nexus_scalp.database.migration.pg_schema import translate_ddl

    notes: list[str] = []
    target = _dsn_for(base_dsn, dbname)

    with psycopg.connect(_admin_dsn(base_dsn), autocommit=True) as conn:
        if reset:
            # Terminate stragglers so DROP DATABASE cannot block.
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (dbname,),
                )
                cur.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
            if cur.fetchone() is None:
                cur.execute(f'CREATE DATABASE "{dbname}"')
                notes.append(f"created database {dbname}")
            else:
                notes.append(f"database {dbname} already present")

    # The boot replay: every authored statement, translated for PostgreSQL.
    with psycopg.connect(target, autocommit=True) as conn:
        with conn.cursor() as cur:
            for stmt in sqlite_ddl_statements():
                cur.execute(translate_ddl(stmt))
    notes.append(f"applied the DDL replay ({len(sqlite_ddl_statements())} statements)")

    # Every registered domain, through the provisioner the app ships.
    from nexus_scalp.database.fabric import unregister_domain_backend

    provisioned, failed = [], []
    for domain in sorted(_DOMAIN_STATEMENTS):
        try:
            backend = provision_domain(domain, target, min_size=1, max_size=4)
            provisioned.append(domain)
            closer = getattr(backend, "close", None)
            if callable(closer):
                closer()
            # Unregister so the pools are closed deterministically instead of
            # being torn down by the garbage collector at interpreter exit
            # (psycopg_pool's __del__ then prints "cannot join current thread"
            # from a dead thread context — noise that reads like a failure).
            unregister_domain_backend(domain)
        except Exception as exc:
            failed.append(f"{domain}: {type(exc).__name__}: {exc}")
    notes.append(f"provisioned {len(provisioned)} domains")
    if failed:
        raise RuntimeError("domain provisioning failed: " + "; ".join(failed))

    # Tables the STORES own (no domain authors them). They are created by the
    # stores' own schema bootstrap on whatever database they are pointed at, so
    # the provisioned target must run it too or the comparison would report
    # them as live-only.
    from nexus_scalp.ai_providers.registry import ProviderRegistryStore

    try:
        # The store builds its driver and bootstraps its tables in __init__
        # (``_ensure_schema``), so constructing it against the target DSN *is*
        # the provisioning step.
        store = ProviderRegistryStore(target)
        closer = getattr(store, "close", None)
        if callable(closer):
            closer()
        notes.append("provisioned the provider registry store tables")
    except Exception as exc:
        raise RuntimeError(f"provider registry store provisioning failed: {exc}") from exc

    return notes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--base-dsn",
        default=os.environ.get("NSE_PG_TEST_URL", ""),
        help="instance DSN (default: NSE_PG_TEST_URL)",
    )
    ap.add_argument(
        "--database",
        default="nexusdb",
        help="database to materialize (default: nexusdb — the migrated target)",
    )
    ap.add_argument(
        "--also",
        action="append",
        default=[],
        help="additional database to materialize the same way (repeatable)",
    )
    ap.add_argument("--no-reset", action="store_true", help="keep an existing database")
    args = ap.parse_args(argv)

    if not args.base_dsn:
        print(
            "PG-FIXTURE: no base DSN — set NSE_PG_TEST_URL or pass --base-dsn",
            file=sys.stderr,
        )
        return 1

    reset = not args.no_reset
    targets = [args.database, *args.also]
    failed = False
    for dbname in targets:
        try:
            for note in _provision(dbname, args.base_dsn, reset=reset):
                print(f"PG-FIXTURE [{dbname}] {note}")
        except Exception as exc:
            print(f"PG-FIXTURE [{dbname}] FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
            failed = True

    if failed:
        return 1
    print(f"PG-FIXTURE: ready — {', '.join(targets)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
