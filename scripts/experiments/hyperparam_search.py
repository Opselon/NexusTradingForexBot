"""Bounded hyperparameter grid search runner (ML-EXP-003).

Controlled, reproducible sweeps — NOT unconstrained random search or
Bayesian optimization (NON_GOALS). What this module guarantees:

  * a Cartesian grid built from a YAML spec, materialized in a FIXED
    deterministic order (sorted keys) so two runs of the same spec
    produce the same trial sequence;
  * a hard ceiling on trial count (``MAX_TRIALS``) — an over-specified
    grid is a *configuration error* here, not a slow weekend;
  * every trial is registered PENDING in :class:`ExperimentRegistry`
    BEFORE its work starts, then finalized with metrics
    (``val_loss`` / ``fold_sharpe`` / ``minority_f1``) or marked FAILED
    — the registry is write-once, so a crash mid-sweep leaves a
    permanent, queryable record of what was attempted;
  * a NaN-loss trial is logged and SKIPPED (ABORT_CONDITIONS), never
    allowed to poison the leaderboard;
  * per-trial isolated output directories — no checkpoint overwrites
    between trials (INVESTIGATION_PLAN);
  * a leaderboard JSON written atomically at the end.

Separation of concerns: this module owns the *search* (grid, bounds,
registry, leaderboard). It does NOT own training — a ``trial_runner``
callable is injected. The default runner
:func:`synthetic_trial_runner` is a deterministic, torch-free
evaluator used by the tests and by smoke sweeps; production callers
pass a real training function (see :func:`lab_trial_runner`).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.model_lab.experiment_registry import (
    DEFAULT_ROOT,
    ExperimentRecord,
    ExperimentRegistry,
    capture_git_revision,
)

#: Hard ceiling on trial count. An over-specified grid raises
#: ``GridTooLargeError`` instead of silently scheduling a week of work.
MAX_TRIALS = 20

#: Search-space keys this runner understands. Anything else in the YAML
#: is rejected rather than silently ignored.
KNOWN_AXES = frozenset(
    {
        "learning_rate",
        "weight_decay",
        "dropout",
        "hidden_dim",
        "batch_size",
        "epochs",
    }
)

#: Categorical axes (non-numeric enum-like values, e.g. loss name).
CATEGORICAL_AXES = frozenset({"loss"})

#: Metrics a trial must report. ``val_loss`` drives the leaderboard,
#: ``fold_sharpe`` / ``minority_f1`` ride along for context.
REQUIRED_METRICS = ("val_loss", "fold_sharpe", "minority_f1")

_TRIAL_ID_RE = re.compile(r"[^a-z0-9-]+")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _slugify(text: str) -> str:
    """Filesystem/registry-safe token: underscores become dashes so a
    composite axis name (``learning_rate``) reads as one id token
    (``learning-rate0.003``), never as two ambiguous fields."""
    return _TRIAL_ID_RE.sub("-", text.strip().lower()).strip("-") or "trial"


def _as_value_list(raw: Any, axis: str) -> list[Any]:
    """Coerce one axis of a YAML spec to a sorted, de-duplicated list.

    Numeric axes (``learning_rate``, ``weight_decay``, ``dropout``,
    ``hidden_dim``, ``batch_size``, ``epochs``) become sorted floats;
    ``learning_rate: 0.001`` (scalar) becomes a single-point axis.
    Categorical axes (:data:`CATEGORICAL_AXES`, e.g. ``loss``) keep their
    string values, sorted for determinism.

    A typo'd ``learning_rate: 0.001,`` in YAML must never become a silent
    1-point axis, so a non-numeric value on a numeric axis raises.
    """
    if axis in CATEGORICAL_AXES:
        values: list[Any]
        if isinstance(raw, str):
            values = [raw.strip()]
        elif isinstance(raw, (list, tuple)) and raw:
            for item in raw:
                if not isinstance(item, str):
                    raise GridSpecError(
                        f"axis {axis!r} is categorical and must hold strings, got {item!r}"
                    )
            values = [str(item).strip() for item in raw]
        else:
            raise GridSpecError(
                f"axis {axis!r} must be a string or a non-empty list of strings, "
                f"got {type(raw).__name__}"
            )
        values = [v for v in values if v]
        if not values:
            raise GridSpecError(f"axis {axis!r} has no non-empty values")
        return sorted(set(values))

    if isinstance(raw, bool) or not isinstance(raw, (int, float, list, tuple)):
        raise GridSpecError(
            f"axis {axis!r} must be a number or a non-empty list of numbers, "
            f"got {type(raw).__name__}"
        )
    if isinstance(raw, (int, float)):
        nums = [float(raw)]
    else:
        if not raw:
            raise GridSpecError(f"axis {axis!r} is an empty list")
        nums = []
        for item in raw:
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                raise GridSpecError(
                    f"axis {axis!r} contains a non-numeric value {item!r}; "
                    "every grid point must be a number"
                )
            nums.append(float(item))
    for num in nums:
        if not math.isfinite(num):
            raise GridSpecError(f"axis {axis!r} contains a non-finite value {num!r}")
    return sorted(set(nums))


@dataclass(frozen=True)
class TrialResult:
    """Outcome of one grid trial as returned by a trial_runner."""

    experiment_id: str
    params: dict[str, Any]
    metrics: dict[str, Any]
    artifact_path: str = ""
    error: str = ""

    @property
    def failed(self) -> bool:
        return bool(self.error)


@dataclass
class GridSearchSpec:
    """Validated search spec (see ``scripts/experiments/grid_search.example.yaml``).

    Attributes:
        name: Sweep name; becomes the experiment_id prefix.
        axes: Ordered axis name -> sorted candidate values.
        seed: Base seed; trial ``i`` uses ``seed + i`` so seeds are unique
            across the grid and stable between runs of the same spec.
        base_config: Frozen model/runner config merged into every trial's
            recorded ``model_config`` (architecture, dataset id, loss...).
        max_trials: Ceiling; ``None`` means :data:`MAX_TRIALS`.
        metric: Leaderboard ranking metric (default ``val_loss``).
    """

    name: str
    axes: dict[str, list[Any]] = field(default_factory=dict)
    seed: int = 42
    base_config: dict[str, Any] = field(default_factory=dict)
    max_trials: int | None = None
    metric: str = "val_loss"

    @property
    def trial_count(self) -> int:
        count = 1
        for values in self.axes.values():
            count *= max(1, len(values))
        return count if self.axes else 0

    @property
    def effective_max_trials(self) -> int:
        return int(self.max_trials) if self.max_trials is not None else MAX_TRIALS


class GridSpecError(ValueError):
    """The search spec is invalid (unknown axis, bad type, too large)."""


class GridTooLargeError(GridSpecError):
    """The materialized grid exceeds the bounded-sweep ceiling."""


class TrialRunnerError(RuntimeError):
    """A trial_runner returned an invalid result envelope."""


def load_spec(source: str | Path | dict[str, Any]) -> GridSearchSpec:
    """Parse a YAML spec (path or inline text) or a plain dict.

    Raises:
        GridSpecError: unknown axis, empty grid, bad types.
        GridTooLargeError: materialized grid exceeds the trial ceiling.
    """
    if isinstance(source, dict):
        payload = dict(source)
    else:
        import yaml  # lazy: keeps the module importable without PyYAML

        text = Path(source).read_text(encoding="utf-8") if isinstance(source, Path) else str(source)
        loaded = yaml.safe_load(text)
        if not isinstance(loaded, dict):
            raise GridSpecError(
                f"spec must be a YAML mapping at the top level, got {type(loaded).__name__}"
            )
        payload = loaded

    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        raise GridSpecError("spec is missing a non-empty string 'name'")
    unknown = sorted(
        set(payload) - KNOWN_AXES - {"name", "seed", "base_config", "max_trials", "metric", "axes"}
    )
    if unknown:
        raise GridSpecError(
            f"unknown spec key(s) {unknown}; expected 'axes' (a mapping of axis name -> values) "
            f"or one of {sorted(KNOWN_AXES)} at the top level, plus "
            f"{sorted({'name', 'seed', 'base_config', 'max_trials', 'metric'})}"
        )
    # Canonical form is a nested `axes:` mapping; bare top-level axis keys
    # (learning_rate: [...]) are accepted as a flat shorthand.
    axes_raw = payload.get("axes")
    if axes_raw is None:
        raw_axes: dict[str, Any] = {}
    elif isinstance(axes_raw, dict):
        raw_axes = dict(axes_raw)
    else:
        raise GridSpecError("'axes' must be a mapping of axis name -> values")
    for key, value in payload.items():
        if key in KNOWN_AXES:
            if key in raw_axes:
                raise GridSpecError(
                    f"axis {key!r} appears both at the top level and inside 'axes'; "
                    "specify it in ONE place only"
                )
            raw_axes[key] = value
    if not raw_axes:
        raise GridSpecError(
            "spec needs a non-empty 'axes' mapping (learning_rate/weight_decay/dropout/hidden_dim/...)"
        )
    unknown_axes = sorted(set(raw_axes) - KNOWN_AXES)
    if unknown_axes:
        raise GridSpecError(
            f"unknown search axis(es) {unknown_axes}; expected one of {sorted(KNOWN_AXES)}"
        )

    axes: dict[str, list[Any]] = {}
    for axis in sorted(raw_axes):
        axes[axis] = _as_value_list(raw_axes[axis], axis)

    spec = GridSearchSpec(
        name=name.strip(),
        axes=axes,
        seed=int(payload.get("seed", 42)),
        base_config=dict(payload.get("base_config") or {}),
        max_trials=payload.get("max_trials"),
        metric=str(payload.get("metric", "val_loss")),
    )
    ceiling = spec.effective_max_trials
    if ceiling < 1:
        raise GridSpecError(f"max_trials must be >= 1, got {ceiling}")
    if spec.trial_count > ceiling:
        raise GridTooLargeError(
            f"grid for {spec.name!r} materializes {spec.trial_count} trials but the "
            f"bounded-sweep ceiling is {ceiling}; narrow the axes or raise max_trials "
            "(unbounded search is a NON_GOAL — the ceiling is the safety net)"
        )
    return spec


def materialize_grid(spec: GridSearchSpec) -> list[dict[str, Any]]:
    """Deterministic Cartesian product in sorted-axis, sorted-value order.

    The ordering is stable: the same spec always yields the same trial
    sequence, so run-to-run comparisons are meaningful.
    """
    axis_names = sorted(spec.axes)
    combos: list[dict[str, Any]] = [{}]
    for axis in axis_names:
        combos = [{**combo, axis: value} for combo in combos for value in spec.axes[axis]]
    return combos


def trial_id(spec: GridSearchSpec, index: int, params: dict[str, Any]) -> str:
    """Stable, human-readable experiment id for trial ``index``."""
    parts = [f"{_slugify(spec.name)}", f"{index:03d}"]
    for axis in sorted(params):
        raw = params[axis]
        value = float(raw)
        text = f"{value:g}"
        parts.append(f"{_slugify(axis)}{text}")
    ident = "-".join(parts)
    return ident[: experiment_id_max_len()] if len(ident) > 96 else ident


def experiment_id_max_len() -> int:
    """SQLite TEXT PRIMARY KEY has no practical limit here; cap for filenames."""
    return 96


#: Trial runner signature: (params, seed, output_dir) -> TrialResult.
TrialRunner = Callable[[dict[str, Any], int, Path], TrialResult]


def synthetic_trial_runner(params: dict[str, Any], seed: int, output_dir: Path) -> TrialResult:
    """Deterministic, dependency-free evaluator used by tests and smoke runs.

    The response surface is a smooth quadratic in ``learning_rate`` with
    axis-wise penalties, so a grid search has a genuine optimum to find
    and the leaderboard ordering is checkable by hand. It writes one
    artifact file per trial so ``artifact_path`` is real, not a lie.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    lr = float(params.get("learning_rate", 1e-3))
    wd = float(params.get("weight_decay", 0.0))
    dropout = float(params.get("dropout", 0.0))
    hidden = float(params.get("hidden_dim", 64.0))

    # Reproducible per-trial noise from the seed (stdlib only).
    import random

    rng = random.Random(seed)
    jitter = rng.uniform(-0.02, 0.02)

    val_loss = (
        0.60
        + 500.0 * (lr - 3e-3) ** 2  # clear optimum at lr=3e-3 (dominates seed jitter)
        + 15.0 * wd**2
        + 0.5 * abs(dropout - 0.15)  # mild dropout preference
        + 1e-4 * abs(hidden - 96.0)
        + jitter
    )
    fold_sharpe = max(0.0, 2.10 - 200.0 * (lr - 3e-3) ** 2 - 2.0 * wd)
    minority_f1 = max(0.0, 0.62 - 6.0 * abs(dropout - 0.15) - 0.5 * wd)
    if not math.isfinite(val_loss):
        raise FloatingPointError("synthetic trial produced a non-finite val_loss")

    artifact = output_dir / "metrics.json"
    metrics = {
        "val_loss": round(val_loss, 6),
        "fold_sharpe": round(fold_sharpe, 6),
        "minority_f1": round(minority_f1, 6),
    }
    artifact.write_text(json.dumps(metrics, sort_keys=True), encoding="utf-8")
    return TrialResult(
        experiment_id=str(params.get("_experiment_id", "")),
        params={k: v for k, v in params.items() if not k.startswith("_")},
        metrics=metrics,
        artifact_path=str(artifact),
    )


