"""Report projection regression; synthetic payload, not trading evidence."""

from nexus_scalp.strategies.factory.benchmark import build_benchmark_artifact
from nexus_scalp.strategies.factory.models import FactoryCandidate, StrategyDsl


def _candidate():
    return FactoryCandidate(
        candidate_id="test-candidate",
        definition_hash="fixture",
        generation_id="test-generation",
        dsl=StrategyDsl(),
    )


def test_factory_preserves_authoritative_backtest_report():
    candidate = _candidate()
    report = {
        "schema_version": "1",
        "engine": "NSE_EMPIRICAL_REPLAY",
        "performance": {"net_pnl_usd": -12.5},
        "limitations": ["Not an MT5 tick simulation"],
    }
    result = build_benchmark_artifact(
        candidate, {"backtest": {"total_trades": 2, "report": report}}
    )
    assert result["backtest"].get("report") == report
