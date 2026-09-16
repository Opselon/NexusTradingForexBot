"""Report transport contracts using real empirical replay and isolated SQLite.

Synthetic research observations are not MT5 Strategy Tester output. No broker,
client adapter, live execution, or promotion is exercised here.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.research.backtest import BacktestEngine
from nexus_scalp.research.economics import EconomicAssumptions, SwapProfile
from nexus_scalp.research.metrics import compute_backtest, compute_sized_economic_pnl
from nexus_scalp.research.models import ResearchDataset, ResearchSample, StrategyRegistryEntry
from nexus_scalp.research.registry import StrategyRegistry
from nexus_scalp.research.splitting import split_temporal
from nexus_scalp.strategies.factory.benchmark import build_benchmark_artifact
from nexus_scalp.strategies.factory.models import FactoryCandidate, StrategyDsl
from nexus_scalp.web.command_center_routes import CommandCenterAPI


@pytest.fixture
def dataset():
    base = datetime(2024, 1, 2, 9, tzinfo=UTC)
    samples = []
    for i, r in enumerate([2.0, -1.0, 1.0, -0.5, 1.5, -1.0, 2.0, -0.5, 99.0, 99.0]):
        samples.append(
            ResearchSample(
                sample_id=f"sample-{i}",
                experience_id=f"experience-{i}",
                idempotency_key=f"key-{i}",
                decision_timestamp=base + timedelta(hours=i),
                outcome_timestamp=base + timedelta(hours=i, minutes=5),
                symbol="XAUUSD",
                strategy_id="report-integration",
                strategy_version="1.0.0",
                direction="BUY" if i % 2 == 0 else "SELL",
                entry_price=2000.0,
                stop_loss=1990.0 if i % 2 == 0 else 2010.0,
                take_profit=2020.0 if i % 2 == 0 else 1980.0,
                risk_distance=10.0,
                realized_r=r,
                realized_pnl_usd=r * 17.0,
                holding_duration_sec=300.0,
                mae_r=0.25,
                mfe_r=max(0.5, r),
                signal_confidence=0.8,
            )
        )
    return ResearchDataset(
        dataset_id="synthetic-report-fixture",
        source="synthetic_integration_test",
        created_at=base,
        samples=samples,
        schema_ids=[samples[0].feature_schema_id],
        source_range={"start": base.isoformat(), "end": samples[-1].outcome_timestamp.isoformat()},
        provenance_extra={"synthetic": True, "fixture_version": 1},
    )


@pytest.fixture
def engine():
    return BacktestEngine(
        economic=EconomicAssumptions.production_like(
            starting_equity_usd=2500.0,
            swap=SwapProfile(
                long_usd_per_lot_per_rollover=-2.0,
                short_usd_per_lot_per_rollover=1.0,
            ),
        )
    )


def _run(engine, dataset, **kwargs):
    return engine.run(dataset, "report-integration", "1.0.0", **kwargs)


def _report(result):
    report = getattr(result, "report", None)
    assert isinstance(report, dict) and report, "computed BacktestResult must carry its report"
    return report


@pytest.fixture
def repo(tmp_path):
    audit = AuditRepository(db_url=f"sqlite:///{tmp_path / 'report.db'}")
    try:
        yield audit
    finally:
        audit.close()


def _persist(repo, result):
    registry = StrategyRegistry(repo)
    assert registry.upsert(
        StrategyRegistryEntry(
            strategy_id=result.strategy_id,
            strategy_version=result.strategy_version,
            backtest=result,
            sample_count=result.total_trades,
        )
    )
    repo._queue.join()
    return registry


def test_engine_report_partition_excludes_oos_observations(dataset, engine):
    result = _run(engine, dataset, use_split=True)
    split = split_temporal(dataset, purge_seconds=300.0, embargo_seconds=60.0)
    selected = split.train + split.validation
    assert result.total_trades == len(selected) == 7
    expected = compute_backtest(
        selected,
        result.strategy_id,
        result.strategy_version,
        dataset.dataset_id,
        engine.assumptions,
    )
    assert result.net_pnl_usd == pytest.approx(expected.net_pnl_usd)
    report = _report(result)
    assert report["settings"]["partition"] == "TRAIN_VALIDATION"
    assert report["settings"]["input_sample_count"] == 10
    assert report["settings"]["selected_sample_count"] == 7
    trade_ids = {trade["sample_id"] for trade in report["trades"]}
    assert trade_ids == {sample.sample_id for sample in selected}
    assert trade_ids.isdisjoint(sample.sample_id for sample in split.oos)
    # Extreme OOS returns must not affect the in-sample report or sized view.
    changed = dataset.model_copy(
        update={
            "samples": dataset.samples[:8]
            + [
                s.model_copy(update={"realized_r": -999.0, "realized_pnl_usd": -99999.0})
                for s in dataset.samples[8:]
            ]
        }
    )
    assert _report(_run(engine, changed, use_split=True)) == report


def test_report_metadata_nonfinite_values_are_unavailable(dataset, engine):
    changed = dataset.model_copy(update={"provenance_extra": {"quality": float("nan")}})
    report = _report(_run(engine, changed))
    assert report["settings"]["dataset_provenance"]["quality"] is None
    json.dumps(report, allow_nan=False)


def test_invalid_samples_do_not_enter_sized_revaluation(dataset, engine):
    invalid = dataset.samples[0].model_copy(update={"realized_pnl_usd": float("inf")})
    changed = dataset.model_copy(update={"samples": [invalid]})
    result = _run(engine, changed)
    assert result.report["data_quality"]["status"] == "INVALID_INPUT"
    assert result.sized is None
    assert result.report["sized"] is None
    json.dumps(result.model_dump(mode="json"), allow_nan=False)


def test_engine_report_full_dataset_context_is_explicit(dataset, engine):
    result = _run(engine, dataset)
    report = _report(result)
    settings = report["settings"]
    assert settings["partition"] == "FULL_DATASET"
    assert settings["input_sample_count"] == settings["selected_sample_count"] == 10
    assert settings["dataset_provenance"] == dataset.provenance_extra
    assert report["engine"] == "NSE_EMPIRICAL_REPLAY"
    assert result.evaluation_mode == "EMPIRICAL_REPLAY"
    assert len(report["trades"]) == 10
    assert json.loads(json.dumps(report, allow_nan=False)) == report
    assert _report(_run(engine, dataset)) == report


def test_economic_report_keeps_raw_and_sized_results_separate(dataset, engine):
    result = _run(engine, dataset)
    raw = compute_backtest(
        dataset.samples,
        result.strategy_id,
        result.strategy_version,
        dataset.dataset_id,
        engine.assumptions,
    )
    sized = compute_sized_economic_pnl(dataset.samples, engine.economic)
    assert result.net_pnl_usd == pytest.approx(raw.net_pnl_usd)
    assert result.equity_curve_r == raw.equity_curve_r
    assert result.sized.model_dump(mode="json") == sized.model_dump(mode="json")
    assert sum(sized.sized_pnl_usd) != pytest.approx(result.net_pnl_usd)
    report = _report(result)
    assert report["economic"] == engine.economic.model_dump(mode="json")
    assert report["sized"] == sized.model_dump(mode="json")
    assert report["performance"] == _report(raw)["performance"]
    assert report["trades"] == _report(raw)["trades"]


def test_factory_projection_preserves_computed_report(dataset, engine):
    result = _run(engine, dataset)
    report = _report(result)
    candidate = FactoryCandidate(
        candidate_id="report-candidate",
        definition_hash="fixture-hash",
        generation_id="fixture-generation",
        dsl=StrategyDsl(),
    )
    pipeline_result = {"backtest": result.model_dump(mode="json"), "lifecycle": "DISCOVERED"}
    before = json.dumps(pipeline_result, sort_keys=True, allow_nan=False)
    artifact = build_benchmark_artifact(candidate, pipeline_result)
    assert artifact["backtest"]["report"] == report
    assert artifact["backtest"]["total_trades"] == result.total_trades
    assert artifact["eligible_for_next_gen"] is False
    assert json.dumps(pipeline_result, sort_keys=True, allow_nan=False) == before


def test_report_survives_registry_and_command_center_persistence(dataset, engine, repo):
    result = _run(engine, dataset)
    report = _report(result)
    registry = _persist(repo, result)
    loaded = registry.get(result.strategy_id)
    assert loaded is not None and loaded.backtest is not None
    assert _report(loaded.backtest) == report
    payload = CommandCenterAPI(repo).validation_pipeline(result.strategy_id)
    assert payload["backtest_report"] == report
    assert payload["lifecycle"] == "DISCOVERED"
    assert payload["gates"][0]["total_trades"] == result.total_trades
    assert json.loads(json.dumps(payload, allow_nan=False))["backtest_report"] == report
    loaded_again = registry.get(result.strategy_id)
    assert loaded_again is not None
    assert loaded_again.model_dump(mode="json") == loaded.model_dump(mode="json")


def test_command_center_reads_persisted_legacy_result_without_fabricating_report(
    dataset, engine, repo
):
    result = _run(engine, dataset)
    registry = _persist(repo, result)
    legacy = result.model_dump(mode="json")
    legacy.pop("report", None)
    # Deliberately write old serialized form, not a new model with default report.
    with sqlite3.connect(repo._db_path) as conn:
        conn.execute(
            "UPDATE strategy_registry SET backtest=? WHERE strategy_id=?",
            (json.dumps(legacy), result.strategy_id),
        )
    loaded = registry.get(result.strategy_id)
    assert loaded is not None and loaded.backtest is not None
    assert loaded.backtest.total_trades == 10
    payload = CommandCenterAPI(repo).validation_pipeline(result.strategy_id)
    assert payload["backtest_report"] is None
    assert payload["gates"][0]["status"] == "PASS"
    assert payload["gates"][0]["expectancy_r"] == pytest.approx(result.expectancy_r)
    assert payload["lifecycle"] == "DISCOVERED"