def lab_trial_runner_factory(
    frame: Any = None,
    feature_cols: list[str] | None = None,
) -> TrialRunner:
    """Build a TrialRunner that drives the real model_lab trainer.

    ``frame`` is the labeled polars DataFrame with a ``_split`` column
    (train/val/oos) shared by every trial — the dataset is FIXED while
    only hyperparameters vary, which is the definition of a controlled
    experiment.

    Kept lazy and torch-free at import time: the training stack
    (``nexus_scalp.model_lab.trainer``) pulls torch + polars, which the
    slim verification venv deliberately omits. Importing it here would
    make the test suite uncollectable on CPU-only CI legs.

    Axis mapping: only axes that are declared ``ExperimentSpec`` fields
    are forwarded to the trainer (``learning_rate``, ``weight_decay``,
    ``batch_size``, ``epochs``, ``label_smoothing``...). Axes that the
    spec does not carry — notably ``dropout`` and ``hidden_dim``, which
    are model *architecture* knobs living in
    ``nexus_scalp/model_lab/architectures.py`` (outside this task's
    OWNERSHIP_SCOPE) — stay in the recorded ``params``/``model_config``
    so a caller's architecture layer can read them; they are NOT
    silently dropped from the provenance record.
    """

    def runner(params: dict[str, Any], seed: int, output_dir: Path) -> TrialResult:
        from nexus_scalp.model_lab.registry import ExperimentSpec
        from nexus_scalp.model_lab.trainer import train_lab_model

        base = spec_defaults()
        base.update({k: v for k, v in params.items() if not k.startswith("_")})
        base["experiment_id"] = str(params.get("_experiment_id", base["experiment_id"]))
        base["seed"] = seed
        # Forward only fields ExperimentSpec actually declares; the rest
        # remain in the recorded provenance (params/model_config).
        forward = {k: v for k, v in base.items() if k in ExperimentSpec.model_fields}
        spec = ExperimentSpec(**forward)
        result = train_lab_model(spec, frame, feature_cols or [])
        metrics = dict(result.get("metrics", {}))
        return TrialResult(
            experiment_id=str(result.get("experiment_id", spec.experiment_id)),
            params={k: v for k, v in params.items() if not k.startswith("_")},
            metrics=metrics,
            artifact_path=str(result.get("artifact_path", "")),
            error=str(result.get("error", "")),
        )

    return runner


