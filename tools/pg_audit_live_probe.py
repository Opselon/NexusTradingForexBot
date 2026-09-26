"""Lane A live probe: exercise EVERY audit producer against a real PostgreSQL.

Usage: NEXUS_AUDIT_DB=postgresql://... python tools/pg_audit_live_probe.py

Not a test module: a one-shot verification that the audit domain's write path
lands rows on PostgreSQL after the portability fixes.  Run against an ISOLATED
cluster only (never localhost:5432).
"""

from __future__ import annotations

import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

import psycopg  # noqa: E402

from nexus_scalp.adapters.database.audit_repository import (  # noqa: E402
    AuditRepository,
)
from nexus_scalp.domain.enums import ActionType, OrderType  # noqa: E402
from nexus_scalp.domain.models import AccountInfo, TradeOrder, TradeProposal  # noqa: E402

DSN = os.environ["NEXUS_AUDIT_DB"]
NOW = datetime(2026, 9, 26, 4, 46, 0, tzinfo=UTC)
ISOD = NOW.isoformat()


def main() -> int:
    repo = AuditRepository(db_url=DSN)
    assert not repo._is_sqlite, "probe must run under a PostgreSQL provider"
    print(f"provider ok: is_sqlite={repo._is_sqlite}")

    # --- _log_guard_telemetry: THE 99.9% dead-letter statement -------------
    # Repeated identical windows must ACCUMULATE, not reset.
    for _ in range(5):
        repo._log_guard_telemetry(_DummyProposal(), "GUARD_OK")
    # A second reason code in the same window proves the conflict target works.
    for _ in range(3):
        repo._log_guard_telemetry(_DummyProposal(), "GUARD_OTHER")

    # --- set_runtime_risk_state (synchronous safety write) ------------------
    repo.set_runtime_risk_state(
        state="HALTED", reason="lane-a-live-probe", source="pg-mig-audit"
    )
    repo.set_runtime_risk_state(state="RUNNING", reason="released", source="probe")

    # --- the financial producers -------------------------------------------
    repo.log_signal(_make_proposal("dk-lanea-1"))
    repo.log_order(
        ticket=1001,
        order_id="ord-lanea-1",
        symbol="EURUSD",
        action="BUY",
        price=1.1050,
        stop_loss=1.1000,
        take_profit=1.1120,
        volume=0.10,
        reason="probe",
        latency=12.5,
        execution_mode="LIVE",
        execution_id="exec-lanea-1",
    )
    order = TradeOrder(
        order_id="ord-lanea-1",
        symbol="EURUSD",
        order_type=OrderType.BUY,
        volume=0.10,
        price=1.1051,
        stop_loss=1.1000,
        take_profit=1.1120,
        magic_number=42,
        comment="NSE_ORDER",
    )
    repo.log_execution(order, "FILLED")
    account = AccountInfo(
        login=12345,
        trade_mode=0,
        leverage=100,
        balance=10000.0,
        equity=10015.0,
        margin=100.0,
        margin_free=9915.0,
    )
    repo.log_account_snapshot(account, 10020.0)
    repo.log_ledger_opened(
        ticket=1002,
        symbol="EURUSD",
        direction="BUY",
        volume=0.10,
        entry_price=1.1050,
        timestamp_str=ISOD,
        order_id="1002",
        entry_reason="probe",
        ai_confidence_at_open=0.8,
        market_regime_at_open="trend",
    )
    repo.log_ledger_closed(
        ticket=1002,
        symbol="EURUSD",
        direction="BUY",
        volume=0.10,
        entry_price=1.1050,
        exit_price=1.1080,
        status="CLOSED",
        pnl=30.0,
        commission=0.5,
        swap=0.0,
        duration_sec=120,
        timestamp_str=ISOD,
        mae=5.0,
        mfe=35.0,
        initial_sl_price=1.1000,
        final_sl_price=1.1010,
        is_risk_free_hit=0,
        exit_mechanism="TP",
        order_id="ord-lanea-1",
        open_time=ISOD,
        close_time=ISOD,
        entry_reason="probe",
        ai_confidence_at_open=0.8,
        market_regime_at_open="trend",
        was_sl_modified=0,
        mae_usd=5.0,
        mfe_usd=35.0,
        account_balance_after=10029.5,
        account_equity_after=10029.5,
        drawdown_percent_after=0.0,
        entry_setup_snapshot="{}",
        exit_reason_source="engine",
        exit_evidence="[]",
        exit_reason_confidence=0.9,
        reversal_events_json="[]",
        account_source="paper",
    )
    repo.record_paper_execution(
        ts=ISOD,
        symbol="EURUSD",
        order_type="BUY",
        volume=0.10,
        requested_price=1.1050,
        bid_at_request=1.1049,
        ask_at_request=1.1051,
        spread=0.0002,
        fill_price=1.1050,
        slippage=0.0,
        rejection_reason="",
        ticket=1001,
        latency_ticks=3,
        status="FILLED",
        source="PAPER_ADAPTER_LEDGER",
    )
    # Idempotency: the same identity row must not duplicate.
    repo.record_paper_execution(
        ts=ISOD,
        symbol="EURUSD",
        order_type="BUY",
        volume=0.10,
        requested_price=1.1050,
        bid_at_request=1.1049,
        ask_at_request=1.1051,
        spread=0.0002,
        fill_price=1.1050,
        slippage=0.0,
        rejection_reason="",
        ticket=1001,
        latency_ticks=3,
        status="FILLED",
        source="PAPER_ADAPTER_LEDGER",
    )

    # --- drain, then verify -----------------------------------------------
    deadline = time.time() + 45
    while time.time() < deadline:
        qf = repo._write_plane.financial_events_failed
        qt = repo._write_plane.telemetry_dropped
        time.sleep(0.5)
        if repo._write_plane._effective_queue().qsize() == 0:
            break
    repo.close()
    print(f"financial_events_failed={qf} telemetry_dropped={qt}")

    expected = {
        "audit_guard_telemetry": 2,
        "runtime_risk_state": 1,
        "audit_signals": 1,
        "audit_orders": 1,
        "audit_executions": 1,
        "audit_account_snapshots": 1,
        "audit_ledger": 1,
        "audit_paper_executions": 1,
    }
    failures: list[str] = []
    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            for table, want in expected.items():
                cur.execute(f"SELECT count(*) FROM {table}")
                got = cur.fetchone()[0]
                print(f"{table}: got={got} want={want}")
                if got != want:
                    failures.append(f"{table}: expected {want}, got {got}")
            # The accumulator contract: 5 GUARD_OK -> count 5, 3 GUARD_OTHER -> 3.
            cur.execute(
                "SELECT reason_code, count FROM audit_guard_telemetry "
                "ORDER BY reason_code"
            )
            for reason_code, count in cur.fetchall():
                print(f"  guard {reason_code} -> {count}")
                want = 5 if reason_code == "GUARD_OK" else 3
                if count != want:
                    failures.append(
                        f"guard telemetry {reason_code}: expected {want}, got {count}"
                    )
            cur.execute("SELECT state FROM runtime_risk_state WHERE id=1")
            print("  runtime_risk_state ->", cur.fetchone())
            cur.execute("SELECT count(*) FROM audit_dead_letter")
            dl = cur.fetchone()[0]
            print(f"audit_dead_letter rows: {dl}")
            if dl:
                cur.execute(
                    "SELECT count(*), left(error, 120) FROM audit_dead_letter "
                    "GROUP BY 2 ORDER BY 1 DESC LIMIT 5"
                )
                for n, err in cur.fetchall():
                    print(f"  dead-letter n={n} :: {err}")
                failures.append(f"{dl} dead-letter rows (expected 0)")
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    print("\nALL AUDIT PRODUCERS LANDED ON POSTGRESQL")
    return 0


class _DummyProposal:
    """_log_guard_telemetry reads .generated_at and .symbol off the proposal."""

    generated_at = NOW
    symbol = "EURUSD"


def _make_proposal(dedup_key: str) -> TradeProposal:
    """A genuine-signal proposal (reason_code outside the guard-telemetry set)."""
    return TradeProposal(
        request_id="req-lanea-1",
        execution_id="EXEC-20260926-044600-lanea",
        symbol="EURUSD",
        generated_at=NOW,
        action=ActionType.BUY,
        confidence=0.82,
        proposed_entry=1.1050,
        stop_loss=1.1000,
        take_profit=1.1120,
        risk_reward_ratio=2.4,
        reason_code="MODEL_SIGNAL",
        model_action="BUY",
        buy_probability=0.5,
        sell_probability=0.3,
        no_trade_probability=0.2,
        regime="trend",
        execution_mode="live",
        decision_stage="FINAL",
        htf_score=0.7,
        smc_score=0.75,
        confidence_before_filters=0.6,
        confidence_after_filters=0.82,
        risk_checks={"confidence_source": "model", "spread_usd": 0.00012},
    )


if __name__ == "__main__":
    raise SystemExit(main())
