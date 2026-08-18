"""
probe_behavior_lineage_gap.py — TASK-2 root-cause probe (READ-ONLY)

Proves where the behavioral/anomaly intelligence disappears:

  1. behavior_detections rows (PHASE 09 engine output)          -> expect 0
  2. audit_experience_outcomes behavioral_flags (PHASE 08)      -> expect 34 rows
  3. ledger rows with MAE/MFE/confidence/regime (evidence pool) -> expect 266
  4. trade_autopsies behavioral_flags (derived flags pool)      -> expect > 0
  5. lifecycle POSITION_EXITED events (model/regime provenance) -> expect > 0
  6. report _stage_behavioral source table (behavior_detections) -> 0 rows => n/a

No writes. Evidence for BUG-083 / design-gap classification.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "artifacts" / "audit.db"


def q(cur: sqlite3.Cursor, sql: str, *args) -> list:
    try:
        return [r for r in cur.execute(sql, args).fetchall()]
    except Exception as e:  # noqa: BLE001
        return [("ERROR", str(e))]


def main() -> int:
    if not DB.exists():
        print(f"DB not found: {DB}")
        return 2
    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    print("== BEHAVIORAL / ANOMALY LINEAGE PROBE (TASK-2) ==")
    print(f"DB: {DB}")

    n = q(cur, "SELECT COUNT(*) FROM behavior_detections")
    print(f"\n1. behavior_detections rows            : {n[0][0]}  (PHASE 09 engine sink)")

    n = q(
        cur,
        "SELECT COUNT(*) FROM audit_experience_outcomes "
        "WHERE behavioral_flags IS NOT NULL AND behavioral_flags != ''",
    )
    print(f"2. outcomes WITH behavioral_flags      : {n[0][0]}  (PHASE 08 flags exist!)")

    n = q(cur, "SELECT COUNT(*) FROM audit_ledger")
    print(f"3. ledger closed rows                  : {n[0][0]}")
    for label, sql in [
        ("   with mae+mfe pts  ", "SELECT COUNT(*) FROM audit_ledger WHERE mae != 0 AND mfe != 0"),
        ("   with confidence   ", "SELECT COUNT(*) FROM audit_ledger WHERE ai_confidence_at_open NOT IN ('', 0)"),
        ("   with regime       ", "SELECT COUNT(*) FROM audit_ledger WHERE market_regime_at_open NOT IN ('', 0)"),
        ("   with sl_modified  ", "SELECT COUNT(*) FROM audit_ledger WHERE was_sl_modified != 0"),
    ]:
        r = q(cur, sql)
        print(f"{label}: {r[0][0]}")

    n = q(cur, "SELECT COUNT(*) FROM trade_autopsies")
    print(f"4. trade_autopsies rows               : {n[0][0]}")
    r = q(
        cur,
        "SELECT COUNT(*) FROM trade_autopsies "
        "WHERE behavioral_flags IS NOT NULL AND behavioral_flags != ''",
    )
    print(f"   autopsies WITH behavioral_flags     : {r[0][0]}")

    n = q(cur, "SELECT COUNT(*) FROM position_lifecycle_events")
    print(f"5. lifecycle events                   : {n[0][0]}")
    r = q(
        cur,
        "SELECT COUNT(*) FROM position_lifecycle_events WHERE event_type = 'POSITION_EXITED'",
    )
    print(f"   POSITION_EXITED events              : {r[0][0]}")

    # flag distribution from the only populated flag source today
    r = q(
        cur,
        "SELECT behavioral_flags, COUNT(*) FROM audit_experience_outcomes "
        "WHERE behavioral_flags != '' GROUP BY behavioral_flags ORDER BY 2 DESC LIMIT 8",
    )
    print("\n6. PHASE 08 flag distribution (only populated source):")
    for row in r:
        print(f"   {row[0]!r:60} {row[1]}")

    # exit classification contradiction candidate: RISK_FREE_SL_HIT w/o sl modification
    r = q(
        cur,
        "SELECT COUNT(*) FROM audit_ledger "
        "WHERE exit_mechanism = 'RISK_FREE_SL_HIT' AND was_sl_modified = 0",
    )
    print(f"\n7. EXIT_CLASSIFICATION_ANOMALY candidate rows "
          f"(RISK_FREE_SL_HIT w/o was_sl_modified): {r[0][0]}")

    # duplicate economic outcome: same execution_id twice in outcomes
    r = q(
        cur,
        "SELECT execution_id, COUNT(*) c FROM audit_experience_outcomes "
        "WHERE is_closed = 1 GROUP BY execution_id HAVING c > 1 LIMIT 5",
    )
    print(f"8. duplicate-outcome execution_id groups: {len(r)}")
    for row in r[:5]:
        print(f"   {row[0]}: {row[1]} outcomes")

    # strategy context: ledger rows with empty entry_reason (strategy loss proxy)
    r = q(cur, "SELECT COUNT(*) FROM audit_ledger WHERE entry_reason IN ('', 'UNKNOWN')")
    print(f"9. ledger rows missing entry_reason    : {r[0][0]}")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
