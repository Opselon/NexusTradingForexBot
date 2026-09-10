"""Gate-first fresh install must NOT poison the audit DB (PERF-DEADLETTER root cause).

Repro of the production incident (container v9.0.11, 2026-09-09..10, ~553k
audit_dead_letter rows @ ~10/s):

1. The startup migration gate (`nexus db migrate`, docker entrypoint step 3)
   runs BEFORE the engine. On a fresh DB, DatabaseMigrationEngine.
   _create_baseline_tables() builds EVERY manifest table as an
   `id INTEGER PRIMARY KEY` skeleton (engine.py:411) — including tables the
   manifest declares with zero columns (audit_signals, audit_guard_telemetry,
   audit_orders, audit_account_snapshots, audit_experiences,
   strategy_registry, experience_model_registry, research_worker_state).
2. The app bootstrap (AuditRepository._create_sqlite_tables) is
   CREATE TABLE IF NOT EXISTS — a NO-OP on the skeleton tables. Its
   _add_column_if_missing loops only heal EXTRAS (execution_mode,
   reason_code, ...), never the CORE columns (request_id, symbol, ...).
3. Every INSERT from every producer then fails with
   "table audit_signals has no column named request_id" — the worker batch
   salvage dead-letters EVERY row, ~10/s, ~553k rows in ~15h, audit.db 1GB.

This test pins the fix: after gate-then-bootstrap on a fresh DB, every
manifest table must carry the columns its production INSERTs use.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from nexus_scalp.adapters.database.audit_repository import AuditRepository  # noqa: E402
from nexus_scalp.database.engine import DatabaseMigrationEngine  # noqa: E402
from nexus_scalp.database.models import DatabaseDomain  # noqa: E402

#: (table, column) pairs that REAL producers write (the exact failure shape
#: measured in the 553k-row dead-letter table: one probe row per error).
REQUIRED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("audit_signals", "request_id"),
    ("audit_signals", "signal_dedup_key"),
    ("audit_signals", "account_source"),
    ("audit_guard_telemetry", "window_start"),
    ("audit_guard_telemetry", "reason_code"),
    ("audit_orders", "symbol"),
    ("audit_orders", "execution_id"),
    ("audit_executions", "order_id"),
    ("audit_executions", "status"),
    ("audit_account_snapshots", "timestamp"),
    ("audit_account_snapshots", "account_source"),
    ("audit_experiences", "experience_id"),
    ("audit_experiences", "idempotency_key"),
    ("strategy_registry", "strategy_id"),
    ("experience_model_registry", "model_id"),
    ("position_lifecycle_events", "event_key"),
    ("research_worker_state", "scope"),
    ("intelligence_worker_state", "scope"),
    ("audit_ledger", "ticket"),
    ("audit_ledger", "net_pnl_usd"),
)


def _gate_then_bootstrap(tmp_path: Path) -> tuple[Path, AuditRepository]:
    """Runs the migration gate on a FRESH db, then the app bootstrap."""
    db = tmp_path / "audit.db"
    eng = DatabaseMigrationEngine(db_path=db, domain=DatabaseDomain.AUDIT)
    res = eng.migrate()
    assert res["state"] == "DB_MIGRATION_SUCCEEDED", res
    repo = AuditRepository(db_url=f"sqlite:///{db}")
    return db, repo


def _columns(db: Path, table: str) -> set[str]:
    con = sqlite3.connect(db)
    try:
        return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    finally:
        con.close()


@pytest.mark.parametrize("table,column", list(REQUIRED_COLUMNS))
def test_gate_first_fresh_install_carries_producer_columns(
    tmp_path: Path, table: str, column: str
) -> None:
    db, repo = _gate_then_bootstrap(tmp_path / table / column if False else tmp_path)
    try:
        assert os.path.exists(db)
        cols = _columns(db, table)
        assert column in cols, (
            f"gate-first fresh install left {table} without core column "
            f"'{column}' (the 553k dead-letter root cause)"
        )
    finally:
        repo.close()


def test_gate_first_bootstrap_signal_row_survives(tmp_path: Path) -> None:
    """End-to-end: gate -> bootstrap -> enqueue a signal row -> flush -> row
    present in audit_signals (worker no longer dead-letters every row)."""
    from nexus_scalp.domain.models import ActionType, TradeProposal

    db, repo = _gate_then_bootstrap(tmp_path)
    try:
        from datetime import UTC, datetime

        proposal = TradeProposal(
            request_id="req-perf-0001",
            execution_id="EXEC-20260910-000000-aaaaaa",
            symbol="XAUUSD",
            generated_at=datetime.now(UTC),
            action=ActionType.NO_TRADE,
            confidence=0.1,
            proposed_entry=1900.0,
            stop_loss=1899.0,
            take_profit=1902.0,
            risk_reward_ratio=2.0,
            reason_code="MODEL_SIGNAL",
            model_action="NO_TRADE",
            buy_probability=0.1,
            sell_probability=0.1,
            no_trade_probability=0.8,
            regime="UNKNOWN",
            regime_confidence=0.0,
            risk_allowed=False,
            guardian_status="IDLE",
            rejection_reason=None,
            final_action="NO_TRADE",
            risk_checks={},
            execution_mode="STANDARD",
            override_reason=None,
            decision_stage="STANDARD_EVAL",
            blocked_by=None,
            htf_score=0.0,
            smc_score=0.0,
            confidence_before_filters=0.0,
            confidence_after_filters=0.0,
        )
        object.__setattr__(
            proposal, "generated_at", datetime.now(UTC)
        )  # TradeProposal may be frozen; set the field directly
        repo.log_signal(proposal)
        assert repo.flush(timeout_sec=10), "audit queue did not drain"
        con = sqlite3.connect(db)
        try:
            n = con.execute("SELECT COUNT(*) FROM audit_signals").fetchone()[0]
            dl = con.execute("SELECT COUNT(*) FROM audit_dead_letter").fetchone()[0]
        finally:
            con.close()
        assert n == 1, f"signal row lost (rows={n})"
        assert dl == 0, f"rows dead-lettered on a healthy schema: {dl}"
    finally:
        repo.close()
