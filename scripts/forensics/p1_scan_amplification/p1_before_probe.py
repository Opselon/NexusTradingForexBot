"""P1 baseline + query-shape probe against the isolated PG17 instance.

Creates a representative audit_signals table (mirroring the live shape),
captures pg_stat_user_tables before/after, and runs the four forensic query
shapes with EXPLAIN (ANALYZE, BUFFERS) where safe.
"""

from __future__ import annotations

import json
import random
import sys
from datetime import UTC, datetime, timedelta

import psycopg

DBNAME = "nse_p1_scan"
DSN = "postgresql://postgres@127.0.0.1:55432/postgres"
DSN_DB = f"postgresql://postgres@127.0.0.1:55432/{DBNAME}"

SCHEMA = """
DROP TABLE IF EXISTS audit_signals;
CREATE TABLE audit_signals (
    id INTEGER PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    request_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    confidence REAL NOT NULL,
    proposed_entry REAL NOT NULL,
    stop_loss REAL NOT NULL,
    take_profit REAL NOT NULL,
    regime TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    execution_mode TEXT,
    reason_code TEXT,
    decision_stage TEXT,
    blocked_by TEXT,
    htf_score REAL,
    smc_score REAL,
    confidence_before_filters REAL,
    confidence_after_filters REAL,
    signal_dedup_key TEXT UNIQUE,
    preferred_direction TEXT,
    raw_prob_buy REAL,
    raw_prob_sell REAL,
    raw_prob_no_trade REAL,
    raw_prob_wait REAL,
    confidence_source TEXT,
    spread_usd REAL,
    account_source TEXT
);
CREATE INDEX idx_audit_signals_generated ON audit_signals (generated_at DESC);
"""

# Live shape: ~9100 rows over ~7 days, NO_TRADE majority (~92%), one row per
# M1 decision (~1/min) with a payload of ~850 bytes.
REASONS = [
    ("CONFIDENCE_GATE", "CONFIDENCE_GATE"),
    ("GUARDIAN_GATE", "GUARDIAN_GATE"),
    ("SYMBOL_WHITELIST_GATE", "SYMBOL_WHITELIST_GATE"),
    ("SPREAD_TP_GATE", "SPREAD_TP_GATE"),
    ("TICK_DEDUP", "TICK_DEDUP"),
    ("REGIME_FLIP", "REGIME_FLIP"),
    ("MODEL_SIGNAL", "MODEL_SIGNAL"),
]
STAGES = [
    "EXPERIENCE_INTELLIGENCE_GATE",
    "TRADE_INTELLIGENCE_GATE",
    "CONFIDENCE_GATE",
    "GUARDIAN_GATE",
    "FINAL_DECISION",
]


def payload_for(i: int, action: str, ts: datetime) -> str:
    model_action = action if action == "NO_TRADE" else random.choice(["BUY_MARKET", "SELL_MARKET"])
    return json.dumps(
        {
            "model_action": model_action,
            "ai_buy_probability": round(random.random(), 4),
            "ai_sell_probability": round(random.random(), 4),
            "ai_no_trade_probability": round(random.random(), 4),
            "regime_confidence": round(random.random(), 3),
            "risk_allowed": random.random() > 0.1,
            "guardian_status": random.choice(["OK", "IDLE", "BLOCKED"]),
            "rejection_reason": "MODEL_SIGNAL" if action != "NO_TRADE" else "CONFIDENCE_GATE",
            "blocked_by": "" if action != "NO_TRADE" else "CONFIDENCE_GATE",
            "decision_stage": random.choice(STAGES),
            "execution_id": f"exec-{i:07d}",
            "padding": "x" * 760,
        }
    )


