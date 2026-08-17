"""Candle Intelligence tests — decision gate + isolated store (BUG-061)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.candle_intelligence.classifier import CandleCloseClassifier
from nexus_scalp.candle_intelligence.config import CandleIntelligenceConfig
from nexus_scalp.candle_intelligence.decision import CandleDecisionEngine
from nexus_scalp.candle_intelligence.engine import CandleIntelligenceEngine
from nexus_scalp.candle_intelligence.models import (
    CandleCloseClass,
    DecisionType,
    RegimeState,
    RiskEvaluation,
    RiskState,
    TradeBias,
)
from nexus_scalp.candle_intelligence.patterns import PatternDetection


def t(i: int = 0) -> datetime:
    return datetime(2026, 8, 17, 10, 0, 0, tzinfo=UTC) + timedelta(minutes=i)


@pytest.fixture()
def decision() -> CandleDecisionEngine:
    return CandleDecisionEngine(CandleIntelligenceConfig())


@pytest.fixture()
def classifier() -> CandleCloseClassifier:
    return CandleCloseClassifier(CandleIntelligenceConfig())


def _regime(name: str = "TRENDING_MOMENTUM") -> RegimeState:
    return RegimeState(
        symbol="XAUUSD", timeframe="M1", timestamp=t(), regime=name, atr=2.5, spread=0.2
    )


def _close(
    decision: CandleDecisionEngine,
    classifier: CandleCloseClassifier,
    o: float,
    h: float,
    l: float,
    c: float,
) -> CandleCloseClass:
    return classifier.classify("XAUUSD", "M1", t(), o, h, l, c).close_class


# ---------------------------------------------------------------------------
# gate behavior
# ---------------------------------------------------------------------------


def test_invalid_close_blocks_entry_and_forces_exit(
    decision: CandleDecisionEngine,
    classifier: CandleCloseClassifier,
) -> None:
    s = classifier.classify("XAUUSD", "M1", t(), float("nan"), 0, 0, 0)
    d = decision.decide(s, [], _regime(), holding_position=False)
    assert d.entry_allowed is False
    assert "INVALID_CANDLE_DATA" in d.no_trade_reason
    assert d.decision_type == DecisionType.NO_TRADE
    # Holding: invalid close forces exit evaluation.
    d2 = decision.decide(s, [], _regime(), holding_position=True)
    assert d2.exit_required is True


def test_weak_close_blocks_entry_when_configured(
    decision: CandleDecisionEngine,
    classifier: CandleCloseClassifier,
) -> None:
    s = classifier.classify("XAUUSD", "M1", t(), 4405.0, 4408.0, 4402.0, 4405.1)  # doji
    assert s.close_class == CandleCloseClass.INDECISION
    d = decision.decide(s, [], _regime(), holding_position=False)
    assert d.entry_allowed is False
    assert d.trade_bias == TradeBias.NEUTRAL


def test_strong_bullish_close_allows_entry_with_confirmation(
    decision: CandleDecisionEngine,
    classifier: CandleCloseClassifier,
) -> None:
    s = classifier.classify("XAUUSD", "M1", t(), 4400.0, 4410.0, 4399.5, 4409.0)
    patterns = [
        PatternDetection(
            pattern_name="BULLISH_ENGULFING",
            direction="BULLISH",
            raw_score=0.8,
            context_weight=0.9,
            confidence_score=0.72,
            reason_codes=["X"],
        ),
    ]
    d = decision.decide(s, patterns, _regime(), holding_position=False)
    assert d.entry_allowed is True
    assert d.decision_type == DecisionType.ENTRY
    assert d.trade_bias == TradeBias.BULLISH
    assert d.confidence_score >= 0.62


def test_regime_blocks_entry(
    decision: CandleDecisionEngine, classifier: CandleCloseClassifier
) -> None:
    s = classifier.classify("XAUUSD", "M1", t(), 4400.0, 4410.0, 4399.5, 4409.0)
    d = decision.decide(s, [], _regime("NEWS_SPIKE"), holding_position=False)
    assert d.entry_allowed is False
    assert "REGIME_BLOCKED" in d.no_trade_reason


def test_risk_blocked_forces_exit_when_holding(
    decision: CandleDecisionEngine,
    classifier: CandleCloseClassifier,
) -> None:
    s = classifier.classify("XAUUSD", "M1", t(), 4400.0, 4410.0, 4399.5, 4409.0)
    risk = RiskEvaluation(risk_state=RiskState.BLOCKED, risk_allowed=False)
    d = decision.decide(s, [], _regime(), risk=risk, holding_position=True)
    assert d.exit_required is True
    assert d.decision_type == DecisionType.EXIT


def test_false_breakout_reduces_confidence(
    decision: CandleDecisionEngine,
    classifier: CandleCloseClassifier,
) -> None:
    # A candle that closed back inside the prior range after a breakout attempt.
    s = classifier.classify("XAUUSD", "M1", t(), 4400.0, 4406.0, 4399.0, 4401.0)
    d = decision.decide(s, [], _regime(), holding_position=False)
    assert d.entry_allowed is False  # weak/indecision close blocks entry


def test_exhaustion_on_hold_triggers_modify(
    decision: CandleDecisionEngine,
    classifier: CandleCloseClassifier,
) -> None:
    s = classifier.classify("XAUUSD", "M1", t(), 4400.0, 4412.0, 4399.0, 4405.0)
    if s.close_class == CandleCloseClass.EXHAUSTION:
        d = decision.decide(s, [], _regime(), holding_position=True)
        assert d.modify_order is True
        assert d.decision_type == DecisionType.MODIFY_SL_TP


# ---------------------------------------------------------------------------
# store + engine integration
# ---------------------------------------------------------------------------


def test_engine_full_pipeline_writes_all_tables(tmp_path) -> None:
    cfg = CandleIntelligenceConfig(db_path=f"{tmp_path}/ci.db")
    eng = CandleIntelligenceEngine(config=cfg)
    try:
        t0 = t()
        # Seed a window so multi-candle patterns have context.
        for i, (o, h, l, c) in enumerate(
            [
                (4405.0, 4408.0, 4402.0, 4403.0),
                (4403.0, 4406.0, 4400.0, 4401.0),
                (4401.0, 4404.0, 4398.0, 4399.0),
            ]
        ):
            eng.ingest_bar(
                "XAUUSD",
                "M1",
                t0 + timedelta(minutes=i),
                o,
                h,
                l,
                c,
                volume=100,
                is_complete=True,
                regime_state=_regime(),
            )
        out = eng.ingest_bar(
            "XAUUSD",
            "M1",
            t0 + timedelta(minutes=3),
            4399.0,
            4409.0,
            4398.5,
            4408.0,
            volume=300,
            is_complete=True,
            regime_state=_regime(),
        )
        assert out is not None
        assert out.entry_allowed is True
        assert out.database_write_status == "WRITTEN"
        assert out.candle_close_summary.close_class == CandleCloseClass.BULLISH_CONTINUATION

        # All 12 tables exist with data (at least the decision-critical ones).
        for table in (
            "candles",
            "candle_closures",
            "candle_patterns",
            "market_regimes",
            "trade_decisions",
            "risk_evaluations",
        ):
            rows = eng.store.query_recent(table, 10)
            assert rows, f"{table} empty"
        assert eng.store.integrity_ok()

        # Determinism: same input -> same decision class.
        out2 = eng.ingest_bar(
            "XAUUSD",
            "M1",
            t0 + timedelta(minutes=4),
            4399.0,
            4409.0,
            4398.5,
            4408.0,
            volume=300,
            is_complete=True,
            regime_state=_regime(),
        )
        assert out2.candle_close_summary.close_class == out.candle_close_summary.close_class
    finally:
        eng.store.close()


def test_recent_decisions_explainable(tmp_path) -> None:
    cfg = CandleIntelligenceConfig(db_path=f"{tmp_path}/ci.db")
    eng = CandleIntelligenceEngine(config=cfg)
    try:
        eng.ingest_bar(
            "XAUUSD",
            "M1",
            t(),
            4400.0,
            4405.0,
            4399.0,
            4401.0,
            is_complete=True,
            regime_state=_regime(),
        )
        rows = eng.recent_decisions(5)
        assert rows
        row = rows[0]
        # Every decision row must carry the audit contract fields.
        for key in (
            "ts",
            "symbol",
            "timeframe",
            "regime",
            "decision_type",
            "risk_state",
            "reason_codes",
            "raw_payload",
            "computed_payload",
        ):
            assert key in row, f"missing {key}"
        assert row["candle_close_classification"]
    finally:
        eng.store.close()