def spec_defaults() -> dict[str, Any]:
    """Minimal ExperimentSpec defaults for the lab runner (overridden by axes)."""
    return {
        "experiment_id": "grid_trial",
        "model_family": "STUDENT_MLP",
        "input_dimension": 70,
        "num_classes": 3,
        "sequence_length": 1,
        "seed": 42,
        "learning_rate": 5e-4,
        "weight_decay": 0.0,
        "epochs": 10,
        "batch_size": 256,
    }


def _sha256_of(path: Path) -> str:
    import hashlib

    if not path or not Path(path).is_file():
        return ""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class SweepReport:
    """Summary of a completed (or partially completed) sweep."""

    name: str
    spec: dict[str, Any]
    trials: list[dict[str, Any]]
    leaderboard: list[dict[str, Any]]
    completed: int
    failed: int
    skipped_nan: int
    started_at: str
    finished_at: str
    metric: str
    registry_root: str
    leaderboard_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "metric": self.metric,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "spec": self.spec,
            "registry_root": self.registry_root,
            "leaderboard_path": self.leaderboard_path,
            "stats": {
                "total_trials": len(self.trials),
                "completed": self.completed,
                "failed": self.failed,
                "skipped_nan": self.skipped_nan,
            },
            "leaderboard": self.leaderboard,
            "trials": self.trials,
        }


