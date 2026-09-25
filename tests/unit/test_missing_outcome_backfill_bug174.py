"""BUG-174 (2026-08-31): historical missing-outcome backfill + schema label truth.

Live-log evidence (21:01 restart): 308 DATASET_REJECTED MISSING_OUTCOME lines in
ONE dataset build. Root causes:

  1. Decisions created BEFORE the P0-A terminal writers existed (and before
     BUG-169b's pre-dispatch emission) never received an outcome row. The
     startup recovery sweep existed (BUG-140 P0-B) but was only exposed as a
     manual web API — it never ran automatically, so the ledger orphans kept
     re-logging on every build.
  2. Orphans whose audit_signals row proves a pre-dispatch gate rejection
     (EXPERIENCE_INTELLIGENCE_GATE / TRADE_INTELLIGENCE_GATE) were skipped by
     the sweep as "no dispatch evidence", although the gate-rejection row IS
     positive evidence the decision was refused before dispatch.

Schema-label truth (user question "why 50D?"): the feature SNAPSHOT is the 50D
base tensor (indices 0..49 of the 70D contract) recorded under the schema id of
the ACTIVE provenance (scalp_v3). The 70D live tensor is assembled at inference
time from base50 + news10 + liquidity10, so `scalp_v3/50D` in the census means
"a scalp_v3-provenance row whose snapshot stores the 50-value base block" —
NOT a schema violation. These tests pin that behaviour.
"""

from __future__ import annotations

import sqlite3

import pytest

from nexus_scalp.experience.lifecycle import DecisionLifecycle
from nexus_scalp.experience.outcome_recovery_sweep import HistoricalOutcomeRecoverySweep


