#!/usr/bin/env python3
"""Fail-closed test-database isolation guard for CI lanes.

The self-hosted runners are persistent machines: a previous run (or a
local gate on the same box) may have left a persisted ``app_settings.db``
pointing at PostgreSQL, and the audit test-isolation seam plus the
persisted provider would otherwise resolve the test DB from that machine
state — i.e. CI would test against a real/shared database.

The CI jobs pin the test surface at JOB level (sqlite provider, per-run
temp paths under RUNNER_TEMP); this guard proves the pinning actually
took effect through the repo's own resolver, before any test runs:

* the ``audit`` domain resolves to SQLite with no PostgreSQL host, and
* no ``NSE_DATABASE__PG_HOST`` override is present in the environment.

Exit 0 = isolated (sqlite only). Anything else exits non-zero with a
plain-language reason, so the lane fails instead of touching a shared DB.
"""

from __future__ import annotations

import os
import sys


def main() -> int:
    pg_host = os.environ.get("NSE_DATABASE__PG_HOST", "").strip()
    if pg_host:
        print(
            f"::error::test-db isolation FAILED: NSE_DATABASE__PG_HOST={pg_host!r} "
            "is set — refusing to test against a configured PostgreSQL host",
            file=sys.stderr,
        )
        return 1

    try:
        from nexus_scalp.database.config import load_database_config
    except Exception as exc:
        print(f"::error::test-db isolation FAILED: cannot load DB config: {exc}", file=sys.stderr)
        return 1

    try:
        cfg = load_database_config("audit")
    except Exception as exc:
        print(
            f"::error::test-db isolation FAILED: audit config did not resolve: {exc}",
            file=sys.stderr,
        )
        return 1

    provider = getattr(cfg, "provider", None)
    provider_value = getattr(provider, "value", provider)
    host = getattr(cfg, "host", "") or ""
    if provider_value != "sqlite" or host:
        print(
            "::error::test-db isolation FAILED: audit domain resolved to "
            f"provider={provider_value!r} host={host!r} — expected sqlite with "
            "no host. The runner's persisted settings may point at a shared "
            "database; refusing to run tests.",
            file=sys.stderr,
        )
        return 1

    print(
        "test-db isolation: OK "
        f"(audit provider={provider_value} sqlite_path={getattr(cfg, 'sqlite_path', '')!r})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
