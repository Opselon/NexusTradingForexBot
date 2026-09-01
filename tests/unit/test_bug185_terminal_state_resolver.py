"""BUG-185 (P0, 2026-09-01): canonical terminal-state resolver + classify-once
unknown-provenance orphans + observable terminal-outcome skips.

Production evidence (live engine 2026-08-31 -> 09-01, artifacts/audit.db):
  * Startup sweep correctly closes evidenced orphans (gate rejections ->
    NOT_DISPATCHED, dispatch rows -> broker truth). 238 pre-Aug-22 orphans
    remain with NO dispatch row and NO gate signal: honest UNKNOWN.
  * research/dataset.py nevertheless re-logged all of them as
    DATASET_REJECTED reason=MISSING_OUTCOME recoverable=True on EVERY ~60s
    research cycle: 14,847 lines in one day's info log.
  * emit_terminal_pending_outcome had SILENT False paths (missing engine /
    empty request_id) — exactly one gate rejection (exp_662cf14a, 06:01:21Z)
    got a REJECT log, no outcome, and no diagnostic.

Fix contract pinned here:
  1. experience/decision_evidence.resolve_decision_evidence is THE canonical
     evidence resolver (P0-M): gate rejection -> NOT_DISPATCHED-able,
     dispatch ticket -> dispatched, absence -> honest unknown.
  2. Recovery sweep + dataset builder consume the SAME resolver
     (semantic parity: no "recoverable=True" vs "skipped_no_dispatch" split).
  3. Unknown provenance is classified ONCE per key per process
     (ORPHAN_CLASSIFIED_UNKNOWN, recoverable=False) and afterwards only
     counted in the census — never re-spammed, never fabricated.
  4. Terminal-outcome emit skips are observable (warning, never silent).
"""

from __future__ import annotations

import logging

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.experience.decision_evidence import (
    EVIDENCE_DISPATCH_TICKET,
    EVIDENCE_GATE_REJECTION,
    EVIDENCE_NO_EVIDENCE,
    TerminalStateEvidence,
    resolve_decision_evidence,
)
from nexus_scalp.experience.ledger import ExperienceLedger
from nexus_scalp.experience.lifecycle import DecisionLifecycle
from nexus_scalp.execution.terminal_outcome import emit_terminal_pending_outcome
from nexus_scalp.research.dataset import (
    REASON_NOT_DISPATCHED,
    REASON_UNKNOWN_PROVENANCE,
    ResearchDatasetBuilder,
)


@pytest.fixture()
def repo(tmp_path):
    r = AuditRepository(db_url=f"sqlite:///{tmp_path / 'bug185.db'}")
    yield r
    r.close()


@pytest.fixture()
def ledger(repo):
    return ExperienceLedger(repo)


def _seed_decision(ledger: ExperienceLedger, request_id: str) -> None:
    from datetime import UTC, datetime

    from nexus_scalp.experience.models import (
        ExperienceRecord,
        FeatureSnapshot,
        StrategyContext,
    )

    rec = ExperienceRecord(
        experience_id=f"exp_row_{request_id}",
        request_id=request_id,
        idempotency_key=f"exp_{request_id}",
        symbol="XAUUSD",
        timeframe="M1",
        decision_timestamp=datetime(2026, 8, 18, tzinfo=UTC),
        strategy_id="strat_fam",
        strategy_version="1.0.0",
        context=StrategyContext(
            strategy_id="strat_fam",
            symbol="XAUUSD",
            session="LONDON",
            regime="TRENDING",
            volatility_regime="NORMAL",
            trend_state="BULLISH",
        ),
        feature_snapshot=FeatureSnapshot(
            feature_schema_id="scalp_v1", feature_dimension=50, values=[0.0] * 50
        ),
        action="BUY_LIMIT",
        entry_reason="SMC",
        model_probability=0.6,
        signal_confidence=0.6,
        proposed_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        risk_reward_ratio=2.0,
        approved_volume=0.1,
    )
    ledger.record_experience(rec)


def _conn(repo):
    import sqlite3

    conn = sqlite3.connect(repo._db_path)
    return conn


class TestCanonicalResolver:
    def test_gate_rejection_is_positive_pre_dispatch_evidence(self, repo, ledger):
        _seed_decision(ledger, "req_g")
        repo._queue.join()
        with _conn(repo) as conn:
            conn.execute(
                """INSERT INTO audit_signals (request_id, symbol, action, confidence,
                       proposed_entry, stop_loss, take_profit, regime, generated_at,
                       payload, execution_mode, reason_code, decision_stage,
                       htf_score, smc_score, confidence_before_filters,
                       confidence_after_filters, signal_dedup_key)
                   VALUES ('req_g', 'XAUUSD', 'NO_TRADE', 0.0, 2000.0, 1990.0, 2020.0,
                           'T', '2026-08-18T00:00:00+00:00', '{}', 'STANDARD',
                           'DEGRADED', 'EXPERIENCE_INTELLIGENCE_GATE', 0.0, 0.0,
                           0.0, 0.0, 'sig_bug185_g')"""
            )
            conn.commit()
            ev = resolve_decision_evidence(conn, "req_g")
        assert ev.evidence == EVIDENCE_GATE_REJECTION
        assert ev.confidence == "PROVEN"
        assert ev.dispatch_proven is False
        assert ev.pre_dispatch_gate == "EXPERIENCE_INTELLIGENCE_GATE"
        assert ev.implied_terminal_state is DecisionLifecycle.NOT_DISPATCHED

    def test_dispatch_ticket_proves_dispatch(self, repo, ledger):
        _seed_decision(ledger, "req_d")
        repo._queue.join()
        with _conn(repo) as conn:
            conn.execute(
                """INSERT INTO audit_orders (ticket, order_id, symbol, action, price,
                       stop_loss, take_profit, volume, reason, latency,
                       execution_mode, timestamp)
                   VALUES (123, 'req_d', 'XAUUSD', 'BUY_LIMIT', 2000.0, 1990.0,
                           2020.0, 0.1, 'MODEL_SIGNAL', 0.0, 'STANDARD',
                           '2026-08-18 00:00:05')"""
            )
            conn.commit()
            ev = resolve_decision_evidence(conn, "req_d")
        assert ev.evidence == EVIDENCE_DISPATCH_TICKET
        assert ev.dispatch_proven is True
        assert ev.implied_terminal_state is None  # broker truth decides later

    def test_no_evidence_stays_unknown(self, repo, ledger):
        _seed_decision(ledger, "req_u")
        repo._queue.join()
        with _conn(repo) as conn:
            ev = resolve_decision_evidence(conn, "req_u")
        assert ev.evidence == EVIDENCE_NO_EVIDENCE
        assert ev.confidence == "UNKNOWN"
        assert ev.implied_terminal_state is None  # NEVER folded into NOT_DISPATCHED
        assert "unknown" in ev.reason.lower()

    def test_resolver_is_deterministic(self, repo, ledger):
        _seed_decision(ledger, "req_det")
        repo._queue.join()
        with _conn(repo) as conn:
            e1 = resolve_decision_evidence(conn, "req_det")
            e2 = resolve_decision_evidence(conn, "req_det")
        assert e1 == e2

    def test_resolver_never_raises_on_missing_tables(self, tmp_path):
        import sqlite3

        conn = sqlite3.connect(":memory:")
        try:
            ev = resolve_decision_evidence(conn, "req_x")
        finally:
            conn.close()
        # empty backend: honest unknown, no crash
        assert isinstance(ev, TerminalStateEvidence)
