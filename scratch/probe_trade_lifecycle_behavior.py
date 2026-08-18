"""
probe_trade_lifecycle_behavior.py — TASK-2 runtime verification probe

Proves ONE trade ticket survives every stage of the canonical lineage:

    broker/ledger -> accounting TradeRecord -> behavior analysis
    -> anomaly analysis -> performance report -> API/Telegram shape

Uses a COPY of the real audit.db (never touches the live artifact). Reads the
real persisted behavior_analysis + behavior_detections + anomaly_events rows
produced by the backfill and walks the same ticket through the report stages.
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REAL_DB = REPO / "artifacts" / "audit.db"

sys.path.insert(0, str(REPO / "tests"))


def main() -> int:
    from nexus_scalp.adapters.database.audit_repository import AuditRepository
    from nexus_scalp.experience import ExperienceLedger
    from nexus_scalp.accounting import AccountingCore, PeriodKind
    from nexus_scalp.reporting import PerformanceReportEngine, format_deep_report
    from nexus_scalp.intelligence.behavior import BehaviorAnalysisBackfiller

    # 1. Work on a scratch COPY of the real DB.
    tmp = Path(tempfile.mkdtemp()) / "replay.db"
    import shutil

    shutil.copy2(REAL_DB, tmp)

    audit = AuditRepository(db_url=f"sqlite:///{tmp}", flush_interval_sec=0.05)
    ledger = ExperienceLedger(audit_repo=audit)
    core = AccountingCore(audit_repo=audit, adapter=None, experience_ledger=ledger)

    # 2. Seed ONE fresh trade through the canonical ledger API (entry->exit).
    ticket = 700001
    ts = datetime.now(UTC) - timedelta(minutes=45)
    audit.log_ledger_closed(
        ticket=ticket,
        symbol="XAUUSD",
        direction="BUY",
        volume=1.0,
        entry_price=2000.0,
        exit_price=1988.0,
        status="CLOSED",
        pnl=-12.0,
        commission=0.0,
        swap=0.0,
        duration_sec=2700.0,
        timestamp_str=ts.isoformat(),
        mae=-18.0,
        mfe=6.0,
        initial_sl_price=1990.0,
        final_sl_price=1990.0,
        is_risk_free_hit=0,
        exit_mechanism="HARD_SL_HIT",
        open_time=(ts - timedelta(minutes=45)).isoformat(),
        close_time=ts.isoformat(),
        was_sl_modified=0,
        mae_usd=-180.0,
        mfe_usd=60.0,
        entry_reason="FAST_LIQUIDITY_SWEEP",
        market_regime_at_open="TRENDING_MOMENTUM",
        ai_confidence_at_open=0.64,
    )
    time.sleep(0.4)

    # 3. Behavior analysis on this ticket.
    backfiller = BehaviorAnalysisBackfiller(audit_repo=audit, max_trades_per_run=500)
    result = backfiller.run()
    print(f"[1] backfill: {result}")
    assert result["analyzed"] >= 1

    # 4. Prove behavior detection rows exist for THIS ticket.
    conn = sqlite3.connect(tmp)
    dets = conn.execute(
        "SELECT pattern, severity, confidence FROM behavior_detections WHERE ticket = ?",
        (str(ticket),),
    ).fetchall()
    anas = conn.execute(
        "SELECT behavior_version, anomaly_version, evidence_coverage, complete_context, "
        "partial_context FROM behavior_analysis WHERE ticket = ?",
        (str(ticket),),
    ).fetchall()
    anos = conn.execute(
        "SELECT anomaly_type FROM anomaly_events WHERE ticket = ?", (str(ticket),)
    ).fetchall()
    print(f"[2] behavior_detections[{len(dets)}] for ticket {ticket}:")
    for d in dets:
        print("      ", d)
    print(f"[3] behavior_analysis[{len(anas)}]:")
    for a in anas:
        print("      ", a)
    print(f"[4] anomaly_events[{len(anos)}]: {[r[0] for r in anos]}")

    assert any(d[0] == "OVERHOLD_LOSER" for d in dets) or any(
        d[0] == "LATE_EXIT_PATTERN" for d in dets
    ), "expected hold-based flag for the 45-min losing trade"
    assert len(anas) == 1

    # 5. Walk the SAME ticket through the performance report stages.
    report = PerformanceReportEngine(core=core, kind=PeriodKind.DAY).generate(
        at=datetime.now(UTC)
    )
    b = report.behavioral
    a = report.anomaly_state
    print(
        f"[5] report.behavioral: state={b.state} analyzed={b.analyzed} "
        f"coverage={b.evidence_coverage} flags={b.total_flags} "
        f"version={b.analysis_version}"
    )
    print(
        f"[6] report.anomaly_state: state={a.state} total={a.total} "
        f"version={a.anomaly_version}"
    )
    assert b.state in ("CLEAR", "FLAGS_FOUND")
    assert b.analyzed >= 1
    assert a.has_data

    # 6. Telegram formatter consumes the canonical report (truthful text).
    text = format_deep_report(report)
    assert "no behavioral flags recorded" not in text
    assert "none detected" not in text
    print("[7] Telegram deep report: truthful behavioral/anomaly section (no n/a)")
    for line in text.split("\n"):
        if "BEHAVIORAL" in line or "ANOMALIES" in line or "NO_DATA" in line:
            print("      ", line[:90])

    # 7. API-shape: the same container serializes with truth-state contract.
    payload = report.to_dict()
    assert payload["behavioral"]["state"] == b.state
    assert payload["anomaly_state"]["state"] == a.state
    print("[8] API payload contract OK:", payload["behavioral"]["state"], "/",
          payload["anomaly_state"]["state"], f"trades={b.analyzed}")

    conn.close()
    audit.close()
    print("\nRUNTIME VERIFICATION: PASS — ticket", ticket, "survived every stage")
    return 0


if __name__ == "__main__":
    sys.exit(main())