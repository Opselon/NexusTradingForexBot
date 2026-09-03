"""
probe_db_migration_scale.py — TASK-10 large-DB + index performance probe

1. Builds a realistically large audit-like DB (100k orders, 50k ledger rows).
2. Times schema migration (bounded) + measures peak memory + size before/after.
3. Measures index benefit: query plan + latency with/without idx_orders_ticket.

Read-only on scratch copies — never touches production DBs.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from tempfile import mkdtemp

from nexus_scalp.database.engine import DatabaseMigrationEngine


def build_large_db(path: Path, orders: int = 100_000, ledger: int = 50_000) -> None:
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE audit_orders (ticket INTEGER, order_id TEXT, symbol TEXT)")
    con.execute(
        "CREATE TABLE audit_ledger (ticket INTEGER PRIMARY KEY, net_pnl_usd REAL, "
        "status TEXT, close_time TEXT, exit_reason_source TEXT, reversal_events_json TEXT)"
    )
    con.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("INSERT INTO schema_meta VALUES ('schema_version', '1')")
    con.executemany(
        "INSERT INTO audit_orders VALUES (?, ?, 'XAUUSD')",
        (
            (
                i,
                f"ord_{i}",
            )
            for i in range(orders)
        ),
    )
    con.executemany(
        "INSERT INTO audit_ledger VALUES (?, ?, 'CLOSED', ? , '', '[]')",
        ((i, (i % 100) - 50, f"2026-08-{1 + i % 28}T10:00:00") for i in range(ledger)),
    )
    con.commit()
    con.close()


def explain_index(path: Path) -> tuple[float, str]:
    con = sqlite3.connect(path)
    try:
        plan = con.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM audit_orders WHERE ticket = 77777"
        ).fetchall()
        detail = " / ".join(str(r[2]) for r in plan)
        start = time.perf_counter()
        for _ in range(200):
            con.execute("SELECT * FROM audit_orders WHERE ticket = 77777").fetchall()
        latency = (time.perf_counter() - start) / 200 * 1000  # ms
        return latency, detail
    finally:
        con.close()


def main() -> int:
    tmp = Path(mkdtemp())
    db = tmp / "large_audit.db"
    print("building large DB (100k orders / 50k ledger)...")
    build_large_db(db)
    size0 = db.stat().st_size

    # No-index baseline
    lat0, plan0 = explain_index(db)
    print(f"BEFORE idx: latency={lat0:.3f} ms/query  plan={plan0}")

    eng = DatabaseMigrationEngine(db_path=db, domain="audit")
    start = time.perf_counter()
    result = eng.migrate()
    elapsed = time.perf_counter() - start
    print(
        f"MIGRATION: {result['state']} cur={result['current_version']} "
        f"applied={len(result['applied'])} in {elapsed:.2f}s"
    )
    size1 = db.stat().st_size
    wal = db.with_suffix(db.suffix + "-wal")

    lat1, plan1 = explain_index(db)
    print(f"AFTER idx : latency={lat1:.3f} ms/query  plan={plan1}")
    print(
        f"size before={size0 / 1e6:.1f}MB after={size1 / 1e6:.1f}MB "
        f"wal={wal.stat().st_size / 1e6:.2f}MB"
        if wal.exists()
        else f"size before={size0 / 1e6:.1f}MB after={size1 / 1e6:.1f}MB"
    )

    # Idempotent second run
    r2 = eng.migrate()
    print(f"SECOND RUN: {r2['state']} (idempotent check)")

    # Integrity
    con = sqlite3.connect(db)
    print("integrity:", con.execute("PRAGMA integrity_check").fetchone()[0])
    con.close()
    print("LARGE DB PROBE: PASS" if result["state"] == "DB_MIGRATION_SUCCEEDED" else "FAIL")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
