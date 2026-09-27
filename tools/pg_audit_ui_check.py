"""Lane A UI verification: the diagnostics/console surface reads REAL audit rows.

Boots the db_console FastAPI router in-process (no server) and drives the
endpoints the operator's diagnostics/review console calls against a
PostgreSQL-backed audit domain:

  GET /api/db/console/tables   -> live row counts (not documented defaults)
  GET /api/db/console/rows     -> real persisted rows

The point of the check: under a PostgreSQL provider the audit read path used
to degrade to a documented default (``no read plane registered``); after the
portability fixes the console must return the rows the producers actually
landed.  Read-only against the target it points at.

Usage: NEXUS_AUDIT_DB=postgresql://... python tools/pg_audit_ui_check.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from nexus_scalp.web.db_console import router  # noqa: E402

DSN = os.environ["NEXUS_AUDIT_DB"]


def main() -> int:
    from fastapi import FastAPI

    api = FastAPI()
    api.include_router(router)
    client = TestClient(api)

    failures: list[str] = []

    resp = client.get("/api/db/console/tables", params={"database": "audit"})
    payload = resp.json()
    print(f"tables -> http={resp.status_code} success={payload.get('success')}")
    if not payload.get("success"):
        failures.append(f"console/tables failed: {payload}")
        print(payload)
    else:
        print(f"  provider={payload.get('provider')}")
        counts = {t["name"]: t["rows"] for t in payload.get("tables", [])}
        for want_table, want_rows in (
            ("audit_guard_telemetry", 2),
            ("audit_signals", 1),
            ("audit_orders", 1),
            ("audit_executions", 1),
            ("audit_account_snapshots", 1),
            ("audit_ledger", 1),
            ("audit_paper_executions", 1),
            ("runtime_risk_state", 1),
        ):
            got = counts.get(want_table)
            print(f"  {want_table}: rows={got}")
            if got != want_rows:
                failures.append(f"console/tables {want_table}: expected {want_rows}, got {got}")
        # A table that reports None means the count failed -> degraded read.
        none_tables = [t for t, r in counts.items() if r is None]
        if none_tables:
            failures.append(f"row counts came back None for {none_tables[:5]}")

    # Real ROWS, not a documented default.
    for table in ("audit_signals", "audit_guard_telemetry", "runtime_risk_state"):
        resp = client.get(
            "/api/db/console/rows",
            params={"database": "audit", "table": table, "limit": 10},
        )
        payload = resp.json()
        if not payload.get("success"):
            failures.append(f"console/rows {table} failed: {payload.get('error')}")
            print(f"rows {table} -> FAIL {payload.get('error')}")
            continue
        rows = payload.get("rows", [])
        columns = payload.get("columns", [])
        print(f"rows {table} -> http={resp.status_code} n={len(rows)} cols={len(columns)}")
        if not rows:
            failures.append(f"console/rows {table}: no rows returned (degraded read?)")
        else:
            print(f"  first row keys: {list(rows[0].keys())[:6]}")

    if failures:
        print("\nUI VERIFICATION FAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    print("\nDIAGNOSTICS/REVIEW UI READS REAL AUDIT ROWS FROM POSTGRESQL")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
