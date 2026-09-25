"""Pure calculation fixtures only: synthetic examples, NOT real trading results."""

from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.research.metrics import compute_backtest
from nexus_scalp.research.models import ExecutionAssumptions, ResearchSample


def sample(index=0, r=-1.0, usd=-100.0, **updates):
    values = dict(
        sample_id=f"fixture-{index}",
        experience_id=f"fixture-exp-{index}",
        idempotency_key=f"fixture-key-{index}",
        strategy_id="fixture-strategy",
        decision_timestamp=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=index),
        outcome_timestamp=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=index + 1),
        symbol="XAUUSD",
        realized_r=r,
        realized_pnl_usd=usd,
        risk_distance=1.0,
        direction="BUY",
    )
    values.update(updates)
    return ResearchSample(**values)


def calculate(samples, **assumptions):
    return compute_backtest(
        samples,
        "fixture-strategy",
        "test-v1",
        "fixture-dataset",
        ExecutionAssumptions(**assumptions),
    )


@pytest.mark.parametrize("r,usd", [(-1.0, -100.0), (1.0, 100.0), (1.0, -100.0), (0.01, 1.0)])
def test_added_friction_never_improves_usd_losses(r, usd):
    baseline = calculate([sample(r=r, usd=usd)])
    stressed = calculate([sample(r=r, usd=usd)], spread_ticks=5.0)
    assert stressed.net_pnl_usd == pytest.approx(usd - 0.05 * abs(usd / r))
    assert stressed.net_pnl_usd < baseline.net_pnl_usd


def test_max_drawdown_usd_uses_usd_path_regression():
    # Pure calculation fixture: verify drawdown units
    s1 = sample(index=0, r=1.0, usd=100.0)
    s2 = sample(index=1, r=-0.5, usd=-500.0)
    # R equity: 0 -> 1.0 -> 0.5 (DD_R = 0.5)
    # USD equity: 0 -> 100.0 -> -400.0 (DD_USD = 500.0)
    result = calculate([s1, s2])
    assert result.max_drawdown_r == pytest.approx(0.5)
    assert result.max_drawdown_usd == pytest.approx(500.0)


def test_report_describes_same_adjusted_partition_without_broker_fabrication():
    import json

    observations = [sample(1, -1, -200, direction=" short "), sample(0, 2, 100, direction="long")]
    result = calculate(observations, spread_ticks=2)
    report = result.report
    assert set(report) == {
        "schema_version",
        "engine",
        "settings",
        "performance",
        "drawdown",
        "directions",
        "trade_statistics",
        "holding_excursion",
        "data_quality",
        "trades",
        "orders",
        "curves",
        "limitations",
    }
    assert report["schema_version"] == "1"
    assert report["engine"] == "NSE_EMPIRICAL_REPLAY"
    assert report["settings"]["strategy_id"] == result.strategy_id
    assert report["settings"]["dataset_id"] == result.dataset_id
    assert report["settings"]["assumptions"] == result.assumptions.model_dump(mode="json")
    assert report["settings"]["initial_deposit_usd"] is None
    assert report["settings"]["leverage"] is None
    perf = report["performance"]
    assert perf["gross_profit_usd"] == 99
    assert perf["gross_loss_usd"] == -204
    assert perf["net_pnl_usd"] == result.net_pnl_usd == -105
    assert perf["expected_payoff_usd"] == -52.5
    assert perf["profit_factor_usd"] == pytest.approx(99 / 204)
    assert perf["win_rate"] == result.win_rate == 0.5
    assert "R" in perf["win_rate_basis"]
    assert perf["sharpe_ratio_r"] == pytest.approx(0.48 / (4.5**0.5))
    assert report["drawdown"]["max_drawdown_usd"] == result.max_drawdown_usd == 204
    assert report["drawdown"]["equity_drawdown_usd"] is None
    assert report["directions"]["BUY"]["total_trades"] == 1
    assert report["directions"]["SELL"]["net_pnl_usd"] == -204
    assert report["curves"]["cumulative_r"] == pytest.approx([1.98, 0.96])
    assert report["curves"]["closed_pnl_usd"] == [99, -105]
    assert report["orders"] is None
    first = report["trades"][0]
    assert first["sample_id"] == "fixture-0"
    assert first["experience_id"] == "fixture-exp-0"
    assert first["direction"] == "BUY"
    assert first["adjusted_r"] == 1.98
    assert first["modeled_cost_usd"] == 1
    for key in ("exit_price", "volume", "commission_usd", "swap_usd"):
        assert first[key] is None
    assert report["data_quality"]["mt5_modeling_quality_pct"] is None
    assert report["holding_excursion"]["mae_r"]["mean"] == 0.0
    assert "unrecorded" in report["holding_excursion"]["mae_r"]["availability_note"]
    assert set(report["directions"]) == {"BUY", "SELL", "UNKNOWN"}
    assert report["limitations"]
    assert json.loads(json.dumps(report, allow_nan=False)) == report


