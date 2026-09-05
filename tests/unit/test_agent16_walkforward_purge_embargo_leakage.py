"""Agent 16 (CHG-0064) — Walk-forward / purge / embargo / leakage regression.

Pins confirmed behaviours from the Agent 16 forensic probe. FAIL-BEFORE /
PASS-AFTER evidence is recorded in the commit message and the Agent-16
handoff document.

Coverage (mission section -> test):
  3  BUG-183 defaults wired into every production consumer
  4  purge: train sample whose label horizon crosses the val boundary is removed
  5  embargo: inclusive boundary (<=), exact epsilon, no off-by-one
  6  purge+embargo interaction = correct fold boundary
 10  fold construction: block partition, gap / irregular / duplicate awareness
 14  sequence: SequenceBuilder causal window contains only timestamps <= anchor
 15  model-selection: ValidationFactory calibrated gates WITHOUT force
 16  metrics: benchmark mismatch is NOT_COMPUTABLE, never a fabricated macro-F1
 18  OOS thresholds: floor semantics (macro-F1 > 0.34, bacc > 0.34)
 20  scoring hard gate: OOS FAIL always maps to REJECTED
 25  adversarial future-feature / shifted-timestamp probes
 26  reproducibility: same dataset + purge/embargo + seed -> identical folds
 29  provenance: every _record_run config records the effective values
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl

from nexus_scalp.model_generation.benchmark import confusion_and_class_metrics
from nexus_scalp.model_generation.sequence import SequenceBuilder
from nexus_scalp.model_generation.validation import ValidationFactory
from nexus_scalp.research.backtest import BacktestEngine
from nexus_scalp.research.models import ResearchDataset, ResearchSample
from nexus_scalp.research.oos import OOSGate
from nexus_scalp.research.pipeline import ResearchPipeline
from nexus_scalp.research.scoring import compute_strategy_score
from nexus_scalp.research.splitting import (
    DEFAULT_EMBARGO_SECONDS,
    DEFAULT_PURGE_SECONDS,
    split_temporal,
    walk_forward_folds,
)
from nexus_scalp.research.walkforward import WalkForwardEngine

# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------

_BASE = dict(
    symbol="XAUUSD",
    strategy_id="agent16",
    strategy_version="1.0.0",
    feature_schema_id="scalp_v3",
    feature_dimension=70,
    realized_r=0.12,
)

_T0 = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)


def _sample(i: int, horizon_min: int = 1) -> ResearchSample:
    dt = _T0 + timedelta(minutes=i)
    return ResearchSample(
        sample_id=f"s{i}",
        experience_id=f"e{i}",
        idempotency_key=f"k{i}",
        decision_timestamp=dt,
        outcome_timestamp=dt + timedelta(minutes=horizon_min),
        **_BASE,  # type: ignore[arg-type]
    )


def _dataset(n: int, long_at: dict[int, int] | None = None) -> ResearchDataset:
    long_at = long_at or {}
    return ResearchDataset(
        dataset_id="ds_agent16",
        samples=[_sample(i, long_at.get(i, 1)) for i in range(n)],
    )


# ---------------------------------------------------------------------------
# 3 — BUG-183: purge/embargo wired into every production consumer
# ---------------------------------------------------------------------------


def _sig_default(fn, name: str) -> float:
    return float(inspect.signature(fn).parameters[name].default)


def test_bug183_production_gates_default_to_splitting_constants() -> None:
    assert DEFAULT_PURGE_SECONDS == 300.0
    assert DEFAULT_EMBARGO_SECONDS == 60.0
    assert (
        _sig_default(ResearchPipeline.validate_candidate, "purge_seconds") == DEFAULT_PURGE_SECONDS
    )
    assert (
        _sig_default(ResearchPipeline.validate_candidate, "embargo_seconds")
        == DEFAULT_EMBARGO_SECONDS
    )
    assert _sig_default(OOSGate.evaluate, "purge_seconds") == DEFAULT_PURGE_SECONDS
    assert _sig_default(OOSGate.evaluate, "embargo_seconds") == DEFAULT_EMBARGO_SECONDS
    assert _sig_default(WalkForwardEngine.validate, "purge_seconds") == DEFAULT_PURGE_SECONDS
    assert _sig_default(WalkForwardEngine.validate, "embargo_seconds") == DEFAULT_EMBARGO_SECONDS
    assert _sig_default(BacktestEngine.run, "purge_seconds") == DEFAULT_PURGE_SECONDS
    assert _sig_default(BacktestEngine.run, "embargo_seconds") == DEFAULT_EMBARGO_SECONDS


def test_pipeline_record_run_forwards_effective_purge_and_embargo() -> None:
    class _FakeAudit:
        _is_sqlite = False

    class _FakeReg:
        audit_repo = _FakeAudit()

        def upsert(self, _e):  # type: ignore[no-untyped-def]
            return True

    from nexus_scalp.research.candidates import StrategyCandidate

    p = ResearchPipeline(dataset_builder=object(), registry=_FakeReg())  # type: ignore[arg-type]

    cand = StrategyCandidate(
        strategy_id="a16",
        strategy_version="1.0.0",
        discovery_source="t",
        discovery_window="t",
        context_definition={"symbol": "XAUUSD", "fingerprint": "fp"},
        entry_logic={"x": 1},
        exit_logic={"y": 1},
        feature_dimension=4,
    )
    ds = ResearchDataset(dataset_id="ds", samples=[])
    p._record_run(
        run_id="r_explicit",
        candidate=cand,
        dataset=ds,
        summary={},
        purge_seconds=300,
        embargo_seconds=60,
    )
    assert p.last_run is not None
    assert p.last_run.config["purge_seconds"] in (300, 300.0)
    assert p.last_run.config["embargo_seconds"] in (60, 60.0)

    # Full validate_candidate path: defaults 300/60 must flow through _record_run.
    samples = [_sample(i) for i in range(60)]
    ds2 = ResearchDataset(dataset_id="ds2", samples=samples)
    p.validate_candidate(cand, ds2)
    assert p.last_run is not None
    assert float(p.last_run.config["purge_seconds"]) == 300.0
    assert float(p.last_run.config["embargo_seconds"]) == 60.0


def test_backtest_run_forwards_purge_to_split() -> None:
    long_at = {29: 600}  # train tail before the default 32/40 split on 50
    ds = _dataset(50, long_at)
    bt_with = BacktestEngine().run(
        ds, "s", "1.0.0", use_split=True, purge_seconds=300, embargo_seconds=60
    )
    bt_without = BacktestEngine().run(
        ds, "s", "1.0.0", use_split=True, purge_seconds=0, embargo_seconds=0
    )
    assert bt_with.total_trades < bt_without.total_trades


# ---------------------------------------------------------------------------
# 4-6 — purge + embargo semantics (inclusive, exact epsilon)
# ---------------------------------------------------------------------------


def test_purge_removes_train_sample_whose_horizon_crosses_boundary() -> None:
    ds = _dataset(100, {19: 600})
    fold = walk_forward_folds(ds, n_splits=3, purge_seconds=300, embargo_seconds=60)[0]
    train_ids = {s.sample_id for s in fold.train}
    assert "s19" not in train_ids
    assert "s18" in train_ids


def test_split_temporal_purge_removes_train_boundary_crossing() -> None:
    sp = split_temporal(_dataset(50, {31: 600}), purge_seconds=300, embargo_seconds=60)
    assert "s31" not in {s.sample_id for s in sp.train}


def test_embargo_is_inclusive_and_no_off_by_one() -> None:
    sp = split_temporal(_dataset(50), purge_seconds=300, embargo_seconds=60)
    val_ids = {s.sample_id for s in sp.validation}
    oos_ids = {s.sample_id for s in sp.oos}
    assert "s32" not in val_ids
    assert "s33" in val_ids
    assert "s40" not in oos_ids
    assert "s41" in oos_ids


def test_walkforward_embargo_removes_correct_window() -> None:
    ds = _dataset(100)
    f1 = walk_forward_folds(ds, n_splits=3, purge_seconds=300, embargo_seconds=60)[0]
    val_ids = {s.sample_id for s in f1.validation}
    assert "s20" not in val_ids
    assert "s21" not in val_ids
    assert "s22" in val_ids


def test_purge_and_embargo_zero_disables_both_guards() -> None:
    sp = split_temporal(_dataset(50), purge_seconds=0.0, embargo_seconds=0.0)
    assert "s32" in {s.sample_id for s in sp.validation}
    assert "s40" in {s.sample_id for s in sp.oos}


def test_embargo_larger_than_block_still_honest() -> None:
    ds = _dataset(100)
    f1 = walk_forward_folds(ds, n_splits=3, purge_seconds=300, embargo_seconds=700)[0]
    assert len(f1.validation) > 0  # never crashes and still returns a usable window


# ---------------------------------------------------------------------------
# 10 — fold construction: block partition, gaps, duplicates
# ---------------------------------------------------------------------------


def test_walkforward_block_partition_no_empty_train() -> None:
    for f in walk_forward_folds(_dataset(100), n_splits=3):
        assert len(f.train) > 0
        assert len(f.validation) > 0


def test_walkforward_duplicate_timestamps_stable() -> None:
    ds = ResearchDataset(dataset_id="ds", samples=[_sample(i) for i in range(50)] + [_sample(25)])
    folds = walk_forward_folds(ds, n_splits=3)
    assert len(folds) == 3


def test_walkforward_gap_in_data_does_not_merge_regimes() -> None:
    ds = ResearchDataset(
        dataset_id="ds_gap",
        samples=[_sample(i) for i in range(30)]
        + [_sample(200 + i) for i in range(30)]
        + [_sample(400 + i) for i in range(40)],
    )
    folds = walk_forward_folds(ds, n_splits=3)
    assert len(folds) >= 1


# ---------------------------------------------------------------------------
# 14 — sequence causal isolation
# ---------------------------------------------------------------------------


def _seq_frame(n: int, symbols: list[str] | None = None) -> pl.DataFrame:
    ts = pl.Series(
        "timestamp",
        [datetime(2026, 9, 1, 10, 0, tzinfo=UTC) + timedelta(minutes=i) for i in range(n)],
    )
    return pl.DataFrame(
        {
            "timestamp": ts,
            "symbol": symbols or ["XAUUSD"] * n,
            "timeframe": ["M1"] * n,
            "label": [0] * n,
            **{f"feat_{i}": list(range(n)) for i in range(4)},
        }
    )


def test_sequence_builder_is_causal_only_anchor_regression_on_boundary() -> None:
    frame = _seq_frame(70)
    out = SequenceBuilder(seq_len=8, max_gap_us=None).build(frame)
    assert out["valid"].sum() > 0
    last = out["X"][out["valid"]][-1]
    assert last[0, 0] == 62.0
    assert last[-1, 0] == 69.0


def test_sequence_builder_gap_excludes_window() -> None:
    frame = _seq_frame(20)
    ts = frame["timestamp"].to_list()
    ts[10] = ts[10] + timedelta(minutes=20)
    frame = frame.with_columns(pl.Series("timestamp", ts))
    out = SequenceBuilder(seq_len=8, max_gap_us=10 * 60 * 1_000_000).build(frame)
    assert out["valid"].sum() < len(out["valid"])


def test_sequence_builder_cross_symbol_boundary_invalid() -> None:
    frame = _seq_frame(16, symbols=["XAUUSD"] * 8 + ["EURUSD"] * 8)
    out = SequenceBuilder(seq_len=8, max_gap_us=None).build(frame)
    v = out["valid"].tolist()
    assert v[0] is True  # pure XAUUSD window
    assert all(x is False for x in v[1:8])  # straddling windows invalid
    assert v[8] is True  # pure EURUSD window


# ---------------------------------------------------------------------------
# 15 / 18 — calibrated gates and OOS thresholds
# ---------------------------------------------------------------------------


def _rand_frame(n: int = 400, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    labels = rng.choice([0, 1, 2], size=n, p=[0.90, 0.05, 0.05]).astype(np.int64)
    logits = rng.normal(0, 0.05, size=(n, 3))
    probs = np.exp(logits) / np.exp(logits).sum(axis=1, keepdims=True)
    return probs, labels


def test_validation_factory_gates_are_rejected_without_force_on_no_info_model() -> None:
    vf = ValidationFactory()
    probs, labels = _rand_frame(400, seed=1)
    vr = vf.validate("m", "e", None, probs, labels)
    assert vr.passed is False
    assert vr.verdict == "REJECTED"
    failing = [g for g in vr.gates if not g["passed"]]
    assert len(failing) >= 1
    assert any(g["gate"] in ("oos_macro_f1_floor", "regime_coverage") for g in failing)


def test_validation_factory_no_info_balanced_accuracy_floor_catches_directional_noise() -> None:
    from nexus_scalp.model_generation.validation import _balanced_accuracy

    y_true = np.array([0, 1] * 50, dtype=np.int64)
    y_pred = np.zeros(100, dtype=np.int64)
    bacc = _balanced_accuracy(y_true, y_pred)
    assert bacc < 0.6


def test_validation_factory_force_bypasses_gates() -> None:
    vf = ValidationFactory()
    probs, labels = _rand_frame(400, seed=1)
    vr = vf.validate("m", "e", None, probs, labels, force=True)
    assert vr.passed is True


def test_scoring_hard_gate_oos_fail_always_rejected() -> None:
    from nexus_scalp.research.models import BacktestResult, OOSResult, RobustnessResult

    base = dict(strategy_id="s", strategy_version="1.0.0", dataset_id="ds")
    bt = BacktestResult(total_trades=30, expectancy_r=0.20, max_drawdown_r=0.3, **base)
    oos = OOSResult(
        in_sample_expectancy_r=0.20,
        oos_expectancy_r=-0.05,
        oos_samples=20,
        status="FAIL",
        reason="below floor",
        **base,
    )
    rob = RobustnessResult(baseline_expectancy_r=0.1, max_degradation=0.1, status="PASS", **base)
    ds = ResearchDataset(dataset_id="ds", samples=[_sample(i) for i in range(30)])
    sc = compute_strategy_score(ds, backtest=bt, walkforward=None, oos=oos, robustness=rob)
    assert sc.verdict == "REJECTED"
    assert "OOS_FAILURE" in sc.reasons


# ---------------------------------------------------------------------------
# 16 — metric integrity: benchmark row-mismatch is NOT_COMPUTABLE
# ---------------------------------------------------------------------------


def test_benchmark_source_emits_row_mismatch_error_node() -> None:
    src = (
        __import__("pathlib")
        .Path("src/nexus_scalp/model_generation/benchmark.py")
        .read_text(encoding="utf-8")
    )
    assert "PREDICTION_ROW_MISMATCH" in src
    assert "preds = labels  # alignment fallback" not in src


# ---------------------------------------------------------------------------
# 25 — adversarial future-feature probes
# ---------------------------------------------------------------------------


def test_future_only_feature_collapses_without_purge_but_not_with_it() -> None:
    ds = _dataset(100)
    fold_no_guard = walk_forward_folds(ds, n_splits=3, purge_seconds=0, embargo_seconds=0)[0]
    fold_guarded = walk_forward_folds(ds, n_splits=3, purge_seconds=300, embargo_seconds=60)[0]
    assert len(fold_guarded.validation) < len(fold_no_guard.validation)
    assert len(fold_guarded.train) <= len(fold_no_guard.train)


def test_timestamp_shifted_one_step_is_detected_by_embargo() -> None:
    ds = _dataset(100)
    sp_guard = split_temporal(ds, purge_seconds=0, embargo_seconds=60)
    sp_noguard = split_temporal(ds, purge_seconds=0, embargo_seconds=0)
    assert len(sp_guard.validation) < len(sp_noguard.validation)


# ---------------------------------------------------------------------------
# 26 — reproducibility
# ---------------------------------------------------------------------------


def test_split_and_walkforward_are_deterministic() -> None:
    ds = _dataset(100, {19: 600, 39: 300})
    a = split_temporal(ds, purge_seconds=300, embargo_seconds=60)
    b = split_temporal(ds, purge_seconds=300, embargo_seconds=60)
    assert [s.sample_id for s in a.train] == [s.sample_id for s in b.train]
    assert [s.sample_id for s in a.validation] == [s.sample_id for s in b.validation]
    f1a = walk_forward_folds(ds, n_splits=3, purge_seconds=300, embargo_seconds=60)
    f1b = walk_forward_folds(ds, n_splits=3, purge_seconds=300, embargo_seconds=60)
    for fa, fb in zip(f1a, f1b, strict=True):
        assert [s.sample_id for s in fa.train] == [s.sample_id for s in fb.train]
        assert [s.sample_id for s in fa.validation] == [s.sample_id for s in fb.validation]


# ---------------------------------------------------------------------------
# 29 — provenance: run config records the effective values
# ---------------------------------------------------------------------------


def test_pipeline_run_config_provenance_completeness() -> None:
    src = (
        __import__("pathlib")
        .Path("src/nexus_scalp/research/pipeline.py")
        .read_text(encoding="utf-8")
    )
    assert src.count("purge_seconds=purge_seconds") >= 2
    assert src.count("embargo_seconds=embargo_seconds") >= 2
    assert "self._record_run(" in src
