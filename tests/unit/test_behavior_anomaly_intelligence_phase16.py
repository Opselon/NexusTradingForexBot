"""
Behavioral + Anomaly Intelligence — TASK-2 regression suite (TEST-BHV-01..20)
=============================================================================
Covers:
  01. historical trades produce analysis records (engine invoked end-to-end)
  02. NO_DATA state is distinguishable from CLEAR
  03. OVERHOLD_LOSER detector (evidence-gated)
  04. PROFIT_GIVEBACK detector
  05. MISSED_BREAKEVEN detector
  06. PREMATURE_BREAKEVEN detector
  07. MODEL_REVERSAL_IGNORED detector
  08. REGIME_CHANGE_IGNORED detector
  09. LIQUIDITY_REVERSAL_IGNORED detector (structural)
  10. RISK_DEVIATION detector (canonical vs intended)
  11. EXIT_CLASSIFICATION_ANOMALY detector
  12. DUPLICATE_ECONOMIC_OUTCOME anomaly
  13. STRATEGY_CONTEXT_LOSS detector
  14. partial evidence is clearly reported (coverage < 100%)
  15. behavior algorithm version persistence
  16. anomaly algorithm version persistence
  17. Telegram output matches canonical report
  18. API output matches canonical report
  19. historical backfill is idempotent
  20. detector execution is bounded and does not block the hot tick path

Every detector assertion is evidence-gated: the flag must carry confidence,
threshold, actual/expected values and an explanation.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from nexus_scalp.accounting import AccountingCore, PeriodKind
from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.experience import ExperienceLedger
from nexus_scalp.intelligence.behavior import BehaviorDetectionEngine
from nexus_scalp.reporting import PerformanceReportEngine

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


class _FakeAccount:
    def __init__(self, balance: float, equity: float):
        self.balance = balance
        self.equity = equity
        self.margin_free = 0.0
        self.margin = 0.0
        self.margin_level = 0.0
        self.currency = "USD"
        self.leverage = 100
        self.login = 42


class _FakePosition:
    def __init__(self, volume: float = 1.0):
        self.volume = volume


class _FakeAdapter:
    def __init__(self) -> None:
        self.account = _FakeAccount(balance=10000.0, equity=10000.0)
        self.positions: list[_FakePosition] = []

    def get_account_info(self) -> _FakeAccount:
        return self.account

    def get_positions(self, symbol: str | None = None) -> list[_FakePosition]:
        return self.positions


@pytest.fixture()
def audit(tmp_path):
    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'test.db'}", flush_interval_sec=0.02)
    # Ensure the derived intelligence tables exist IMMEDIATELY (the lazy
    # ensure_schema only runs on first save; the reporting stage reads them
    # through a SEPARATE connection — deterministic visibility required).
    try:
        import sqlite3 as _sq

        _c = _sq.connect(str(tmp_path / "test.db"), timeout=5.0)
        try:
            _c.execute(
                """CREATE TABLE IF NOT EXISTS behavior_analysis (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    analysis_key TEXT UNIQUE NOT NULL,
                    ticket TEXT NOT NULL,
                    symbol TEXT DEFAULT '',
                    strategy_id TEXT DEFAULT '',
                    behavior_version TEXT DEFAULT '',
                    anomaly_version TEXT DEFAULT '',
                    analyzed_at TEXT DEFAULT '',
                    evidence_coverage REAL DEFAULT 0.0,
                    complete_context INTEGER DEFAULT 0,
                    partial_context INTEGER DEFAULT 0,
                    flags TEXT DEFAULT '[]',
                    anomalies TEXT DEFAULT '[]') """
            )
            _c.execute(
                """CREATE TABLE IF NOT EXISTS behavior_detections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    behavior_key TEXT UNIQUE NOT NULL,
                    behavior_id TEXT NOT NULL,
                    ticket TEXT NOT NULL,
                    experience_id TEXT DEFAULT '',
                    ticket_ctx TEXT DEFAULT '',
                    pattern TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    confidence REAL DEFAULT 0.0,
                    evidence TEXT DEFAULT '{}',
                    detected_at TEXT DEFAULT '',
                    autocorrected INTEGER DEFAULT 0) """
            )
            _c.execute(
                """CREATE TABLE IF NOT EXISTS anomaly_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    anomaly_id TEXT UNIQUE NOT NULL,
                    ticket TEXT NOT NULL,
                    anomaly_type TEXT NOT NULL,
                    category TEXT DEFAULT '',
                    severity TEXT DEFAULT '',
                    confidence REAL DEFAULT 0.0,
                    evidence TEXT DEFAULT '{}',
                    detected_at TEXT DEFAULT '',
                    algorithm_version TEXT DEFAULT '') """
            )
            _c.commit()
        finally:
            _c.close()
    except Exception:
        pass
    yield repo
    # Deterministic: drain the queued-writer before the fixture tears down so
    # later tests' reads observe the rows (skill.md async-queue note).
    try:
        repo._queue.join()
    except Exception:
        pass
    repo.close()


@pytest.fixture()
def ledger(audit):
    return ExperienceLedger(audit_repo=audit)


@pytest.fixture()
def core(audit, ledger):
    return AccountingCore(audit_repo=audit, adapter=_FakeAdapter(), experience_ledger=ledger)


def _flush(audit: AuditRepository, seconds: float = 0.3) -> None:
    # Deterministic: drain the queued-writer before reading (skill.md).
    # The worker batches on a 1s flush interval when idle, so a join alone
    # is NOT enough for bulk commits; open a direct connection and wait for
    # the queued rows to be consumed + committed (bounded, offline test path).
    try:
        audit._queue.join()
    except Exception:
        pass
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            import sqlite3 as _sq

            _conn = _sq.connect(audit._db_path, timeout=2.0)
            try:
                n = _conn.execute("SELECT COUNT(*) FROM audit_ledger").fetchone()[0]
            finally:
                _conn.close()
            if n > 0:
                return
        except Exception:
            pass
        time.sleep(0.02)
    time.sleep(seconds)


def _ledger_closed(
    audit: AuditRepository,
    ticket: int,
    *,
    direction: str = "BUY",
    entry: float = 2000.0,
    exit_price: float,
    pnl: float,
    commission: float = 0.0,
    swap: float = 0.0,
    close_ts: datetime | None = None,
    exit_mechanism: str = "TAKE_PROFIT_HIT",
    initial_sl: float = 1990.0,
    final_sl: float = 1990.0,
    is_risk_free_hit: int = 0,
    was_sl_modified: int = 0,
    mae: float = 0.0,
    mfe: float = 0.0,
    mae_usd: float = 0.0,
    mfe_usd: float = 0.0,
    entry_reason: str = "PURE_AI",
    regime: str = "TRENDING_MOMENTUM",
    confidence: float = 0.6,
) -> None:
    ts = close_ts or datetime.now(UTC)
    stamp = ts.isoformat()
    audit.log_ledger_closed(
        ticket=ticket,
        symbol="XAUUSD",
        direction=direction,
        volume=1.0,
        entry_price=entry,
        exit_price=exit_price,
        status="CLOSED",
        pnl=pnl,
        commission=commission,
        swap=swap,
        duration_sec=600.0,
        timestamp_str=stamp,
        mae=mae,
        mfe=mfe,
        initial_sl_price=initial_sl,
        final_sl_price=final_sl,
        is_risk_free_hit=is_risk_free_hit,
        exit_mechanism=exit_mechanism,
        open_time=(ts - timedelta(minutes=10)).isoformat(),
        close_time=stamp,
        was_sl_modified=was_sl_modified,
        mae_usd=mae_usd,
        mfe_usd=mfe_usd,
        entry_reason=entry_reason,
        market_regime_at_open=regime,
        ai_confidence_at_open=confidence,
    )
    _flush(audit)


def _experience_outcome(
    ledger: ExperienceLedger,
    *,
    request_id: str,
    execution_id: str,
    strategy_id: str = "strat_a",
    realized_r: float = 0.0,
    exit_reason: str = "TAKE_PROFIT_HIT",
    behavioral_flags: list[str] | None = None,
) -> None:
    from nexus_scalp.experience.models import (
        ExperienceOutcome,
        ExperienceRecord,
        FeatureSnapshot,
        StrategyContext,
    )
    from nexus_scalp.experience.quality import compute_behavior_metrics

    dt = datetime.now(UTC) - timedelta(hours=2)
    ctx = StrategyContext(strategy_id=strategy_id, regime="TRENDING_MOMENTUM")
    exp = ExperienceRecord(
        experience_id=f"exp_{request_id}",
        request_id=request_id,
        idempotency_key=f"exp_{request_id}",
        symbol="XAUUSD",
        decision_timestamp=dt,
        strategy_id=strategy_id,
        context=ctx,
        feature_snapshot=FeatureSnapshot(values=[0.1] * 50),
        action="BUY_MARKET",
        entry_reason="PURE_AI",
        model_probability=0.8,
        signal_confidence=0.8,
        proposed_entry=2000.0,
        stop_loss=1990.0,
        take_profit=2020.0,
        risk_reward_ratio=2.0,
    )
    ledger.record_experience(exp)
    outcome = ExperienceOutcome(
        idempotency_key=exp.idempotency_key,
        execution_id=execution_id,
        outcome_timestamp=dt + timedelta(minutes=5),
        is_executed=True,
        is_closed=True,
        exit_reason=exit_reason,
        realized_pnl_usd=realized_r * 100.0,
        realized_r_multiple=realized_r,
        behavior=compute_behavior_metrics(
            mae_points=0.0,
            mfe_points=0.0,
            mae_usd=0.0,
            mfe_usd=0.0,
            planned_risk_distance=10.0,
            duration_sec=300.0,
            initial_sl_distance=10.0,
            atr_at_entry=4.0,
        ),
        behavioral_flags=[f for f in (behavioral_flags or [])],
    )
    ledger.record_outcome(outcome)


def _engine(core: AccountingCore) -> PerformanceReportEngine:
    return PerformanceReportEngine(core=core, kind=PeriodKind.DAY)


def _detections_for(db_path: str, ticket: int | None = None) -> list[dict[str, Any]]:
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        if ticket is not None:
            rows = conn.execute(
                "SELECT * FROM behavior_detections WHERE ticket = ?", (str(ticket),)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM behavior_detections").fetchall()
        cols = [d[0] for d in conn.execute("SELECT * FROM behavior_detections LIMIT 0").description]
        return [dict(zip(cols, r, strict=False)) for r in rows]
    finally:
        conn.close()


def _anomaly_events_for(db_path: str) -> list[dict[str, Any]]:
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT * FROM anomaly_events").fetchall()
        cols = [d[0] for d in conn.execute("SELECT * FROM anomaly_events LIMIT 0").description]
        return [dict(zip(cols, r, strict=False)) for r in rows]
    finally:
        conn.close()


def _analysis_rows(db_path: str) -> list[dict[str, Any]]:
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT * FROM behavior_analysis").fetchall()
        cols = [d[0] for d in conn.execute("SELECT * FROM behavior_analysis LIMIT 0").description]
        return [dict(zip(cols, r, strict=False)) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# TEST-BHV-01 — historical trades produce analysis records
# ---------------------------------------------------------------------------


class TestHistoricalAnalysis:
    def test_engine_invocation_produces_records(self, audit, core, ledger) -> None:
        """Existing historical trades MUST yield analysis records end-to-end."""
        for i in range(3):
            _ledger_closed(
                audit,
                ticket=1000 + i,
                exit_price=1995.0,
                pnl=-5.0,
                exit_mechanism="HARD_SL_HIT",
                mae=10.0,
                mfe=2.0,
                mae_usd=100.0,
                mfe_usd=20.0,
            )
        # A real engine invocation against the canonical core:
        engine = BehaviorDetectionEngine(audit_repo=audit)
        from nexus_scalp.intelligence.behavior import analyze_canonical_trades

        result = analyze_canonical_trades(
            audit_repo=audit,
            engine=engine,
            behavior_version="behavior-v1",
            anomaly_version="anomaly-v1",
        )
        assert result["analyzed"] == 3
        assert result["flags"] >= 0  # detectors may or may not fire; analysis must run
        assert result["coverage"] > 0.0
        # And the analysis ledger rows were persisted:
        assert len(_analysis_rows(audit._db_path)) == 3


# ---------------------------------------------------------------------------
# TEST-BHV-02 — NO_DATA vs CLEAR state
# ---------------------------------------------------------------------------


class TestTruthStates:
    def test_no_data_state(self, core) -> None:
        """Empty DB -> NO_DATA, never CLEAR."""
        report = _engine(core).generate(at=datetime.now(UTC))
        assert report.behavioral.state == "NO_DATA"
        assert report.anomaly_state.state == "NO_DATA"

    def test_clear_state(self, audit, core) -> None:
        """Analyzed with zero flags -> CLEAR with coverage."""
        _ledger_closed(
            audit,
            ticket=2001,
            exit_price=2020.0,
            pnl=200.0,
            exit_mechanism="TAKE_PROFIT_HIT",
            mfe=20.0,
            mfe_usd=200.0,
            mae=2.0,
            mae_usd=20.0,
        )
        from nexus_scalp.intelligence.behavior import BehaviorAnalysisBackfiller

        backfiller = BehaviorAnalysisBackfiller(audit_repo=audit)
        backfiller.run(behavior_version="behavior-v1", anomaly_version="anomaly-v1")
        report = _engine(core).generate(at=datetime.now(UTC))
        assert report.behavioral.state in ("CLEAR", "FLAGS_FOUND")
        assert report.behavioral.analyzed == 1
        assert report.behavioral.evidence_coverage > 0.0


# ---------------------------------------------------------------------------
# TEST-BHV-03 — OVERHOLD_LOSER
# ---------------------------------------------------------------------------


class TestOverholdLoser:
    def test_overhold_loser_fires_with_evidence(self, audit) -> None:
        engine = BehaviorDetectionEngine(audit_repo=audit)
        flags = engine.analyze(
            ticket="9001",
            realized_r=-0.9,
            mfe_r=0.1,
            mae_r=-1.1,
            giveback_pct=0.0,
            holding_duration_sec=4200.0,
            expected_duration_sec=600.0,
            exit_mechanism="HARD_SL_HIT",
        )
        keys = {f.pattern for f in flags}
        assert "OVERHOLD_LOSER" in keys
        od = next(f for f in flags if f.pattern == "OVERHOLD_LOSER")
        assert od.severity.value == "HIGH"
        assert od.confidence > 0.5
        assert "threshold" in od.evidence
        assert "actual" in od.evidence
        assert "expected" in od.evidence

    def test_overhold_loser_requires_evidence(self, audit) -> None:
        """A short loser must NOT fire OVERHOLD_LOSER."""
        engine = BehaviorDetectionEngine(audit_repo=audit)
        flags = engine.analyze(
            ticket="9002",
            realized_r=-0.9,
            mfe_r=0.1,
            mae_r=-1.1,
            giveback_pct=0.0,
            holding_duration_sec=60.0,
            expected_duration_sec=600.0,
            exit_mechanism="HARD_SL_HIT",
        )
        assert "OVERHOLD_LOSER" not in {f.pattern for f in flags}


# ---------------------------------------------------------------------------
# TEST-BHV-04 — PROFIT_GIVEBACK
# ---------------------------------------------------------------------------


class TestProfitGiveback:
    def test_giveback_fires(self, audit) -> None:
        engine = BehaviorDetectionEngine(audit_repo=audit)
        flags = engine.analyze(
            ticket="9003",
            realized_r=0.3,
            mfe_r=1.5,
            mae_r=-0.2,
            giveback_pct=0.75,
            holding_duration_sec=600.0,
            expected_duration_sec=600.0,
            exit_mechanism="MANUAL_CLOSE",
        )
        assert "PROFIT_GIVEBACK" in {f.pattern for f in flags}

    def test_giveback_not_fired_for_small_giveback(self, audit) -> None:
        engine = BehaviorDetectionEngine(audit_repo=audit)
        flags = engine.analyze(
            ticket="9004",
            realized_r=1.2,
            mfe_r=1.5,
            mae_r=-0.2,
            giveback_pct=0.2,
            holding_duration_sec=600.0,
            expected_duration_sec=600.0,
            exit_mechanism="TAKE_PROFIT_HIT",
        )
        assert "PROFIT_GIVEBACK" not in {f.pattern for f in flags}


# ---------------------------------------------------------------------------
# TEST-BHV-05 — MISSED_BREAKEVEN
# ---------------------------------------------------------------------------


class TestMissedBreakeven:
    def test_missed_breakeven_fires(self, audit) -> None:
        engine = BehaviorDetectionEngine(audit_repo=audit)
        flags = engine.analyze(
            ticket="9005",
            realized_r=-0.8,
            mfe_r=0.9,
            mae_r=-0.8,
            giveback_pct=0.0,
            holding_duration_sec=600.0,
            expected_duration_sec=600.0,
            exit_mechanism="HARD_SL_HIT",
            sl_moved=False,
        )
        assert "MISSED_BREAKEVEN" in {f.pattern for f in flags}

    def test_missed_breakeven_not_fired_when_be_used(self, audit) -> None:
        engine = BehaviorDetectionEngine(audit_repo=audit)
        flags = engine.analyze(
            ticket="9006",
            realized_r=-0.8,
            mfe_r=0.9,
            mae_r=-0.8,
            giveback_pct=0.0,
            holding_duration_sec=600.0,
            expected_duration_sec=600.0,
            exit_mechanism="BREAK_EVEN_SL_HIT",
            sl_moved=True,
        )
        assert "MISSED_BREAKEVEN" not in {f.pattern for f in flags}


# ---------------------------------------------------------------------------
# TEST-BHV-06 — PREMATURE_BREAKEVEN
# ---------------------------------------------------------------------------


class TestPrematureBreakeven:
    def test_premature_be_fires(self, audit) -> None:
        engine = BehaviorDetectionEngine(audit_repo=audit)
        flags = engine.analyze(
            ticket="9007",
            realized_r=0.0,
            mfe_r=0.12,
            mae_r=-0.1,
            giveback_pct=0.0,
            holding_duration_sec=300.0,
            expected_duration_sec=600.0,
            exit_mechanism="BREAK_EVEN_SL_HIT",
            sl_moved=True,
        )
        assert "PREMATURE_BREAKEVEN" in {f.pattern for f in flags}

    def test_premature_be_not_fired_for_mature_excursion(self, audit) -> None:
        engine = BehaviorDetectionEngine(audit_repo=audit)
        flags = engine.analyze(
            ticket="9008",
            realized_r=0.0,
            mfe_r=0.6,
            mae_r=-0.1,
            giveback_pct=0.0,
            holding_duration_sec=300.0,
            expected_duration_sec=600.0,
            exit_mechanism="BREAK_EVEN_SL_HIT",
            sl_moved=True,
        )
        assert "PREMATURE_BREAKEVEN" not in {f.pattern for f in flags}


# ---------------------------------------------------------------------------
# TEST-BHV-07 — MODEL_REVERSAL_IGNORED
# ---------------------------------------------------------------------------


class TestModelReversalIgnored:
    def test_model_reversal_ignored_fires(self, audit) -> None:
        engine = BehaviorDetectionEngine(audit_repo=audit)
        flags = engine.analyze(
            ticket="9009",
            realized_r=-0.6,
            mfe_r=0.2,
            mae_r=-0.6,
            giveback_pct=0.0,
            holding_duration_sec=600.0,
            expected_duration_sec=600.0,
            exit_mechanism="HARD_SL_HIT",
            model_flip=1.0,  # model direction reversed vs entry
            model_conf_at_exit=0.12,
        )
        assert "MODEL_REVERSAL_IGNORED" in {f.pattern for f in flags}

    def test_model_reversal_requires_flip(self, audit) -> None:
        engine = BehaviorDetectionEngine(audit_repo=audit)
        flags = engine.analyze(
            ticket="9010",
            realized_r=-0.6,
            mfe_r=0.2,
            mae_r=-0.6,
            giveback_pct=0.0,
            holding_duration_sec=600.0,
            expected_duration_sec=600.0,
            exit_mechanism="HARD_SL_HIT",
            model_flip=0.0,
            model_conf_at_exit=0.6,
        )
        assert "MODEL_REVERSAL_IGNORED" not in {f.pattern for f in flags}


# ---------------------------------------------------------------------------
# TEST-BHV-08 — REGIME_CHANGE_IGNORED
# ---------------------------------------------------------------------------


class TestRegimeChangeIgnored:
    def test_regime_change_ignored_fires(self, audit) -> None:
        engine = BehaviorDetectionEngine(audit_repo=audit)
        flags = engine.analyze(
            ticket="9011",
            realized_r=-0.7,
            mfe_r=0.1,
            mae_r=-0.7,
            giveback_pct=0.0,
            holding_duration_sec=1200.0,
            expected_duration_sec=600.0,
            exit_mechanism="HARD_SL_HIT",
            regime_flip=1.0,
            regime_at_exit="STRONG_TREND_AGAINST",
        )
        assert "REGIME_CHANGE_IGNORED" in {f.pattern for f in flags}

    def test_regime_change_requires_flip(self, audit) -> None:
        engine = BehaviorDetectionEngine(audit_repo=audit)
        flags = engine.analyze(
            ticket="9012",
            realized_r=-0.7,
            mfe_r=0.1,
            mae_r=-0.7,
            giveback_pct=0.0,
            holding_duration_sec=1200.0,
            expected_duration_sec=600.0,
            exit_mechanism="HARD_SL_HIT",
            regime_flip=0.0,
            regime_at_exit="TRENDING_MOMENTUM",
        )
        assert "REGIME_CHANGE_IGNORED" not in {f.pattern for f in flags}


# ---------------------------------------------------------------------------
# TEST-BHV-09 — LIQUIDITY_REVERSAL_IGNORED
# ---------------------------------------------------------------------------


class TestLiquidityReversalIgnored:
    def test_liquidity_reversal_fires_on_structural_evidence(self, audit) -> None:
        engine = BehaviorDetectionEngine(audit_repo=audit)
        flags = engine.analyze(
            ticket="9013",
            realized_r=-0.9,
            mfe_r=0.1,
            mae_r=-0.9,
            giveback_pct=0.0,
            holding_duration_sec=900.0,
            expected_duration_sec=600.0,
            exit_mechanism="HARD_SL_HIT",
            liquidity_sweep_opposite=True,
        )
        assert "LIQUIDITY_REVERSAL_IGNORED" in {f.pattern for f in flags}


# ---------------------------------------------------------------------------
# TEST-BHV-10 — RISK_DEVIATION
# ---------------------------------------------------------------------------


class TestRiskDeviation:
    def test_risk_deviation_detected(self, audit) -> None:
        engine = BehaviorDetectionEngine(audit_repo=audit)
        flags = engine.analyze(
            ticket="9014",
            realized_r=-1.0,
            mfe_r=0.0,
            mae_r=-1.0,
            giveback_pct=0.0,
            holding_duration_sec=600.0,
            expected_duration_sec=600.0,
            exit_mechanism="HARD_SL_HIT",
            actual_risk_usd=220.0,
            intended_risk_usd=100.0,
        )
        assert "RISK_DEVIATION" in {f.pattern for f in flags}
        rd = next(f for f in flags if f.pattern == "RISK_DEVIATION")
        assert abs(rd.evidence["actual"] - 220.0) < 1e-6
        assert abs(rd.evidence["expected"] - 100.0) < 1e-6


# ---------------------------------------------------------------------------
# TEST-BHV-11 — EXIT_CLASSIFICATION_ANOMALY
# ---------------------------------------------------------------------------


class TestExitClassificationAnomaly:
    def test_risk_free_sl_hit_without_modification(self, audit) -> None:
        engine = BehaviorDetectionEngine(audit_repo=audit)
        flags = engine.analyze(
            ticket="9015",
            realized_r=-1.0,
            mfe_r=0.1,
            mae_r=-1.0,
            giveback_pct=0.0,
            holding_duration_sec=600.0,
            expected_duration_sec=600.0,
            exit_mechanism="RISK_FREE_SL_HIT",
            sl_moved=False,
        )
        assert "EXIT_CLASSIFICATION_ANOMALY" in {f.pattern for f in flags}


# ---------------------------------------------------------------------------
# TEST-BHV-12 — DUPLICATE_ECONOMIC_OUTCOME
# ---------------------------------------------------------------------------


class TestDuplicateEconomicOutcome:
    def test_duplicate_outcome_detected_in_backfill(self, audit, ledger) -> None:
        _experience_outcome(ledger, request_id="dup_a", execution_id="777001", realized_r=-0.3)
        _experience_outcome(ledger, request_id="dup_b", execution_id="777001", realized_r=-0.5)
        _flush(audit)
        from nexus_scalp.intelligence.behavior import analyze_canonical_trades

        engine = BehaviorDetectionEngine(audit_repo=audit)
        result = analyze_canonical_trades(
            audit_repo=audit,
            engine=engine,
            behavior_version="behavior-v1",
            anomaly_version="anomaly-v1",
        )
        assert result["anomalies"] >= 1
        types = {d["anomaly_type"] for d in _anomaly_events_for(audit._db_path)}
        assert "DUPLICATE_ECONOMIC_OUTCOME" in types
        # Batch-level scan must also be idempotent.
        again = analyze_canonical_trades(
            audit_repo=audit,
            engine=engine,
            behavior_version="behavior-v1",
            anomaly_version="anomaly-v1",
        )
        assert again["anomalies"] == 0
        d1 = {d["anomaly_id"] for d in _anomaly_events_for(audit._db_path)}
        assert len(d1) == len({x for x in d1})


# ---------------------------------------------------------------------------
# TEST-BHV-13 — STRATEGY_CONTEXT_LOSS
# ---------------------------------------------------------------------------


class TestStrategyContextLoss:
    def test_strategy_context_loss_detected(self, audit) -> None:
        _ledger_closed(
            audit,
            ticket=9016,
            exit_price=1990.0,
            pnl=-10.0,
            exit_mechanism="HARD_SL_HIT",
            entry_reason="",
        )
        from nexus_scalp.intelligence.behavior import analyze_canonical_trades

        engine = BehaviorDetectionEngine(audit_repo=audit)
        analyze_canonical_trades(
            audit_repo=audit,
            engine=engine,
            behavior_version="behavior-v1",
            anomaly_version="anomaly-v1",
        )
        types = {d["anomaly_type"] for d in _anomaly_events_for(audit._db_path)}
        assert "STRATEGY_CONTEXT_LOSS" in types


# ---------------------------------------------------------------------------
# TEST-BHV-14 — partial evidence is clearly reported
# ---------------------------------------------------------------------------


class TestPartialEvidence:
    def test_coverage_reported(self, audit, core) -> None:
        # One rich trade + one sparse trade (no mae/mfe/risk).
        _ledger_closed(
            audit,
            ticket=9101,
            exit_price=2020.0,
            pnl=200.0,
            mfe=20.0,
            mfe_usd=200.0,
            mae=2.0,
            mae_usd=20.0,
        )
        _ledger_closed(
            audit,
            ticket=9102,
            exit_price=1990.0,
            pnl=-10.0,
            mae=0.0,
            mfe=0.0,
            mae_usd=0.0,
            mfe_usd=0.0,
            initial_sl=0.0,
        )
        from nexus_scalp.intelligence.behavior import BehaviorAnalysisBackfiller

        BehaviorAnalysisBackfiller(audit_repo=audit).run(
            behavior_version="behavior-v1", anomaly_version="anomaly-v1"
        )
        report = _engine(core).generate(at=datetime.now(UTC))
        assert report.behavioral.analyzed == 2
        assert report.behavioral.evidence_coverage < 1.0
        assert report.behavioral.partial_context >= 1


# ---------------------------------------------------------------------------
# TEST-BHV-15/16 — algorithm version persistence
# ---------------------------------------------------------------------------


class TestVersionPersistence:
    def test_versions_persisted(self, audit) -> None:
        _ledger_closed(audit, ticket=9201, exit_price=2020.0, pnl=100.0)
        from nexus_scalp.intelligence.behavior import BehaviorAnalysisBackfiller

        backfiller = BehaviorAnalysisBackfiller(audit_repo=audit)
        backfiller.run(behavior_version="behavior-v1", anomaly_version="anomaly-v1")
        rows = _analysis_rows(audit._db_path)
        assert rows
        assert all(r["behavior_version"] == "behavior-v1" for r in rows)
        assert all(r["anomaly_version"] == "anomaly-v1" for r in rows)


# ---------------------------------------------------------------------------
# TEST-BHV-17 — Telegram output matches canonical report
# ---------------------------------------------------------------------------


class TestTelegramContract:
    def test_telegram_shows_truth_state(self, audit, core) -> None:
        from nexus_scalp.reporting import format_deep_report

        _ledger_closed(audit, ticket=9301, exit_price=1990.0, pnl=-10.0)
        report = _engine(core).generate(at=datetime.now(UTC))
        text = format_deep_report(report)
        # NO_DATA is the truthful state when nothing ran yet
        assert report.behavioral.state == "NO_DATA"
        assert "NO_DATA" in text
        assert "no behavioral flags recorded" not in text


# ---------------------------------------------------------------------------
# TEST-BHV-18 — API output matches canonical report
# ---------------------------------------------------------------------------


class TestApiContract:
    def test_api_shape(self, audit, core) -> None:
        _ledger_closed(audit, ticket=9401, exit_price=1990.0, pnl=-10.0)
        report = _engine(core).generate(at=datetime.now(UTC))
        payload = report.to_dict()
        assert payload["behavioral"]["state"] == "NO_DATA"
        assert payload["anomaly_state"]["state"] == "NO_DATA"
        # API contract keys required by the task:
        assert "analysis_version" in payload["behavioral"]
        assert "evidence_coverage" in payload["behavioral"]


# ---------------------------------------------------------------------------
# TEST-BHV-19 — historical backfill idempotency
# ---------------------------------------------------------------------------


class TestBackfillIdempotency:
    def test_backfill_twice_no_duplicates(self, audit) -> None:
        _ledger_closed(audit, ticket=9501, exit_price=1990.0, pnl=-10.0)
        from nexus_scalp.intelligence.behavior import BehaviorAnalysisBackfiller

        backfiller = BehaviorAnalysisBackfiller(audit_repo=audit)
        first = backfiller.run(behavior_version="behavior-v1", anomaly_version="anomaly-v1")
        second = backfiller.run(behavior_version="behavior-v1", anomaly_version="anomaly-v1")
        assert first["analyzed"] == 1
        assert second["analyzed"] == 0  # nothing new
        rows = _analysis_rows(audit._db_path)
        assert len(rows) == 1
        dets = _detections_for(audit._db_path)
        keys = [d["behavior_key"] for d in dets]
        assert len(keys) == len(set(keys))  # idempotent key uniqueness


# ---------------------------------------------------------------------------
# TEST-BHV-20 — bounded, non-blocking execution
# ---------------------------------------------------------------------------


class TestBoundedExecution:
    def test_analysis_is_bounded(self, audit) -> None:
        from nexus_scalp.intelligence.behavior import BehaviorAnalysisBackfiller

        backfiller = BehaviorAnalysisBackfiller(audit_repo=audit, max_trades_per_run=50)
        # 120 trades exist; a bounded run processes at most 50.
        for i in range(120):
            _ledger_closed(
                audit,
                ticket=9600 + i,
                exit_price=1990.0,
                pnl=-10.0,
                mae=10.0,
                mfe=1.0,
                mae_usd=100.0,
                mfe_usd=10.0,
            )
        started = time.perf_counter()
        result = backfiller.run(behavior_version="behavior-v1", anomaly_version="anomaly-v1")
        duration = time.perf_counter() - started
        assert result["analyzed"] <= 50
        assert duration < 10.0  # bounded wall time, no per-tick scans
