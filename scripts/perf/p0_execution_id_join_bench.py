"""P0 pg-read slow query — before/after benchmark evidence.

Reproduces the measurements behind AUDIT-0012 / idx_exp_outcome_exec.
Run:  PYTHONPATH=src python scripts/perf/p0_execution_id_join_bench.py

Not a test (no absolute-ms assertions): this records the EXPLAIN-level
evidence plus wall-clock medians so the fix is reproducible on any machine.
The regression TESTS live in tests/unit/test_p0_outcome_execution_id_index.py
(plan-shape assertions, which are deterministic across machines).
"""

from __future__ import annotations

import argparse
import sqlite3
import statistics
import tempfile
import time
from pathlib import Path

#: Production-shaped sizes measured on nexusdb @ 2026-10-01.
N_EXPERIENCES = 22_000
N_OUTCOMES = 4_391
N_TICKETS_OBSERVED = 315

HOT_SQL = (
    "SELECT o.execution_id, e.experience_id, e.strategy_id, e.strategy_version, "
    "e.model_id, e.model_version, e.feature_schema_id, e.feature_dimension "
    "FROM audit_experience_outcomes o "
    "JOIN audit_experiences e ON e.idempotency_key = o.idempotency_key "
    "WHERE o.execution_id IN ({placeholders})"
)

_SCHEMA_MODULES = (
    "nexus_scalp.database.engine",
    "nexus_scalp.database.models",
)


def _build_db(db_path: Path) -> tuple[sqlite3.Connection, list[str]]:
    from nexus_scalp.database.engine import DatabaseMigrationEngine
    from nexus_scalp.database.models import DatabaseDomain

    eng = DatabaseMigrationEngine(db_path=db_path, domain=DatabaseDomain.AUDIT)
    res = eng.migrate()
    assert res["state"] in {"DB_MIGRATION_SUCCEEDED", "DB_MIGRATION_NOT_REQUIRED"}, res
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    for i in range(N_EXPERIENCES):
        con.execute(
            "INSERT INTO audit_experiences (experience_id, request_id, execution_id, "
            "idempotency_key, symbol, timeframe, strategy_id, decision_timestamp, "
            "action, entry_reason, proposed_entry, stop_loss, take_profit, payload) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"exp_{i}",
                f"req_{i}",
                "",
                f"key_{i}",
                "EURUSD",
                "M1",
                "strat_a",
                "2026-09-01 10:00:00",
                "BUY",
                "reason",
                2000.0,
                1990.0,
                2010.0,
                "{}",
            ),
        )
    tickets: list[str] = []
    for i in range(N_OUTCOMES):
        ticket = str(150_000_000_000 + i)
        tickets.append(ticket)
        con.execute(
            "INSERT INTO audit_experience_outcomes (idempotency_key, execution_id, "
            "outcome_timestamp, payload) VALUES (?,?,?,?)",
            (f"key_{i}", ticket, "2026-09-02 10:00:00", "{}"),
        )
    con.commit()
    con.execute("ANALYZE")
    con.commit()
    return con, tickets


def _sqlite_plan(con: sqlite3.Connection, sql: str, args: tuple) -> str:
    con.row_factory = sqlite3.Row
    return "\n".join(
        " ".join(str(r["detail"]).split())
        for r in con.execute("EXPLAIN QUERY PLAN " + sql, args).fetchall()
    )


def _bench(con: sqlite3.Connection, sql: str, args: tuple, runs: int = 5) -> tuple[float, int]:
    ts: list[float] = []
    rows = 0
    for _ in range(runs):
        t0 = time.perf_counter()
        rows = len(con.execute(sql, args).fetchall())
        ts.append((time.perf_counter() - t0) * 1000)
    return statistics.median(ts), rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tmp", default=None, help="scratch dir for the benchmark DB")
    args = parser.parse_args()

    from nexus_scalp.database.engine import DatabaseMigrationEngine  # noqa: F401
    from nexus_scalp.database.models import DatabaseDomain  # noqa: F401

    if args.tmp:
        scratch = Path(args.tmp)
        scratch.mkdir(parents=True, exist_ok=True)
    else:
        scratch = Path(tempfile.mkdtemp(prefix="p0_exec_join_bench_"))
    db_file = scratch / "bench.db"
    if db_file.exists():
        db_file.unlink()  # hermetic: never benchmark against a stale corpus
    con, tickets = _build_db(db_file)

    print(f"corpus: {N_EXPERIENCES} experiences / {N_OUTCOMES} outcomes (production-shaped)")
    print(f"probe tickets: {N_TICKETS_OBSERVED}\n")

    probe = tickets[:N_TICKETS_OBSERVED]
    placeholders = ",".join("?" * len(probe))
    sql = HOT_SQL.format(placeholders=placeholders)
    args_ = tuple(probe)

    with_index = _sqlite_plan(con, sql, args_)
    ms_with, rows = _bench(con, sql, args_)

    con.execute("DROP INDEX idx_exp_outcome_exec")
    con.execute("ANALYZE")
    without_index = _sqlite_plan(con, sql, args_)
    ms_without, rows_without = _bench(con, sql, args_)

    print("=== WITH idx_exp_outcome_exec (AUDIT-0012 applied) ===")
    for line in with_index.splitlines():
        print(f"  {line}")
    print(f"  rows={rows}  wall-clock median={ms_with:.3f}ms\n")

    print("=== WITHOUT the index (simulated AUDIT-0012 revert / pre-fix) ===")
    for line in without_index.splitlines():
        print(f"  {line}")
    print(f"  rows={rows_without}  wall-clock median={ms_without:.3f}ms\n")

    seq_before = "SCAN o" in without_index
    seq_after = "SCAN o" in with_index
    print("=== VERDICT ===")
    print(f"  full scan before fix : {seq_before}")
    print(f"  full scan after fix  : {seq_after}")
    print(f"  plan uses idx_exp_outcome_exec: {'idx_exp_outcome_exec' in with_index}")
    print(f"  result rows identical: {rows == rows_without} ({rows})")
    if ms_without > 0:
        print(f"  wall-clock speedup   : {ms_without / ms_with:.2f}x")
    print(
        "\n  NOTE on the wall-clock line: SQLite serves this corpus from a small\n"
        "  number of cached pages, so a cached single-process benchmark does NOT\n"
        "  show the win (and can even regress slightly). The defect this fixes is\n"
        "  PostgreSQL-side: EXPLAIN ANALYZE on nexusdb showed the IN-list resolved\n"
        "  as a Seq Scan and then hashed against a 22k-row Seq Scan of\n"
        "  audit_experiences; with the index the outcomes side becomes an Index Scan\n"
        "  and the shared-buffer cost of the probe drops from ~2364 pages to ~130.\n"
        "  See the PR body for the PostgreSQL EXPLAIN ANALYZE before/after.\n"
    )

    con.close()
    return 0 if (not seq_after and rows == rows_without) else 1


if __name__ == "__main__":
    raise SystemExit(main())