def test_empty_and_bounded_report_have_explicit_undefined_statistics():
    empty = calculate([]).report
    assert empty["trades"] == []
    assert empty["performance"]["expected_payoff_usd"] is None
    assert empty["performance"]["win_rate"] is None
    assert empty["performance"]["profit_factor_usd"] is None
    result = calculate([sample(i, 1, 10) for i in range(503)])
    report = result.report
    assert len(report["trades"]) == 500
    assert report["trade_statistics"]["total_trades"] == 503
    assert report["trade_statistics"]["truncated"] is True
    assert report["performance"]["net_pnl_usd"] == 5030
    assert report["performance"]["profit_factor_usd"] is None
    assert report["performance"]["sharpe_ratio_r"] is None
    assert len(report["curves"]["cumulative_r"]) == 503


def test_zero_r_has_unknown_modeled_usd_cost_and_explicit_model_limits():
    report = calculate([sample(r=0, usd=10, direction="???")], spread_ticks=2).report
    assert report["trades"][0]["modeled_cost_usd"] is None
    assert report["trades"][0]["direction"] == "UNKNOWN"
    assert report["directions"]["UNKNOWN"]["total_trades"] == 1
    assert report["settings"]["partition"] == "SUPPLIED_SAMPLES"
    assert "abs" in report["settings"]["usd_friction_model"]
    assert report["curves"].get("equity_usd") is None


def test_nonfinite_report_input_does_not_serialize_nan_or_infinity():
    import json

    for updates in ({"realized_r": float("nan")}, {"realized_pnl_usd": float("inf")}):
        result = calculate([sample().model_copy(update=updates)])
        assert result.report, "invalid input still requires an explicit quality report"
        assert result.report["data_quality"]["status"] == "INVALID_INPUT"
        json.dumps(result.report, allow_nan=False)


def test_sanitizer_handles_nested_provenance_without_mutating_input():
    from nexus_scalp.research import backtest_report

    assert hasattr(backtest_report, "sanitize_report")
    original = {"provenance": [{"bad": float("inf"), "zero": 0.0}]}
    safe = backtest_report.sanitize_report(original)
    assert safe == {"provenance": [{"bad": None, "zero": 0.0}]}
    assert original["provenance"][0]["bad"] == float("inf")
    assert calculate([sample()]).report["drawdown"]["recovery_duration_trades"] is None
    report = calculate([sample()], spread_ticks=float("inf")).report
    assert report["data_quality"]["status"] == "INVALID_INPUT"
    import json

    json.dumps(report, allow_nan=False)


def test_overflow_is_invalid_instead_of_fabricated_safe_metrics():
    result = calculate([sample(0, 1e308, 1e308), sample(1, 1e308, 1e308)])
    assert result.report["data_quality"]["status"] == "INVALID_INPUT"
    assert result.report["performance"]["net_pnl_usd"] is None
    assert result.report["drawdown"]["max_drawdown_usd"] is None
    import json

    json.dumps(result.model_dump(), allow_nan=False)