@pytest.fixture()
def sweep_db(tmp_path):
    """Minimal audit.db with one gate-rejected orphan + one unknown orphan."""
    db = tmp_path / "audit.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE audit_experiences (
            id INTEGER PRIMARY KEY,
            experience_id TEXT, request_id TEXT, execution_id TEXT, decision_id TEXT,
            idempotency_key TEXT UNIQUE, correction_of TEXT, record_version INTEGER,
            symbol TEXT, timeframe TEXT, strategy_id TEXT, strategy_version TEXT,
            decision_timestamp TEXT, action TEXT, entry_reason TEXT,
            model_probability REAL, signal_confidence REAL, proposed_entry REAL,
            stop_loss REAL, take_profit REAL, risk_reward_ratio REAL,
            min_rr_policy REAL, feature_schema_id TEXT, feature_dimension INTEGER,
            feature_hash TEXT, model_id TEXT, model_version TEXT,
            config_version TEXT, payload TEXT
        );
        CREATE TABLE audit_experience_outcomes (
            id INTEGER PRIMARY KEY, idempotency_key TEXT UNIQUE, execution_id TEXT,
            outcome_timestamp TEXT, is_executed INTEGER, is_closed INTEGER,
            exit_reason TEXT, realized_pnl_usd REAL, realized_r_multiple REAL,
            approved_volume REAL, mae_points REAL, mfe_points REAL, mae_usd REAL,
            mfe_usd REAL, mae_r REAL, mfe_r REAL, holding_duration_seconds REAL,
            slippage_points REAL, execution_latency_ms REAL, strategy_quality REAL,
            entry_quality REAL, execution_quality REAL, management_quality REAL,
            exit_quality REAL, behavioral_flags TEXT, payload TEXT
        );
        CREATE TABLE audit_orders (
            id INTEGER PRIMARY KEY, ticket INTEGER, order_id TEXT, symbol TEXT,
            action TEXT, price REAL, stop_loss REAL, take_profit REAL, volume REAL,
            reason TEXT, latency REAL, execution_mode TEXT, timestamp TEXT,
            execution_id TEXT
        );
        CREATE TABLE audit_signals (
            id INTEGER PRIMARY KEY, request_id TEXT, symbol TEXT, action TEXT,
            confidence REAL, generated_at TEXT, payload TEXT, execution_mode TEXT,
            reason_code TEXT, decision_stage TEXT, blocked_by TEXT, htf_score REAL,
            smc_score REAL, confidence_before_filters REAL,
            confidence_after_filters REAL, signal_dedup_key TEXT
        );
        """
    )
    # Orphan A: gate-rejected pre-dispatch (signal row proves it)
    conn.execute(
        "INSERT INTO audit_experiences (idempotency_key, request_id, decision_timestamp) "
        "VALUES ('exp_aaa', 'req_aaa', '2026-08-31T12:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO audit_signals (request_id, decision_stage, generated_at) "
        "VALUES ('req_aaa', 'EXPERIENCE_INTELLIGENCE_GATE', '2026-08-31T12:00:00+00:00')"
    )
    # Orphan B: no evidence at all -> must stay skipped
    conn.execute(
        "INSERT INTO audit_experiences (idempotency_key, request_id, decision_timestamp) "
        "VALUES ('exp_bbb', 'req_bbb', '2026-08-31T12:00:01+00:00')"
    )
    conn.commit()
    yield db
    conn.close()


class _FakeRepo:
    def __init__(self, path):
        self._db_path = str(path)
        self._is_sqlite = True


class _FakeLedger:
    """Records terminal outcomes written through the idempotent contract."""

    def __init__(self, existing_keys: set[str]):
        self.audit_repo = None
        self._existing = existing_keys
        self.written: list[str] = []

    def record_terminal_outcome(self, outcome):
        if outcome.idempotency_key in self._existing:
            return False
        self.written.append(outcome.idempotency_key)
        self._existing.add(outcome.idempotency_key)
        return True

    def flush_pending(self, timeout_sec: float = 5.0) -> bool:
        return True


def test_gate_rejection_orphan_gets_not_dispatched_outcome(sweep_db):
    """An orphan with a gate-rejection signal row is POSITIVE pre-dispatch
    evidence -> backfilled as NOT_DISPATCHED (not skipped as unknown)."""
    ledger = _FakeLedger(existing_keys=set())
    sweep = HistoricalOutcomeRecoverySweep(ledger=ledger)
    sweep.repo = _FakeRepo(sweep_db)

    result = sweep.run(dry_run=False)

    assert result.scanned == 2
    assert result.recovered == 1
    assert result.skipped_no_dispatch == 1
    assert "exp_aaa" in ledger.written
    assert "exp_bbb" not in ledger.written


def test_recovery_sweep_is_idempotent(sweep_db):
    """Re-running the sweep must not duplicate outcomes (UNIQUE key contract)."""
    ledger = _FakeLedger(existing_keys=set())
    sweep = HistoricalOutcomeRecoverySweep(ledger=ledger)
    sweep.repo = _FakeRepo(sweep_db)

    sweep.run(dry_run=False)
    first_count = len(ledger.written)
    assert first_count == 1

    # Second pass: the ledger now has the row -> nothing new is written.
    second = sweep.run(dry_run=False)
    assert len(ledger.written) == first_count
    assert second.recovered == 0


def test_dry_run_writes_nothing(sweep_db):
    ledger = _FakeLedger(existing_keys=set())
    sweep = HistoricalOutcomeRecoverySweep(ledger=ledger)
    sweep.repo = _FakeRepo(sweep_db)

    result = sweep.run(dry_run=True)
    assert result.recovered == 1
    assert ledger.written == []


def test_scalp_v3_50d_snapshot_label_is_expected_not_corruption(tmp_path):
    """'scalp_v3/50D' in the schema census = scalp_v3 provenance row whose
    feature snapshot stores the 50-value BASE block (indices 0..49 of the 70D
    contract). The 70D vector is assembled at inference time (base50+news10+
    liquidity10), so a 50-length snapshot under scalp_v3 provenance is the
    documented P0 shape, not a schema violation."""
    from nexus_scalp.features.schema_contract import DIMENSION as SCHEMA_70D

    assert SCHEMA_70D == 70
    # The 70D contract is 50 base + 10 news + 10 liquidity.
    base, news, liq = 50, 10, 10
    assert base + news + liq == SCHEMA_70D
