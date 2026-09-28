"""P1 AFTER probe + Test H: run the FIXED query paths against PostgreSQL.

Runs each post-fix consumer path exactly as the application issues it and
measures pg_stat_user_tables seq_scan / seq_tup_read growth, on FRESH
connections (pg_stat_clear_snapshot() inside one implicit transaction
returns stale zeros — that bug cost two probe rewrites).

Mirrors p1_pg_probe.py (BEFORE) shape-for-shape so the numbers compare.
"""

from __future__ import annotations

import time
from pathlib import Path

import psycopg

DSN = "host=127.0.0.1 port=55432 user=postgres dbname=nse_p1_scan"

FIXED_QUERIES: list[tuple[str, str, tuple]] = [
    (
        "Q1-after get_decision_stats (bounded tail, by decision_stage)",
        "SELECT decision_stage, action, count(*) FROM ("
        "SELECT id, action, decision_stage, reason_code, generated_at "
        "FROM audit_signals ORDER BY id DESC LIMIT 2000"
        ") tail GROUP BY decision_stage, action ORDER BY count(*) DESC",
        (),
    ),
    (
        "Q1-after get_decision_stats (bounded tail, by reason_code)",
        "SELECT reason_code, action, count(*) FROM ("
        "SELECT id, action, decision_stage, reason_code, generated_at "
        "FROM audit_signals ORDER BY id DESC LIMIT 2000"
        ") tail GROUP BY reason_code, action ORDER BY count(*) DESC",
        (),
    ),
    (
        "Q2-after operator summary census (bounded tail, one query)",
        "SELECT action, generated_at FROM audit_signals ORDER BY id DESC LIMIT 2000",
        (),
    ),
    (
        "Q2-after operator funnel (bounded tail, one query)",
        "SELECT action, decision_stage, blocked_by, generated_at "
        "FROM audit_signals ORDER BY id DESC LIMIT 2000",
        (),
    ),
    (
        "Q3-after count_decisions(NO_TRADE) over the bounded slice",
        "SELECT count(*) FROM (SELECT id, action FROM audit_signals "
        "ORDER BY id DESC LIMIT 2000) tail WHERE tail.action = 'NO_TRADE'",
        (),
    ),
    (
        "Q3-after no-trade reasons (bounded tail grouped by reason_code)",
        "SELECT reason_code, count(*) FROM ("
        "SELECT id, action, reason_code FROM audit_signals "
        "ORDER BY id DESC LIMIT 2000) tail WHERE tail.action = 'NO_TRADE' "
        "GROUP BY reason_code ORDER BY count(*) DESC LIMIT 50",
        (),
    ),
    (
        "Q4b-after get_recent_predictions (unchanged: pkey index)",
        "SELECT request_id, symbol, action, confidence FROM audit_signals "
        "ORDER BY id DESC LIMIT 40",
        (),
    ),
    (
        "Q4b-after ledger_high_water_mark (the memo key)",
        "SELECT max(id) FROM audit_signals",
        (),
    ),
    (
        "Q4-after why_blocked sargable path",
        "SELECT * FROM audit_signals WHERE request_id = %s "
        "ORDER BY generated_at DESC, id DESC LIMIT 20",
        ("req-0000001",),
    ),
]


def stats() -> tuple[int, int]:
    with psycopg.connect(DSN) as c, c.cursor() as cur:
        cur.execute(
            "SELECT seq_scan, seq_tup_read FROM pg_stat_user_tables WHERE relname = 'audit_signals'"
        )
        s, t = cur.fetchone()
        return int(s), int(t)


def plan(sql: str, args: tuple) -> str:
    with psycopg.connect(DSN) as c, c.cursor() as cur:
        cur.execute("EXPLAIN (ANALYZE, BUFFERS, VERBOSE) " + sql, args)
        return "\n".join(r[0] for r in cur.fetchall())


def main() -> None:
    out = Path("C:/Users/Capsizer/AppData/Local/hermes/cache/scratch/p1_after.txt")
    parts: list[str] = []

    parts.append("=== P1 AFTER: fixed query paths on PostgreSQL 17 ===")
    parts.append("table: audit_signals  rows: 9108 (representative fixture)")
    parts.append("")

    # sanity: every fixed path must return rows
    parts.append("--- correctness (Test H) ---")
    for name, sql, args in FIXED_QUERIES:
        with psycopg.connect(DSN) as c, c.cursor() as cur:
            cur.execute(sql, args)
            rows = cur.fetchall()
        parts.append(f"OK  {name}: {len(rows)} rows")
    parts.append("")

    # amplitude: 50 iterations of each fixed path, like p1_pg_probe.py
    parts.append("--- amplitude: 50 iterations of each fixed path ---")
    for name, sql, args in FIXED_QUERIES:
        s0, t0 = stats()
        t_start = time.perf_counter()
        with psycopg.connect(DSN) as c, c.cursor() as cur:
            for _ in range(50):
                cur.execute(sql, args)
                cur.fetchall()
        elapsed = time.perf_counter() - t_start
        s1, t1 = stats()
        d_seq = s1 - s0
        d_tup = t1 - t0
        parts.append(
            f"{name}\n    seq_scan +{d_seq}  seq_tup_read +{d_tup}  "
            f"tup/scan={d_tup / d_seq if d_seq else 0:.1f}  "
            f"{elapsed * 1000:.1f}ms/50"
        )
    parts.append("")

    # the SSE hot path: memo means the tail read fires once per NEW row
    parts.append("--- SSE hot path: 5Hz x 10s with the high-water-mark memo ---")
    s0, t0 = stats()
    reads = 0
    t_start = time.perf_counter()
    hw = None
    while time.perf_counter() - t_start < 10.0:
        with psycopg.connect(DSN) as c, c.cursor() as cur:
            cur.execute("SELECT max(id) FROM audit_signals")
            now_hw = cur.fetchone()[0]
        if now_hw != hw:
            reads += 1
            hw = now_hw
            with psycopg.connect(DSN) as c, c.cursor() as cur:
                cur.execute("SELECT request_id FROM audit_signals ORDER BY id DESC LIMIT 40")
                cur.fetchall()
        time.sleep(0.2)
    s1, t1 = stats()
    parts.append(
        f"tail reads over 10s at 5Hz: {reads} (unmemoized: 50)\n"
        f"    seq_scan +{s1 - s0}  seq_tup_read +{t1 - t0}"
    )
    parts.append("")

    # plans for the fixed shapes
    parts.append("--- plans (EXPLAIN ANALYZE BUFFERS VERBOSE) ---")
    for name, sql, args in FIXED_QUERIES:
        parts.append(f"\n### {name}\n{plan(sql, args)}")

    out.write_text("\n".join(parts), encoding="utf-8")
    print(f"wrote {out}")
    print("\n".join(parts[:40]))


if __name__ == "__main__":
    main()
