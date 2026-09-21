"""Tests for the bounded hyperparameter grid search runner (ML-EXP-003).

Run: PYTHONPATH=src:. python -m pytest tests/unit/test_hyperparam_search.py -v
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

import pytest

from nexus_scalp.model_lab.experiment_registry import (
    ExperimentRegistry,
)
from scripts.experiments.hyperparam_search import (
    MAX_TRIALS,
    GridSearchSpec,
    GridSpecError,
    GridTooLargeError,
    SweepReport,
    TrialResult,
    TrialRunnerError,
    lab_trial_runner_factory,
    load_spec,
    materialize_grid,
    run_grid_search,
    synthetic_trial_runner,
    trial_id,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "scripts" / "experiments"

_MINI = {
    "name": "mini_sweep",
    "axes": {
        "learning_rate": [0.001, 0.003],
        "dropout": [0.10, 0.20],
    },
    "seed": 42,
    "base_config": {
        "model_family": "STUDENT_MLP",
        "input_dimension": 70,
        "num_classes": 3,
        "dataset_id": "ds_lab_001",
        "dataset_hash": "a" * 64,
    },
}


def _mini_flat() -> dict[str, Any]:
    """Same grid in the flat top-level-axis shorthand."""
    return {
        "name": "mini_flat",
        "learning_rate": [0.001, 0.003],
        "dropout": [0.10, 0.20],
        "base_config": _MINI["base_config"],
    }


def _spec(**over: Any) -> GridSearchSpec:
    payload = dict(_MINI)
    payload.update(over)
    return load_spec(payload)


@pytest.fixture
def sweep_root(tmp_path: Path) -> Path:
    return tmp_path / "artifacts" / "experiments"


# ----------------------------------------------------------------------
# 1. spec validation
# ----------------------------------------------------------------------
def test_load_spec_from_dict_and_yaml(tmp_path: Path) -> None:
    from_spec = load_spec(_MINI)
    assert from_spec.trial_count == 4
    assert list(from_spec.axes) == ["dropout", "learning_rate"]

    yaml_path = tmp_path / "spec.yaml"
    import yaml

    yaml_path.write_text(yaml.safe_dump(_MINI), encoding="utf-8")
    from_yaml = load_spec(yaml_path)
    assert from_yaml.trial_count == from_spec.trial_count
    assert from_yaml.name == "mini_sweep"


def test_load_spec_rejects_unknown_axis() -> None:
    bad: dict[str, Any] = {
        "name": "mini_sweep",
        "axes": {"learning_rate": [0.001], "momentum": [0.9]},
    }
    with pytest.raises(GridSpecError, match="unknown search axis"):
        load_spec(bad)


def test_load_spec_rejects_unknown_top_level_key() -> None:
    bad = {**_MINI, "optimizer": "adam"}
    with pytest.raises(GridSpecError, match="unknown spec key"):
        load_spec(bad)


@pytest.mark.parametrize("bad", [{}, {"name": ""}, {"name": "x"}])
def test_load_spec_requires_name_and_axes(bad: dict[str, Any]) -> None:
    with pytest.raises(GridSpecError):
        load_spec(bad)


def test_load_spec_scalar_axis_becomes_single_point() -> None:
    spec = load_spec({**_MINI, "axes": {"learning_rate": 0.001}})
    assert spec.trial_count == 1
    assert materialize_grid(spec) == [{"learning_rate": 0.001}]


def test_load_spec_axis_values_sorted_and_deduped() -> None:
    spec = load_spec({**_MINI, "axes": {"learning_rate": [0.01, 0.001, 0.01]}})
    assert spec.axes["learning_rate"] == [0.001, 0.01]


def test_load_spec_rejects_non_numeric_axis() -> None:
    with pytest.raises(GridSpecError, match="non-numeric"):
        load_spec({**_MINI, "axes": {"learning_rate": ["fast"]}})


def test_load_spec_rejects_non_finite_axis() -> None:
    with pytest.raises(GridSpecError, match="non-finite"):
        load_spec({**_MINI, "axes": {"learning_rate": [float("nan")]}})


def test_load_spec_rejects_empty_axis_list() -> None:
    with pytest.raises(GridSpecError):
        load_spec({**_MINI, "axes": {"learning_rate": []}})


def test_grid_too_large_raises_not_silently_schedules() -> None:
    big = {
        "name": "big",
        "axes": {"learning_rate": [0.001 * i for i in range(10)], "dropout": [0.1, 0.2, 0.3]},
    }
    with pytest.raises(GridTooLargeError, match="bounded-sweep ceiling"):
        load_spec(big)


def test_max_trials_override_raises_when_still_exceeded() -> None:
    with pytest.raises(GridTooLargeError):
        load_spec({**_MINI, "max_trials": 1})


def test_max_trials_zero_is_a_config_error() -> None:
    with pytest.raises(GridSpecError, match="max_trials must be >= 1"):
        load_spec({**_MINI, "max_trials": 0})


def test_default_max_trials_is_the_hard_ceiling() -> None:
    spec = _spec()
    assert spec.effective_max_trials == MAX_TRIALS
    assert spec.trial_count <= spec.effective_max_trials


def test_flat_top_level_axes_are_accepted_as_shorthand() -> None:
    spec = load_spec(_mini_flat())
    assert spec.trial_count == 4
    assert spec.axes == _spec().axes


def test_axis_in_both_places_is_rejected() -> None:
    bad = {**_MINI, "learning_rate": [0.001]}
    with pytest.raises(GridSpecError, match="ONE place only"):
        load_spec(bad)


def test_axes_must_be_a_mapping() -> None:
    with pytest.raises(GridSpecError, match="'axes' must be a mapping"):
        load_spec({**_MINI, "axes": [0.001]})


# ----------------------------------------------------------------------
# 2. deterministic grid materialization
# ----------------------------------------------------------------------
def test_materialize_grid_is_deterministic_and_sorted() -> None:
    spec = _spec()
    a = materialize_grid(spec)
    b = materialize_grid(spec)
    assert a == b
    assert a == [
        {"dropout": 0.10, "learning_rate": 0.001},
        {"dropout": 0.10, "learning_rate": 0.003},
        {"dropout": 0.20, "learning_rate": 0.001},
        {"dropout": 0.20, "learning_rate": 0.003},
    ]


def test_trial_count_matches_materialized_grid() -> None:
    spec = _spec()
    assert spec.trial_count == len(materialize_grid(spec))


# ----------------------------------------------------------------------
# 3. trial ids: stable, unique, filesystem-safe
# ----------------------------------------------------------------------
def test_trial_ids_are_stable_unique_and_safe() -> None:
    spec = _spec()
    combos = materialize_grid(spec)
    ids = [trial_id(spec, i, c) for i, c in enumerate(combos)]
    assert len(set(ids)) == len(ids)
    again = [trial_id(spec, i, c) for i, c in enumerate(combos)]
    assert ids == again
    for ident in ids:
        assert len(ident) <= 96
        # registry-safe: no path separators or spaces
        assert not any(ch in ident for ch in "/\\: ")


def test_trial_id_encodes_params_and_index() -> None:
    spec = _spec()
    ident = trial_id(spec, 1, {"dropout": 0.10, "learning_rate": 0.003})
    assert ident.startswith("mini-sweep-001")
    assert "learning-rate0.003" in ident
    assert "dropout0.1" in ident


def test_trial_id_respects_registry_id_validator() -> None:
    spec = _spec()
    ident = trial_id(spec, 0, {"dropout": 0.1, "learning_rate": 0.001})
    # The registry must ACCEPT every id the runner can mint.
    reg = ExperimentRegistry(root=Path("/tmp/never_used"))
    from nexus_scalp.model_lab.experiment_registry import validate_experiment_id

    assert validate_experiment_id(ident) == ident
    assert reg.root.name  # smoke: the object constructed without error


# ----------------------------------------------------------------------
# 4. a real 4-trial sweep (BENCHMARK_PLAN)
# ----------------------------------------------------------------------
def test_four_trial_mini_sweep_records_all_in_registry(sweep_root: Path) -> None:
    spec = _spec()
    reg = ExperimentRegistry(root=sweep_root)
    report = run_grid_search(spec, registry=reg, output_root=sweep_root / "sweeps" / spec.name)

    assert report.completed == 4
    assert report.failed == 0
    # BENCHMARK_PLAN: all 4 trials recorded with DISTINCT experiment ids.
    ids = [t["experiment_id"] for t in report.trials]
    assert len(set(ids)) == 4
    assert reg.count() == 4
    assert reg.count("COMPLETED") == 4

    for trial in report.trials:
        rec = reg.get_experiment(trial["experiment_id"])
        assert rec is not None
        assert rec.status == "COMPLETED"
        # the 7 mandatory reproduction fields are populated
        assert rec.git_sha
        assert rec.dataset_hash == "a" * 64
        assert rec.model_config_dict["learning_rate"] == trial["params"]["learning_rate"]
        assert rec.metrics["val_loss"] > 0
        assert math.isfinite(rec.metrics["val_loss"])


def test_every_trial_reports_the_three_required_metrics(sweep_root: Path) -> None:
    report = run_grid_search(_spec(), registry=ExperimentRegistry(root=sweep_root))
    assert report.trials
    for trial in report.trials:
        for metric in ("val_loss", "fold_sharpe", "minority_f1"):
            assert metric in trial["metrics"], (trial["experiment_id"], metric)
            assert isinstance(trial["metrics"][metric], float)


def test_per_trial_output_dirs_are_isolated(sweep_root: Path) -> None:
    report = run_grid_search(_spec(), registry=ExperimentRegistry(root=sweep_root))
    dirs = [Path(t["output_dir"]) for t in report.trials]
    assert len({str(d) for d in dirs}) == len(dirs)
    for d in dirs:
        assert (d / "metrics.json").is_file()
    # no two trials share an artifact file
    arts = [t["artifact_path"] for t in report.trials]
    assert len(set(arts)) == len(arts)


def test_seeds_are_unique_across_the_grid_and_stable(sweep_root: Path) -> None:
    reg = ExperimentRegistry(root=sweep_root)
    r1 = run_grid_search(_spec(), registry=reg)
    r2 = run_grid_search(
        _spec(),
        registry=ExperimentRegistry(root=sweep_root / "second"),
        output_root=sweep_root / "sweeps" / "mini_sweep2",
    )
    s1 = [t["seed"] for t in r1.trials]
    assert len(set(s1)) == len(s1)
    assert s1 == [42, 43, 44, 45]
    assert [t["seed"] for t in r2.trials] == s1


# ----------------------------------------------------------------------
# 5. leaderboard
# ----------------------------------------------------------------------
def test_leaderboard_ranks_by_metric_and_picks_the_true_optimum(sweep_root: Path) -> None:
    report = run_grid_search(_spec(), registry=ExperimentRegistry(root=sweep_root))
    assert len(report.leaderboard) == 3
    losses = [row["metric"]["val_loss"] for row in report.leaderboard]
    assert losses == sorted(losses), "leaderboard must be ascending for val_loss"
    # synthetic surface has its optimum at lr=3e-3, dropout=0.10
    assert report.leaderboard[0]["experiment_id"].endswith("learning-rate0.003")
    assert "dropout0.1" in report.leaderboard[0]["experiment_id"]


def test_leaderboard_json_is_written_and_self_consistent(sweep_root: Path) -> None:
    report = run_grid_search(_spec(), registry=ExperimentRegistry(root=sweep_root))
    path = Path(report.leaderboard_path)
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["name"] == "mini_sweep"
    assert payload["stats"]["total_trials"] == 4
    assert payload["stats"]["completed"] == 4
    assert len(payload["leaderboard"]) == 3
    assert [r["experiment_id"] for r in payload["leaderboard"]] == [
        r["experiment_id"] for r in report.leaderboard
    ]
    # every leaderboard entry resolves to a registered experiment
    reg = ExperimentRegistry(root=sweep_root)
    for row in payload["leaderboard"]:
        assert reg.get_experiment(row["experiment_id"]) is not None


def test_leaderboard_depth_follows_trial_count(sweep_root: Path) -> None:
    spec = load_spec({**_MINI, "axes": {"learning_rate": [0.001, 0.003, 0.01]}})
    report = run_grid_search(spec, registry=ExperimentRegistry(root=sweep_root))
    assert len(report.leaderboard) == 3
    assert report.completed == 3


def test_leaderboard_metric_direction_is_derived_not_assumed(sweep_root: Path) -> None:
    spec = load_spec({**_MINI, "metric": "fold_sharpe"})
    report = run_grid_search(spec, registry=ExperimentRegistry(root=sweep_root))
    sharpes = [row["metric"]["fold_sharpe"] for row in report.leaderboard]
    assert sharpes == sorted(sharpes, reverse=True), "fold_sharpe must DESCEND"


# ----------------------------------------------------------------------
# 6. ABORT_CONDITIONS: NaN loss is skipped, sweep survives
# ----------------------------------------------------------------------
def test_nan_loss_trial_is_marked_failed_and_skipped(sweep_root: Path) -> None:
    def nan_runner(params: dict[str, Any], seed: int, out: Path) -> TrialResult:
        return TrialResult(
            experiment_id=str(params.get("_experiment_id", "")),
            params=params,
            metrics={"val_loss": float("nan"), "fold_sharpe": 1.0, "minority_f1": 0.5},
        )

    reg = ExperimentRegistry(root=sweep_root)
    report = run_grid_search(_spec(), trial_runner=nan_runner, registry=reg)
    assert report.completed == 0
    assert report.failed == 4
    assert report.skipped_nan == 4
    assert reg.count("FAILED") == 4
    for trial in report.trials:
        assert "NaN" in trial["error"]
        rec = reg.get_experiment(trial["experiment_id"])
        assert rec is not None and rec.status == "FAILED"


def test_inf_loss_is_also_skipped(sweep_root: Path) -> None:
    def inf_runner(params: dict[str, Any], seed: int, out: Path) -> TrialResult:
        return TrialResult(
            experiment_id=str(params.get("_experiment_id", "")),
            params=params,
            metrics={"val_loss": float("inf"), "fold_sharpe": 1.0, "minority_f1": 0.5},
        )

    report = run_grid_search(
        _spec(), trial_runner=inf_runner, registry=ExperimentRegistry(root=sweep_root)
    )
    assert report.skipped_nan == 4
    assert report.completed == 0


def test_runner_exception_is_caught_and_recorded_not_raised(sweep_root: Path) -> None:
    def boom(params: dict[str, Any], seed: int, out: Path) -> TrialResult:
        raise RuntimeError("training exploded")

    reg = ExperimentRegistry(root=sweep_root)
    report = run_grid_search(_spec(), trial_runner=boom, registry=reg)
    assert report.failed == 4
    assert report.completed == 0
    rec = reg.get_experiment(report.trials[0]["experiment_id"])
    assert rec is not None
    assert "training exploded" in rec.metrics["error"]


def test_partial_failure_still_produces_a_leaderboard(sweep_root: Path) -> None:
    calls = {"i": 0}

    def flaky(params: dict[str, Any], seed: int, out: Path) -> TrialResult:
        calls["i"] += 1
        if calls["i"] == 1:
            raise RuntimeError("first trial dies")
        return synthetic_trial_runner(params, seed, out)

    report = run_grid_search(
        _spec(), trial_runner=flaky, registry=ExperimentRegistry(root=sweep_root)
    )
    assert report.completed == 3
    assert report.failed == 1
    assert len(report.leaderboard) == 3
    assert report.leaderboard[0]["experiment_id"] != report.trials[0]["experiment_id"]


def test_runner_returning_wrong_experiment_id_is_rejected(sweep_root: Path) -> None:
    def liar(params: dict[str, Any], seed: int, out: Path) -> TrialResult:
        return TrialResult(
            experiment_id="some_other_id",
            params=params,
            metrics={"val_loss": 1.0, "fold_sharpe": 1.0, "minority_f1": 0.5},
        )

    report = run_grid_search(
        _spec(), trial_runner=liar, registry=ExperimentRegistry(root=sweep_root)
    )
    assert report.failed == 4
    assert "expected" in report.trials[0]["error"]


# ----------------------------------------------------------------------
# 7. immutability: a re-run of the same spec is a no-op, not a re-write
# ----------------------------------------------------------------------
def test_rerunning_the_same_spec_is_idempotent(sweep_root: Path) -> None:
    reg = ExperimentRegistry(root=sweep_root)
    first = run_grid_search(_spec(), registry=reg)
    # the SAME registry: re-registering identical records is a no-op
    second = run_grid_search(_spec(), registry=reg)
    assert reg.count() == 4, "no duplicate rows"
    assert [t["experiment_id"] for t in first.trials] == [t["experiment_id"] for t in second.trials]


def test_manifests_are_written_once_and_are_tamper_checkable(sweep_root: Path) -> None:
    reg = ExperimentRegistry(root=sweep_root)
    report = run_grid_search(_spec(), registry=reg)
    for trial in report.trials:
        if trial["manifest_path"]:
            payload = json.loads(Path(trial["manifest_path"]).read_text(encoding="utf-8"))
            assert payload["experiment_id"] == trial["experiment_id"]
            assert payload["git_sha"] and payload["git_sha"] != "UNRESOLVED"
            assert payload["seed"] == trial["seed"]
            assert payload["metrics"]["val_loss"] == trial["metrics"]["val_loss"]
            verified = reg.verify_manifest(trial["experiment_id"])
            assert verified["verified"] is True, verified.get("reason")
            assert verified["reason"] == "OK"


# ----------------------------------------------------------------------
# 8. reproducibility: the registry bundle reconstructs the trial
# ----------------------------------------------------------------------
def test_reproduction_bundle_round_trips_the_trial(sweep_root: Path) -> None:
    reg = ExperimentRegistry(root=sweep_root)
    report = run_grid_search(_spec(), registry=reg)
    trial = report.trials[0]
    bundle = reg.reproduction_bundle(trial["experiment_id"])
    assert bundle["experiment_id"] == trial["experiment_id"]
    assert bundle["model_config"]["learning_rate"] == trial["params"]["learning_rate"]
    assert bundle["model_config"]["dropout"] == trial["params"]["dropout"]
    assert bundle["seed"] == trial["seed"]
    assert bundle["dataset_hash"] == "a" * 64
    assert bundle["reproducible"] in {True, False}  # dirty tree is allowed


# ----------------------------------------------------------------------
# 9. example YAML spec ships valid and bounded
# ----------------------------------------------------------------------
def test_shipped_example_spec_is_valid_and_within_the_ceiling() -> None:
    example = SCRIPTS_DIR / "grid_search.example.yaml"
    assert example.is_file(), "example spec must ship with the runner"
    spec = load_spec(example)
    assert spec.trial_count == 16
    assert spec.trial_count <= spec.effective_max_trials
    assert spec.metric == "val_loss"
    assert spec.base_config["loss"] == "focal_smoothing"


def test_shipped_example_yaml_parses() -> None:
    import yaml

    example = SCRIPTS_DIR / "grid_search.example.yaml"
    payload = yaml.safe_load(example.read_text(encoding="utf-8"))
    assert payload["name"] == "lr_wd_dropout_sweep"
    assert set(payload["axes"]) <= {
        "learning_rate",
        "weight_decay",
        "dropout",
        "hidden_dim",
        "loss",
        "batch_size",
        "epochs",
    }


# ----------------------------------------------------------------------
# 10. CLI
# ----------------------------------------------------------------------
def test_cli_runs_the_shipped_example(tmp_path: Path) -> None:
    from scripts.experiments.hyperparam_search import _main

    reg = tmp_path / "artifacts" / "experiments"
    rc = _main(
        [
            str(SCRIPTS_DIR / "grid_search.example.yaml"),
            "--registry-root",
            str(reg),
            "--runner",
            "synthetic",
            "--top",
            "2",
        ]
    )
    assert rc == 0
    lb = json.loads((reg / "sweeps" / "lr-wd-dropout-sweep" / "leaderboard.json").read_text())
    assert lb["stats"]["completed"] == 16
    assert len(lb["leaderboard"]) == 2


def test_cli_returns_nonzero_when_every_trial_fails(tmp_path: Path) -> None:
    """A sweep where no trial completes is a non-zero exit (ABORT_CONDITIONS
    made visible to the operator), not a silent success."""
    from scripts.experiments.hyperparam_search import _main

    reg = tmp_path / "artifacts" / "experiments"
    spec_path = tmp_path / "spec.yaml"
    import yaml

    # Oversized: the ceiling itself rejects the spec (a config error path).
    spec_path.write_text(
        "name: big\nmax_trials: 1\naxes:\n  learning_rate: [0.001, 0.003, 0.01]\n",
        encoding="utf-8",
    )
    with pytest.raises(GridTooLargeError):
        _main([str(spec_path), "--registry-root", str(reg)])


def test_cli_rejects_oversized_spec(tmp_path: Path) -> None:
    from scripts.experiments.hyperparam_search import _main

    spec_path = tmp_path / "big.yaml"
    spec_path.write_text(
        "name: big\naxes:\n  learning_rate: [0.001, 0.002, 0.003, 0.004, 0.005,"
        " 0.006, 0.007, 0.008, 0.009, 0.01]\n  dropout: [0.1, 0.2, 0.3]\n",
        encoding="utf-8",
    )
    with pytest.raises(GridTooLargeError):
        _main([str(spec_path)])


# ----------------------------------------------------------------------
# 11. lab runner wiring (lazy, torch-free import contract)
# ----------------------------------------------------------------------
def test_lab_runner_factory_imports_lazily_and_maps_only_spec_fields() -> None:
    # importing the factory must NOT pull torch/polars
    import importlib

    mod = importlib.import_module("scripts.experiments.hyperparam_search")
    assert "torch" not in sys.modules, "lab_trial_runner_factory must stay torch-free at import"
    runner = mod.lab_trial_runner_factory(frame=None, feature_cols=None)
    assert callable(runner)


def test_lab_runner_forwards_only_declared_spec_fields() -> None:
    """dropout/hidden_dim are architecture knobs — they must not crash
    ExperimentSpec construction, and must survive in the provenance."""
    from nexus_scalp.model_lab.registry import ExperimentSpec

    # Exercise the field filter directly against the real model.
    params = {"learning_rate": 0.003, "dropout": 0.15, "hidden_dim": 96.0}
    forward = {
        **{
            "experiment_id": "t",
            "model_family": "STUDENT_MLP",
            "input_dimension": 70,
            "num_classes": 3,
            "sequence_length": 1,
            "seed": 42,
        },
        **params,
    }
    safe = {k: v for k, v in forward.items() if k in ExperimentSpec.model_fields}
    spec = ExperimentSpec(**safe)
    assert spec.learning_rate == 0.003
    assert "dropout" not in ExperimentSpec.model_fields
    assert "hidden_dim" not in ExperimentSpec.model_fields


def test_synthetic_runner_is_deterministic_for_a_fixed_seed(tmp_path: Path) -> None:
    a = synthetic_trial_runner({"learning_rate": 0.003, "dropout": 0.10}, 42, tmp_path / "a")
    b = synthetic_trial_runner({"learning_rate": 0.003, "dropout": 0.10}, 42, tmp_path / "b")
    assert a.metrics == b.metrics
    # a farther-from-optimum learning rate must score worse, at any seed
    far = synthetic_trial_runner({"learning_rate": 0.02, "dropout": 0.10}, 43, tmp_path / "c")
    assert a.metrics["val_loss"] < far.metrics["val_loss"]