def seed(n_rows: int = 9108, days: float = 7.0) -> None:
    """Seed audit_signals with the production row/time shape."""
    rng = random.Random(20260928)
    base = datetime(2026, 9, 28, 14, 0, tzinfo=UTC)
    rows = []
    for i in range(1, n_rows + 1):
        # Even cadence across the window (M1-ish), oldest first so id rises
        # with time — exactly the production property.
        ts = base - timedelta(seconds=(n_rows - i) * 60.0 / (n_rows / (days * 1440.0)))
        action = "NO_TRADE" if rng.random() < 0.92 else rng.choice(["BUY_MARKET", "SELL_MARKET"])
        stage, blocked = rng.choice(REASONS)
        rows.append(
            (
                f"req-{i:07d}",
                "XAUUSD",
                action,
                round(rng.random(), 4),
                2650.0,
                2645.0,
                2660.0,
                rng.choice(["TRENDING", "RANGING", "MIXED"]),
                ts.isoformat(),
                payload_for(i, action, ts),
                "STANDARD",
                "MODEL_SIGNAL",
                stage if action == "NO_TRADE" else "FINAL_DECISION",
                blocked if action == "NO_TRADE" else None,
                round(rng.random(), 3),
                round(rng.random(), 3),
                round(rng.random(), 4),
                round(rng.random(), 4),
                f"ddk-{i:07d}",
                "" if action == "NO_TRADE" else action[:3],
                round(rng.random(), 4),
                round(rng.random(), 4),
                round(rng.random(), 4),
                None,
                "RAW_MODEL",
                round(rng.uniform(0.1, 1.5), 5),
                "PAPER",
            )
        )
    with psycopg.connect(DSN_DB) as c:
        c.autocommit = True
        with c.cursor().copy(
            "COPY audit_signals (request_id, symbol, action, confidence, proposed_entry, stop_loss, take_profit, regime, generated_at, payload, execution_mode, reason_code, decision_stage, blocked_by, htf_score, smc_score, confidence_before_filters, confidence_after_filters, signal_dedup_key, preferred_direction, raw_prob_buy, raw_prob_sell, raw_prob_no_trade, raw_prob_wait, confidence_source, spread_usd, account_source) FROM STDIN"
        ) as cp:
            for r in rows:
                cp.write_row(r)
        with c.cursor() as cur:
            cur.execute("ANALYZE audit_signals")
            cur.execute("SELECT count(*), max(generated_at), min(generated_at) FROM audit_signals")
            print("seeded:", cur.fetchone())


def stats(label: str) -> dict:
    with psycopg.connect(DSN_DB) as c:
        with c.cursor() as cur:
            cur.execute(
                """SELECT relname, seq_scan, seq_tup_read, idx_scan, n_live_tup, n_dead_tup
                   FROM pg_stat_user_tables WHERE relname='audit_signals'"""
            )
            cols = ("rel", "seq_scan", "seq_tup_read", "idx_scan", "live", "dead")
            row = dict(zip(cols, cur.fetchone(), strict=False))
            cur.execute("SELECT pg_size_pretty(pg_total_relation_size('audit_signals'))")
            row["size"] = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM audit_signals")
            row["exact"] = cur.fetchone()[0]
            print(f"[{label}] {row}")
            return row


def explain(title: str, sql: str, args=(), analyze: bool = False) -> None:
    opt = "ANALYZE, BUFFERS, " if analyze else ""
    with psycopg.connect(DSN_DB) as c:
        with c.cursor() as cur:
            cur.execute(f"EXPLAIN ({opt}VERBOSE, COSTS) {sql}", args)
            plan = "\n".join(r[0] for r in cur.fetchall())
    print(f"\n----- {title} ({'ANALYZED' if analyze else 'ESTIMATED'})\n{plan}\n")


def main() -> int:
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {DBNAME}")
        c.execute(f"CREATE DATABASE {DBNAME}")
    with psycopg.connect(DSN_DB, autocommit=True) as c:
        c.execute(SCHEMA)
    seed()
    stats("after-seed")

    cutoff = (datetime.now(UTC) - timedelta(hours=168)).isoformat()
    explain(
        "Q1 BEFORE: decisions/stats — generated_at >= ? GROUP BY",
        "SELECT action, decision_stage, COUNT(*) AS n FROM audit_signals "
        "WHERE generated_at >= %s GROUP BY action, decision_stage ORDER BY n DESC",
        (cutoff,),
        analyze=True,
    )
    explain(
        "Q2 BEFORE: operator/summary — id IN (last 20000)",
        "SELECT action, decision_stage, blocked_by, generated_at FROM audit_signals "
        "WHERE id IN (SELECT id FROM audit_signals ORDER BY id DESC LIMIT 20000)",
        (),
        analyze=True,
    )
    explain(
        "Q3 BEFORE: decisions/no-trade/reasons — action='NO_TRADE' GROUP BY",
        "SELECT COALESCE(reason_code, 'UNKNOWN') AS reason, COUNT(*) AS n FROM audit_signals "
        "WHERE action = 'NO_TRADE' GROUP BY reason ORDER BY n DESC",
        (),
        analyze=True,
    )
    explain(
        "Q4 BEFORE: get_recent_predictions — ORDER BY id DESC LIMIT 40",
        "SELECT request_id, symbol, action, confidence, proposed_entry, stop_loss, "
        "take_profit, regime, generated_at, payload, execution_mode, reason_code, "
        "decision_stage, blocked_by FROM audit_signals ORDER BY id DESC LIMIT 40",
        (),
        analyze=True,
    )
    explain(
        "Q4b BEFORE: incidents why_blocked — ticket=? OR payload LIKE ?",
        "SELECT * FROM audit_signals WHERE request_id = %s OR payload LIKE %s "
        "ORDER BY generated_at DESC LIMIT 20",
        ("req-0000001", "%req-0000001%"),
        analyze=False,  # deliberately non-sargable; estimate only
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