def _nan_in_metrics(metrics: dict[str, Any]) -> bool:
    for value in metrics.values():
        if isinstance(value, float) and not math.isfinite(value):
            return True
        if isinstance(value, dict) and _nan_in_metrics(value):
            return True
    return False


def run_grid_search(
    spec: GridSearchSpec,
    *,
    trial_runner: TrialRunner = synthetic_trial_runner,
    registry: ExperimentRegistry | None = None,
    output_root: Path | str | None = None,
    on_trial: Callable[[int, int, TrialResult], None] | None = None,
    top_n: int = 3,
) -> SweepReport:
    """Execute a bounded grid sweep.

    Every trial is registered PENDING *before* its work starts, then
    finalized COMPLETED (metrics) or FAILED (error). A trial whose
    metrics contain NaN/inf is recorded FAILED and skipped
    (ABORT_CONDITIONS) — the sweep continues, the leaderboard never
    ranks a poisoned result.

    Returns a :class:`SweepReport` and writes ``leaderboard.json``.
    """
    if registry is None:
        registry = ExperimentRegistry()
    if output_root is None:
        output_root = registry.root / "sweeps" / _slugify(spec.name)
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    combos = materialize_grid(spec)
    started_at = _utc_now()
    trials: list[dict[str, Any]] = []
    completed = failed = skipped_nan = 0
    git_sha, git_dirty, working_tree_hash = capture_git_revision(cwd=os.getcwd())

    for index, params in enumerate(combos):
        trial_seed = int(spec.seed) + index
        ident = trial_id(spec, index, params)
        trial_dir = output_root / ident
        trial_dir.mkdir(parents=True, exist_ok=True)

        record = ExperimentRecord(
            experiment_id=ident,
            git_sha=git_sha,
            git_dirty=git_dirty,
            working_tree_hash=working_tree_hash,
            dataset_id=str(spec.base_config.get("dataset_id", "")),
            dataset_hash=str(spec.base_config.get("dataset_hash", "")),
            model_config={**spec.base_config, **params},
            params={**params, "trial_index": index, "base_seed": int(spec.seed)},
            seed=trial_seed,
            metrics={},
            status="PENDING",
        )
        registered = registry.register(record)
        if registered.status != "PENDING":
            # Idempotency: trial was already finalized in a prior run.
            # Reuse its existing record rather than trying to overwrite
            # immutable metrics (which would violate the contract).
            if registered.status == "COMPLETED":
                completed += 1
                trials.append(
                    {
                        "experiment_id": ident,
                        "index": index,
                        "params": params,
                        "seed": trial_seed,
                        "status": "COMPLETED",
                        "metrics": registered.metrics,
                        "artifact_path": "",
                        "artifact_hash": registered.artifact_hash,
                        "manifest_path": str(registry._manifest_path(ident)),
                        "output_dir": str(trial_dir),
                        "reused": True,
                    }
                )
            else:
                failed += 1
                trials.append(
                    {
                        "experiment_id": ident,
                        "index": index,
                        "params": params,
                        "seed": trial_seed,
                        "status": registered.status,
                        "error": registered.metrics.get("error", "previously failed"),
                        "output_dir": str(trial_dir),
                        "reused": True,
                    }
                )
            continue

        error = ""
        metrics: dict[str, Any] = {}
        artifact_path = ""
        try:
            result = trial_runner({**params, "_experiment_id": ident}, trial_seed, trial_dir)
            if result.experiment_id and result.experiment_id != ident:
                raise TrialRunnerError(
                    f"trial runner returned experiment_id {result.experiment_id!r}, "
                    f"expected {ident!r}"
                )
            metrics = dict(result.metrics)
            artifact_path = result.artifact_path
            error = result.error
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"

        if not error and _nan_in_metrics(metrics):
            skipped_nan += 1
            error = "ABORT_CONDITIONS: trial metrics contain NaN/inf; skipped"

        if error:
            failed += 1
            registry.record_failure(ident, error)
            trials.append(
                {
                    "experiment_id": ident,
                    "index": index,
                    "params": params,
                    "seed": trial_seed,
                    "status": "FAILED",
                    "error": error[:800],
                    "output_dir": str(trial_dir),
                }
            )
        else:
            completed += 1
            artifact_hash = _sha256_of(Path(artifact_path)) if artifact_path else ""
            manifest_sha = ""
            finalized = registry.record_result(
                ident, metrics=metrics, artifact_hash=artifact_hash, manifest_sha256=manifest_sha
            )
            try:
                written = registry.write_manifest(finalized)
                manifest_sha = str(written)
            except Exception:
                manifest_sha = ""
            trials.append(
                {
                    "experiment_id": ident,
                    "index": index,
                    "params": params,
                    "seed": trial_seed,
                    "status": "COMPLETED",
                    "metrics": metrics,
                    "artifact_path": artifact_path,
                    "artifact_hash": artifact_hash,
                    "manifest_path": manifest_sha,
                    "output_dir": str(trial_dir),
                }
            )
        if on_trial is not None:
            on_trial(index, len(combos), TrialResult(ident, params, metrics, artifact_path, error))

    leaderboard_records = (
        registry.top_n(spec.metric, n=max(1, min(max(1, int(top_n)), len(combos))))
        if combos
        else []
    )
    leaderboard = [
        {
            "rank": rank,
            "experiment_id": rec.experiment_id,
            "metric": {spec.metric: rec.metrics.get(spec.metric)},
            "metrics": rec.metrics,
            "params": rec.params,
            "seed": rec.seed,
            "artifact_hash": rec.artifact_hash,
        }
        for rank, rec in enumerate(leaderboard_records, start=1)
    ]
    leaderboard_path = output_root / "leaderboard.json"
    report = SweepReport(
        name=spec.name,
        spec={
            "name": spec.name,
            "axes": spec.axes,
            "seed": spec.seed,
            "base_config": spec.base_config,
            "max_trials": spec.effective_max_trials,
            "metric": spec.metric,
        },
        trials=trials,
        leaderboard=leaderboard,
        completed=completed,
        failed=failed,
        skipped_nan=skipped_nan,
        started_at=started_at,
        finished_at=_utc_now(),
        metric=spec.metric,
        registry_root=str(registry.root),
        leaderboard_path=str(leaderboard_path),
    )
    _atomic_write_json(leaderboard_path, report.to_dict())
    return report


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bounded hyperparameter grid search (ML-EXP-003).",
    )
    parser.add_argument("spec", help="Path to the grid search YAML spec")
    parser.add_argument(
        "--registry-root",
        default=str(DEFAULT_ROOT),
        help="ExperimentRegistry root (default: artifacts/experiments)",
    )
    parser.add_argument(
        "--output-root",
        default=None,
        help="Per-trial output directory root (default: <registry>/sweeps/<name>)",
    )
    parser.add_argument(
        "--runner",
        default="synthetic",
        choices=["synthetic", "lab"],
        help="Trial runner (synthetic = dependency-free smoke, lab = model_lab trainer)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=3,
        help="Leaderboard depth (default 3)",
    )
    args = parser.parse_args(argv)

    spec = load_spec(Path(args.spec))
    registry = ExperimentRegistry(root=args.registry_root)
    runner: TrialRunner = (
        lab_trial_runner_factory() if args.runner == "lab" else synthetic_trial_runner
    )
    report = run_grid_search(
        spec,
        trial_runner=runner,
        registry=registry,
        output_root=args.output_root,
        top_n=args.top,
    )
    print(
        json.dumps(
            {
                "name": report.name,
                "metric": report.metric,
                "stats": report.to_dict()["stats"],
                "leaderboard_path": report.leaderboard_path,
                "top": report.leaderboard[: max(1, args.top)],
            },
            indent=2,
        )
    )
    return 0 if report.completed else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
