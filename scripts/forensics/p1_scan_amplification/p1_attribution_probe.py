"""P1 scan-attribution probe (working version).

Stats reads must happen on a FRESH connection: pg_stat_clear_snapshot() in
the same implicit transaction makes the counters appear frozen at the
snapshot taken before the queries ran (probed directly: 0 delta while the
same connection's own seq scans accumulate).
"""
from __future__ import annotations

import sys
import time
from datetime import UTC, datetime, timedelta

import psycopg

DSN_DB = "postgresql://postgres@127.0.0.1:55432/nse_p1_scan"

CUTOFF = (datetime.now(UTC) - timedelta(hours=168)).isoformat()

# (class name, sql, args, calls_per_sec from the source)
CLASSES: list[tuple[str, str, tuple, float]] = [
    (
        "SSE get_recent_predictions(40) @5.0Hz",
        "SELECT request_id, symbol, action, confidence, proposed_entry, stop_loss, "
        "take_profit, regime, generated_at, payload, execution_mode, reason_code, "
        "decision_stage, blocked_by FROM audit_signals ORDER BY id DESC LIMIT 40",
        (),
        5.0,
    ),
    (
        "v1 signals/latest @1/15s",
        "SELECT request_id, symbol, action, confidence, proposed_entry, stop_loss, "
        "take_profit, regime, generated_at, payload, execution_mode, reason_code, "
        "decision_stage, blocked_by, htf_score, smc_score, confidence_before_filters, "
        "confidence_after_filters FROM audit_signals ORDER BY id DESC LIMIT 1",
        (),
        1.0 / 15.0,
    ),
    (
        "v1 decisions/stats Q1 @1/60s",
        "SELECT action, decision_stage, COUNT(*) AS n FROM audit_signals "
        "WHERE generated_at >= %s GROUP BY action, decision_stage ORDER BY n DESC",
        (CUTOFF,),
        1.0 / 60.0,
    ),
    (
        "v1 decisions/no-trade Q3 @1/60s",
        "SELECT COALESCE(reason_code, 'UNKNOWN') AS reason, COUNT(*) AS n FROM audit_signals "
        "WHERE action = 'NO_TRADE' GROUP BY reason ORDER BY n DESC",
        (),
        1.0 / 60.0,
    ),
    (
        "v1 decisions/no-trade COUNT Q3b @1/60s",
        "SELECT COUNT(*) AS n FROM audit_signals WHERE action = 'NO_TRADE'",
        (),
        1.0 / 60.0,
    ),
    (
        "op summary Q2 @1/15s",
        "SELECT action, decision_stage, blocked_by, generated_at FROM audit_signals "
        "WHERE id IN (SELECT id FROM audit_signals ORDER BY id DESC LIMIT 20000)",
        (),
        1.0 / 15.0,
    ),
]


def reset() -> None:
    with psycopg.connect(DSN_DB, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT pg_stat_reset_single_table_counters('audit_signals'::regclass)")


def stats() -> dict[str, int]:
    """Read on a FRESH connection so the stats snapshot is current."""
    with psycopg.connect(DSN_DB, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                """SELECT seq_scan, seq_tup_read, idx_scan, idx_tup_fetch
                   FROM pg_stat_user_tables WHERE relname='audit_signals'"""
            )
            keys = ("seq_scan", "seq_tup_read", "idx_scan", "idx_tup_fetch")
            return dict(zip(keys, (int(v or 0) for v in cur.fetchone())))


def run_class(name: str, sql: str, args: tuple, rate: float, seconds: float) -> dict:
    reset()
    before = stats()
    with psycopg.connect(DSN_DB) as c:
        with c.cursor() as cur:
            t0 = time.perf_counter()
            n = 0
            next_at = t0
            deadline = t0 + seconds
            while True:
                now = time.perf_counter()
                if now >= deadline:
                    break
                if now >= next_at:
                    cur.execute(sql, args)
                    cur.fetchall()
                    n += 1
                    next_at = t0 + n / rate
                else:
                    time.sleep(min(0.001, max(0.0, next_at - now)))
            elapsed = time.perf_counter() - t0
    after = stats()
    d = {k: after[k] - before[k] for k in before}
    d["calls"] = n
    d["elapsed_s"] = round(elapsed, 2)
    d["rate_hz"] = round(n / elapsed, 3)
    print(
        f"  {name}: calls={n} seq_scan+={d['seq_scan']} "
        f"seq_tup_read+={d['seq_tup_read']} idx_scan+={d['idx_scan']}"
    )
    return d


def main() -> int:
    run_sec = float(sys.argv[1]) if len(sys.argv) > 1 else 8.0
    print(f"=== scan attribution (run_sec={run_sec}) ===")
    results: dict[str, dict] = {}
    for name, sql, args, rate in CLASSES:
        results[name] = run_class(name, sql, args, rate, run_sec)

    print("\n=== extrapolation to the forensic 18.11h window ===")
    hours = 18.11
    total_seq = 0.0
    total_tup = 0.0
    for name, r in results.items():
        calls_18h = r["rate_hz"] * hours * 3600
        seq_18h = r["seq_scan"] * calls_18h / max(r["calls"], 1)
        tup_18h = r["seq_tup_read"] * calls_18h / max(r["calls"], 1)
        total_seq += seq_18h
        total_tup += tup_18h
        print(
            f"  {name}: {calls_18h:9.0f} calls -> {seq_18h:9.0f} seq_scans, {tup_18h:13.0f} seq_tup_read"
        )
    print(f"  TOTAL: {total_seq:9.0f} seq_scans, {total_tup:13.0f} seq_tup_read")
    print("  forensic observed: 533577 seq_scans, 4927831046 seq_tup_read")
    return 0


if __name__ == "__main__":
    sys.exit(main())
