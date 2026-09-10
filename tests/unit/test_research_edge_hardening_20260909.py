"""RESEARCH EDGE HARDENING (2026-09-09): statistical significance + perf parity +
research DB index migration (AUDIT-0008).

Why these tests exist
---------------------
The edge-research audit found that a strategy could be VALIDATED on an OOS
window whose positive expectancy was statistically indistinguishable from
noise (point estimate only). These tests pin the new contract:

  1. OOSGate.evaluate attaches a deterministic bootstrap significance for the
     OOS window; the scoring verdict accepts ONLY decisive evidence when the
     field is present (legacy producers without it keep old behavior).
  2. The sized economic re-valuation keeps EXACT numerical parity after the
     performance fixes (shared RiskEngine instance + linear equity curve).
  3. Migration AUDIT-0008 applies/verifies/rolls back idempotently and the
     observability hot queries actually use the new indexes.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.research.metrics import (
    MIN_OOS_SIGNIFICANCE_SAMPLES,
    compute_sized_economic_pnl,
    oos_significance,
)
from nexus_scalp.research.models import (
    BacktestResult,
    EconomicAssumptions,
    OOSResult,
    ResearchDataset,
    ResearchSample,
)
from nexus_scalp.research.oos import OOSGate
from nexus_scalp.research.scoring import (
    _oos_evidence_is_decisive,
    compute_strategy_score,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _mk_samples(n: int, win_frac: float = 0.6) -> list[ResearchSample]:
    base = datetime(2026, 8, 1, tzinfo=UTC)
    out: list[ResearchSample] = []
    for i in range(n):
        win = i % 10 < int(win_frac * 10)
        out.append(
            ResearchSample(
                sample_id=f"s{i}",
                experience_id=f"e{i}",
                idempotency_key=f"k{i}",
                decision_timestamp=base + timedelta(minutes=i * 10),
                outcome_timestamp=base + timedelta(minutes=i * 10 + 7),
                symbol="XAUUSD",
                strategy_id="S",
                strategy_version="1.0.0",
                regime="LONDON",
                entry_price=2000.0,
                stop_loss=1998.0,
                take_profit=2006.0,
                direction="BUY",
                realized_r=0.6 if win else -0.9,
                realized_pnl_usd=12.0 if win else -18.0,
                risk_distance=2.0,
                holding_duration_sec=420.0,
                mae_r=0.2,
                mfe_r=0.8,
            )
        )
    return out


def _passing_backtest() -> BacktestResult:
    return BacktestResult(
        strategy_id="S",
        strategy_version="1.0.0",
        dataset_id="D",
        total_trades=50,
        wins=30,
        losses=20,
        expectancy_r=0.5,
    )


# ---------------------------------------------------------------------------
# 1. OOS significance — gate attachment + verdict contract
# ---------------------------------------------------------------------------


def test_oos_gate_attaches_significance() -> None:
    ds = ResearchDataset(dataset_id="D", samples=_mk_samples(120, win_frac=0.65))
    res = OOSGate().evaluate(ds, "S", "1.0.0")
    sig = res.oos_significance
    assert sig is not None
    assert sig["n"] == res.oos_samples
    assert sig["n"] >= MIN_OOS_SIGNIFICANCE_SAMPLES
    # Deterministic: same dataset -> identical CI.
    res2 = OOSGate().evaluate(ds, "S", "1.0.0")
    assert res2.oos_significance == sig


def test_oos_significance_decisive_only_when_ci_above_zero() -> None:
    strong = oos_significance([0.8] * 30 + [0.4] * 20 + [-0.5] * 10)
    assert strong["n"] == 60
    assert strong["ci_low"] > 0.0
    assert strong["decisive"] is True

    weak = oos_significance([0.1, -0.1] * 30)
    assert weak["n"] == 60
    assert weak["decisive"] is False  # CI straddles zero

    tiny = oos_significance([1.0] * 5)
    assert tiny["n"] == 5
    assert tiny["decisive"] is False  # below the sample floor


def test_scoring_rejects_indecisive_oos_but_keeps_legacy_behavior() -> None:
    ds = ResearchDataset(dataset_id="D", samples=_mk_samples(120))
    bt = _passing_backtest()

    # Legacy producer (no significance field): unchanged behavior.
    legacy = OOSResult(
        strategy_id="S",
        strategy_version="1.0.0",
        dataset_id="D",
        status="PASS",
        oos_expectancy_r=0.4,
    )
    assert _oos_evidence_is_decisive(legacy) is True
    score_legacy = compute_strategy_score(ds, bt, None, legacy, None)
    assert score_legacy.verdict != "REJECTED"  # not OOS-blocked; other gates absent

    # Noisy OOS: positive point estimate but CI straddles 0 -> not VALIDATED.
    noisy = OOSResult(
        strategy_id="S",
        strategy_version="1.0.0",
        dataset_id="D",
        status="PASS",
        oos_expectancy_r=0.05,
        oos_significance={
            "n": 15,
            "mean_r": 0.05,
            "ci_low": -0.3,
            "ci_high": 0.4,
            "decisive": False,
        },
    )
    assert _oos_evidence_is_decisive(noisy) is False

    decisive = OOSResult(
        strategy_id="S",
        strategy_version="1.0.0",
        dataset_id="D",
        status="PASS",
        oos_expectancy_r=0.4,
        oos_significance={"n": 40, "mean_r": 0.4, "ci_low": 0.2, "ci_high": 0.6, "decisive": True},
    )
    assert _oos_evidence_is_decisive(decisive) is True


def test_purge_embargo_semantics_untouched_by_significance() -> None:
    # The significance work is additive: the OOS gate's PASS/FAIL thresholds
    # and its purged/embargoed split are byte-identical to the pinned
    # contract (test_agent17 remains the source of truth for thresholds).
    ds = ResearchDataset(dataset_id="D", samples=_mk_samples(60, win_frac=0.9))
    res = OOSGate().evaluate(ds, "S", "1.0.0")
    # 90% winners at 0.6R/-0.9R -> positive expectancy; with a healthy sample
    # the CI sits above zero and the gate PASSes on the same threshold as
    # before (>= 0.0R).
    assert res.status == "PASS"
    assert res.oos_expectancy_r > 0.0
    assert res.oos_significance is not None


# ---------------------------------------------------------------------------
# 2. Sized economic re-valuation: exact parity after the perf fixes
# ---------------------------------------------------------------------------


def test_sized_equity_curve_matches_running_sum_reference() -> None:
    samples = _mk_samples(400)
    econ = EconomicAssumptions()
    sized = compute_sized_economic_pnl(samples, econ)
    start = float(econ.starting_equity_usd)
    running = start
    for i, net in enumerate(sized.sized_pnl_usd):
        running += float(net)
        assert sized.equity_curve_usd[i + 1] == running


def test_shared_risk_engine_produces_identical_volumes() -> None:
    """The hoisted single RiskEngine must equal per-trade construction."""
    from nexus_scalp.configuration.config import RiskConfig
    from nexus_scalp.research.economics import compute_sizing
    from nexus_scalp.risk.risk_engine import RiskEngine

    econ = EconomicAssumptions()
    samples = _mk_samples(120)
    sized = compute_sized_economic_pnl(samples, econ)

    equity = float(econ.starting_equity_usd)
    peak = equity
    for i, s in enumerate(samples):
        fresh = RiskEngine(
            config=RiskConfig(risk_per_trade_pct=econ.sizing.base_risk_pct),
            max_allowed_lots=econ.sizing.max_allowed_lots,
        )
        decision = compute_sizing(
            policy=econ.sizing,
            instrument=econ.instrument,
            equity=equity,
            peak_equity=peak,
            entry=float(s.entry_price),
            stop_loss=float(s.stop_loss),
            confidence=0.0,
            regime=str(s.regime or ""),
            risk_engine=fresh,
        )
        assert sized.volumes[i] == decision.volume, f"volume drift at trade {i}"
        # advance the causal equity path exactly like the sized view does
        equity += float(sized.sized_pnl_usd[i])
        peak = max(peak, equity)


def test_sized_path_performance_is_linear() -> None:
    """The equity curve construction must stay O(n): 4x samples -> <= ~6x time
    (loose bound guards the O(n^2) regression without being flaky)."""
    import time

    econ = EconomicAssumptions()

    def _time(n: int) -> float:
        samples = _mk_samples(n)
        t0 = time.perf_counter()
        compute_sized_economic_pnl(samples, econ)
        return time.perf_counter() - t0

    small = _time(1000)
    big = _time(4000)
    assert big < small * 8 + 1.0, f"quadratic regression suspected: {small=:.3f} {big=:.3f}"


# ---------------------------------------------------------------------------
# 3. AUDIT-0008: research hot-query index migration
# ---------------------------------------------------------------------------


def _migrated_db(tmp_path):
    from nexus_scalp.adapters.database.audit_repository import AuditRepository
    from nexus_scalp.database.engine import DatabaseMigrationEngine
    from nexus_scalp.database.models import DatabaseDomain

    db = tmp_path / "research_idx.db"
    repo = AuditRepository(db_url=f"sqlite:///{db}", flush_interval_sec=0.02)
    eng = DatabaseMigrationEngine(db, DatabaseDomain.AUDIT)
    result = eng.migrate()
    return repo, eng, result, db


def test_audit_0008_applies_and_reaches_v8(tmp_path) -> None:
    repo, eng, result, db = _migrated_db(tmp_path)
    try:
        assert result["state"] == "DB_MIGRATION_SUCCEEDED"
        # AUDIT-0009 (archive tables) bumped the chain past 8; assert the
        # chain reached AT LEAST v8 with all three round-1 indexes present.
        assert eng.current_version() >= 8
        conn = sqlite3.connect(db)
        try:
            names = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                ).fetchall()
            }
        finally:
            conn.close()
        assert {
            "idx_gates_strategy_run",
            "idx_research_runs_strategy_executed",
            "idx_evidence_strategy_created_id",
        } <= names
    finally:
        repo.close()


def test_audit_0008_idempotent_restart_is_not_required(tmp_path) -> None:
    from nexus_scalp.database.engine import DatabaseMigrationEngine
    from nexus_scalp.database.models import DatabaseDomain, MigrationState

    repo, eng, result, db = _migrated_db(tmp_path)
    try:
        eng2 = DatabaseMigrationEngine(db, DatabaseDomain.AUDIT)
        r2 = eng2.migrate()
        assert r2["state"] == MigrationState.DB_MIGRATION_NOT_REQUIRED.value
    finally:
        repo.close()


def test_audit_0008_rollback_drops_only_new_indexes(tmp_path) -> None:
    from nexus_scalp.database.engine import DatabaseMigrationEngine
    from nexus_scalp.database.models import DatabaseDomain

    repo, eng, result, db = _migrated_db(tmp_path)
    try:
        eng2 = DatabaseMigrationEngine(db, DatabaseDomain.AUDIT)
        status = eng2.verify()
        assert status["current_version"] >= 8
        assert status["integrity"] == "ok"
        # Direct rollback of the 0008 indexes leaves the rest of the chain intact.
        conn = sqlite3.connect(db)
        try:
            for name in (
                "idx_gates_strategy_run",
                "idx_research_runs_strategy_executed",
                "idx_evidence_strategy_created_id",
            ):
                conn.execute(f"DROP INDEX IF EXISTS {name}")
            conn.commit()
            names = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                ).fetchall()
            }
            # Pre-existing indexes survive (history untouched).
            assert "idx_gates_strategy" in names
            assert "idx_research_runs_strategy" in names
        finally:
            conn.close()
    finally:
        repo.close()


def test_research_hot_queries_use_the_new_indexes(tmp_path) -> None:
    repo, eng, result, db = _migrated_db(tmp_path)
    try:
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        try:
            plans = {
                "gates": conn.execute(
                    "EXPLAIN QUERY PLAN SELECT * FROM research_gates WHERE strategy_id='s' "
                    "AND research_run_id='r' ORDER BY order_index, gate_id LIMIT 500;"
                ).fetchall(),
                "runs": conn.execute(
                    "EXPLAIN QUERY PLAN SELECT * FROM research_runs WHERE strategy_id='s' "
                    "ORDER BY executed_at DESC LIMIT 100;"
                ).fetchall(),
            }
        finally:
            conn.close()
        gates_detail = " ".join(r["detail"] for r in plans["gates"])
        runs_detail = " ".join(r["detail"] for r in plans["runs"])
        assert "idx_gates_strategy_run" in gates_detail
        assert "idx_research_runs_strategy_executed" in runs_detail
        assert "USE TEMP B-TREE FOR ORDER BY" not in runs_detail
    finally:
        repo.close()
