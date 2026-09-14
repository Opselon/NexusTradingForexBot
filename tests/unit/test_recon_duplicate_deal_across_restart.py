"""RECON CRITICAL GATE — durable duplicate-event idempotency ACROSS restart.

Execution-lane verified gap (2026-09-14): in-session duplicate protection for
broker events is proven (test_trade_lifecycle_task3 tl13/tl15), but the
in-memory guard (_reconcile_seen, _processed_orders) dies with the process.
The ONLY cross-boot protection is the durable UNIQUE identity in the audit DB
(INV-006: duplicate broker events must not create duplicate outcomes). No
existing test replays the same financial identity through a SECOND
AuditRepository instance over the same file — i.e. exactly what happens when
the engine crashes mid-reconciliation and restarts against broker truth.

Deterministic, temp DBs, no clock dependence.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.domain.enums import ActionType, OrderType
from nexus_scalp.domain.models import TradeOrder, TradeProposal


def _proposal(rid: str, minute: int) -> TradeProposal:
    return TradeProposal(
        request_id=rid,
        execution_id=f"EXEC-{rid}",
        symbol="XAUUSD",
        generated_at=datetime(2026, 9, 14, 12, minute, 0, tzinfo=UTC),
        action=ActionType.BUY,
        confidence=0.8,
        proposed_entry=4400.0,
        stop_loss=4390.0,
        take_profit=4420.0,
        risk_reward_ratio=2.0,
        reason_code="MODEL_SIGNAL",
    )


def _order(oid: str) -> TradeOrder:
    return TradeOrder(
        order_id=oid,
        symbol="XAUUSD",
        order_type=OrderType.BUY,
        volume=0.10,
        price=4400.0,
        stop_loss=4390.0,
        take_profit=4420.0,
        magic_number=888101,
    )


def _rows(db, sql: str) -> int:
    con = sqlite3.connect(db)
    try:
        return int(con.execute(sql).fetchone()[0])
    finally:
        con.close()


def test_signal_replay_after_restart_collapses_to_one_durable_decision(tmp_path):
    """Same decision identity re-logged by a FRESH repository (restart) =>
    still exactly one audit_signals row: signal_dedup_key UNIQUE +
    ON CONFLICT DO NOTHING are boot-independent."""
    db = tmp_path / "idem.db"
    r1 = AuditRepository(db_url=f"sqlite:///{db}", flush_interval_sec=0.02)
    try:
        r1.log_signal(_proposal("DUP-1", 5))
        assert r1.flush(timeout_sec=10.0) is True
    finally:
        r1.close()

    r2 = AuditRepository(db_url=f"sqlite:///{db}", flush_interval_sec=0.02)
    try:
        # post-restart redelivery of the SAME broker-visible decision (new
        # request_id — dedup keys on evaluated content, not UUIDs):
        r2.log_signal(_proposal("DUP-1-REDELIVERED", 5))
        assert r2.flush(timeout_sec=10.0) is True
    finally:
        r2.close()

    n = _rows(db, "SELECT COUNT(*) FROM audit_signals")
    assert n == 1, f"cross-restart signal replay created {n} durable decisions, want 1"


def test_execution_attempt_replay_after_restart_is_exactly_once(tmp_path):
    """audit_executions identity (order_id,status) UNIQUE: a redelivered
    attempt outcome after restart collapses; a DISTINCT lifecycle status
    remains a separate real event (contract in executions_idempotency)."""
    db = tmp_path / "exec.db"
    order = _order("ORD-DUP")
    r1 = AuditRepository(db_url=f"sqlite:///{db}", flush_interval_sec=0.02)
    try:
        r1.log_execution(order, "FILLED")
        assert r1.flush(timeout_sec=10.0) is True
    finally:
        r1.close()

    r2 = AuditRepository(db_url=f"sqlite:///{db}", flush_interval_sec=0.02)
    try:
        r2.log_execution(order, "FILLED")  # redelivery of the same outcome
        r2.log_execution(order, "REJECTED")  # a genuinely different attempt outcome
        assert r2.flush(timeout_sec=10.0) is True
    finally:
        r2.close()

    total = _rows(db, "SELECT COUNT(*) FROM audit_executions WHERE order_id='ORD-DUP'")
    filled = _rows(
        db, "SELECT COUNT(*) FROM audit_executions WHERE order_id='ORD-DUP' AND status='FILLED'"
    )
    assert filled == 1, f"duplicate FILLED outcome survived restart: {filled} rows"
    assert total == 2, (
        f"(order_id,status) identity broken: expected FILLED+REJECTED only, got {total}"
    )
