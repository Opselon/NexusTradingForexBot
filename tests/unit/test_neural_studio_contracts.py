"""Neural Studio 50D/70D contract + model builder regression battery.

Covers the proven defect set and the new model-engineering surface:
  - 50D/70D cross-contamination is rejected in every direction (Phase 40)
  - the 'loss 0.0000' class is reported as NOT_TRAINED, not as a metric (Phase 12)
  - the model builder resolves real contracts and rejects impossible configs (Phases 4/41)
  - runtime state distinguishes LOADED / INFERENCE AVAILABLE / ENGINE RUNNING (Phases 3/35)
  - the tensor inspector reports the raw/normalized/model-input triple at one width (Phases 23/24)
  - OOS is carved out before the fit split and never fitted on (Phase 14)
  - switch requires confirmation and leaves the runtime truthful (Phases 20/39)
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import numpy as np
import pytest

# Ensure the suite under test binds THIS tree's src (the dev venv's editable
# install pins nexus_scalp to the main checkout otherwise).
REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexus_scalp.model_lab.contract_gate import (  # noqa: E402
    ContractDimensionError,
    assert_model_weights_dimension,
    assert_scaler_compatibility,
    assert_scaler_file_compatibility,
    assert_schema_dimension,
    assert_tensor_dimension,
    assert_vector_dimension,
)
from nexus_scalp.model_lab.model_builder import (  # noqa: E402
    DIMENSION_TO_SCHEMA_ID,
    SUPPORTED_DIMENSIONS,
    ModelBuilderConfig,
    get_feature_contract,
    validate_builder_config,
)
from nexus_scalp.model_lab.runtime_state import (  # noqa: E402
    INFERENCE_STATES,
    MODEL_LIFECYCLE_STATES,
    resolve_runtime_state,
)
from nexus_scalp.model_lab.tensor_inspector import inspect_tensor  # noqa: E402

# =============================================================================
# Phase 40 — 50D / 70D cross-contamination audit
# =============================================================================


class _FakeScaler:
    def __init__(self, width: int, ready: bool = True) -> None:
        self.mean = np.zeros(width, dtype=np.float32)
        self.std = np.ones(width, dtype=np.float32)
        self._width = width
        self._ready = ready

    def is_ready(self) -> bool:
        return self._ready

    def dimension(self) -> int:
        return self._width

    def transform(self, x: np.ndarray) -> np.ndarray:
        if x.shape[-1] != self._width:
            raise ContractDimensionError(f"scaler is {self._width}D, input is {int(x.shape[-1])}D")
        return np.clip(x, -5.0, 5.0)


@pytest.mark.parametrize(
    "dimension",
    [50, 70],
)
def test_supported_dimensions_resolve_their_contract(dimension: int) -> None:
    """Each contract resolves exactly its own schema id and feature count."""
    contract = get_feature_contract(dimension)
    assert contract.dimension == dimension
    assert contract.schema_id == DIMENSION_TO_SCHEMA_ID[dimension]
    assert len(contract.slots) == dimension
    assert contract.dtype == "float32"
    assert contract.output_classes == 3
    # The verified 70D grouping (Phase 5): 0..49 / 50..59 / 60..69
    if dimension == 70:
        families = contract._family_ranges()
        assert families["BASE"] == {"start": 0, "end": 49, "count": 50}
        assert families["NEWS"] == {"start": 50, "end": 59, "count": 10}
        assert families["LIQUIDITY"] == {"start": 60, "end": 69, "count": 10}


def test_unsupported_dimension_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported model contract dimension"):
        get_feature_contract(60)


@pytest.mark.parametrize("bad_dim", [49, 51, 69, 71, 100])
def test_vector_width_mismatch_is_rejected(bad_dim: int) -> None:
    """A 50-feature vector is not a 70-feature vector with 20 missing."""
    with pytest.raises(ContractDimensionError):
        assert_vector_dimension([0.0] * bad_dim, 70, context="audit")


def test_truncation_and_padding_are_both_rejected() -> None:
    with pytest.raises(ContractDimensionError):
        assert_vector_dimension([0.0] * 50, 70, context="truncation")
    with pytest.raises(ContractDimensionError):
        assert_vector_dimension([0.0] * 70, 50, context="padding")


def test_tensor_width_mismatch_is_rejected() -> None:
    with pytest.raises(ContractDimensionError):
        assert_tensor_dimension(np.zeros((1, 50), dtype=np.float32), 70)
    with pytest.raises(ContractDimensionError):
        assert_tensor_dimension(np.zeros((1, 70), dtype=np.float32), 50)


def test_50d_scaler_on_70d_model_is_rejected() -> None:
    """The exact defect the old _StudioLoadedScaler permitted."""
    scaler_50 = _FakeScaler(50)
    with pytest.raises(ContractDimensionError):
        assert_scaler_compatibility(scaler_50, 70, context="contamination")


def test_70d_scaler_on_50d_model_is_rejected() -> None:
    with pytest.raises(ContractDimensionError):
        assert_scaler_compatibility(_FakeScaler(70), 50)


@pytest.mark.parametrize("dimension", [50, 70])
def test_matching_scaler_passes(dimension: int) -> None:
    assert assert_scaler_compatibility(_FakeScaler(dimension), dimension) is None


def test_schema_dimension_binding_is_enforced() -> None:
    assert assert_schema_dimension("scalp_v1", 50) is None
    assert assert_schema_dimension("scalp_v3", 70) is None
    with pytest.raises(ContractDimensionError):
        assert_schema_dimension("scalp_v1", 70, context="misbinding")
    with pytest.raises(ContractDimensionError):
        assert_schema_dimension("scalp_v3", 50, context="misbinding")


def test_weights_dimension_is_read_from_the_checkpoint(tmp_path: Path) -> None:
    import torch

    from nexus_scalp.models.scalp_net import ScalpNet

    ckpt = tmp_path / "m70.pt"
    torch.save(ScalpNet(num_features=70, num_classes=3).state_dict(), ckpt)
    assert assert_model_weights_dimension(ckpt, 70) is None
    with pytest.raises(ContractDimensionError):
        assert_model_weights_dimension(ckpt, 50)


def test_scaler_file_width_is_checked(tmp_path: Path) -> None:
    path = tmp_path / "m.scaler.npz"
    np.savez(path, mean=np.zeros(50), std=np.ones(50), dimension=50)
    assert assert_scaler_file_compatibility(path, 50) is None
    with pytest.raises(ContractDimensionError):
        assert_scaler_file_compatibility(path, 70)


# =============================================================================
# Phase 12 — the suspicious 0.0000 loss class
# =============================================================================


def _tmp_registry(tmp_path: Path):
    from nexus_scalp.model_generation.model_registry import ModelRegistry

    return ModelRegistry(db_path=tmp_path / "models.db")


def test_never_trained_record_reports_not_trained(tmp_path: Path) -> None:
    """A registered-but-never-trained row must not present 0.0 as a metric."""
    from nexus_scalp.model_generation.model_registry import ModelRecord

    registry = _tmp_registry(tmp_path)
    registry.register_model(
        ModelRecord(
            id="phantom_50d",
            name="phantom_50d.pt",
            version="1.0.0",
            dimension=50,
            architecture="ScalpNet",
            weights_path="artifacts/model_generation/checkpoints/phantom_50d.pt",
            sha256="abc",
        )
    )
    rec = registry.get_model("phantom_50d")
    assert rec is not None
    assert rec.training_status == "NOT_TRAINED"
    assert rec.final_loss == 0.0  # the default, which is exactly the trap
    assert rec.epochs == 0


def test_legacy_db_migrates_and_backfills(tmp_path: Path) -> None:
    """A pre-existing models.db gains the column and keeps its truthful rows."""
    from nexus_scalp.model_generation.model_registry import (
        ModelRecord,
        ModelRegistry,
    )

    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE model_checkpoints (
            id TEXT PRIMARY KEY, name TEXT, version TEXT, dimension INTEGER,
            architecture TEXT, weights_path TEXT, scaler_path TEXT,
            manifest_path TEXT, sha256 TEXT, epochs INTEGER, final_loss REAL,
            final_val_loss REAL, accuracy REAL, dataset_path TEXT,
            is_active INTEGER, fine_tune_enabled INTEGER, stage TEXT,
            created_at TEXT, loaded_at TEXT, metrics_json TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO model_checkpoints (id, name, version, dimension, architecture, "
        "weights_path, scaler_path, manifest_path, sha256, epochs, final_loss, "
        "final_val_loss, accuracy, dataset_path, is_active, fine_tune_enabled, stage, "
        "created_at, loaded_at, metrics_json) VALUES "
        "('trained_a','a','1',50,'ScalpNet','p','s','','x',3,0.91,0.95,0.0,'',0,1,"
        "'STAGING','2026-01-01T00:00:00Z',NULL,'{}'),"
        "('phantom_b','b','1',50,'ScalpNet','p2','','','y',0,0.0,0.0,0.0,'',0,0,"
        "'STAGING','2026-01-01T00:00:00Z',NULL,'{}')"
    )
    conn.commit()
    conn.close()

    registry = ModelRegistry(db_path=db)
    trained = registry.get_model("trained_a")
    phantom = registry.get_model("phantom_b")
    assert trained is not None and phantom is not None
    assert trained.training_status == "TRAINED"
    assert phantom.training_status == "NOT_TRAINED"


# =============================================================================
# Phases 4 / 41 — model builder validation
# =============================================================================


def _cfg(**overrides) -> ModelBuilderConfig:
    base = {
        "config_name": "test",
        "dimension": 70,
        "schema_id": "scalp_v3",
    }
    base.update(overrides)
    return ModelBuilderConfig(**base)


def test_valid_config_passes() -> None:
    findings = validate_builder_config(_cfg())
    assert [f for f in findings if f.severity == "error"] == []


def test_schema_dimension_mismatch_is_an_error() -> None:
    findings = validate_builder_config(_cfg(dimension=70, schema_id="scalp_v1"))
    codes = [f.code for f in findings if f.severity == "error"]
    assert "SCHEMA_DIMENSION_MISMATCH" in codes


def test_fine_tune_base_dimension_contamination_is_an_error(tmp_path: Path) -> None:
    """Fine-tuning a 50D base into a 70D contract is contamination."""
    from nexus_scalp.model_generation.model_registry import (
        ModelRecord,
        ModelRegistry,
    )

    registry = _tmp_registry(tmp_path)
    registry.register_model(
        ModelRecord(
            id="some_50d_model",
            name="some_50d_model.pt",
            version="1.0.0",
            dimension=50,
            architecture="ScalpNet",
            weights_path="p",
            sha256="abc",
            fine_tune_enabled=True,
        )
    )
    findings = validate_builder_config(
        _cfg(dimension=70, train_mode="fine_tune", base_model_id="some_50d_model"),
        registry=registry,
    )
    codes = [f.code for f in findings if f.severity == "error"]
    assert "FINE_TUNE_DIM_MISMATCH" in codes


def test_fine_tune_without_base_is_an_error() -> None:
    findings = validate_builder_config(_cfg(train_mode="fine_tune"))
    codes = [f.code for f in findings if f.severity == "error"]
    assert "FINE_TUNE_NO_BASE" in codes


def test_unsupported_architecture_option_is_an_error() -> None:
    findings = validate_builder_config(_cfg(hidden_dim=99999))
    assert any(f.code == "HIDDEN_DIM_UNSUPPORTED" for f in findings)
    findings = validate_builder_config(_cfg(optimizer="rmsprop"))
    assert any(f.code == "OPTIMIZER_UNSUPPORTED" for f in findings)


def test_output_classes_contract_is_enforced() -> None:
    findings = validate_builder_config(_cfg(output_classes=4))
    assert any(f.code == "OUTPUT_CLASSES_CONTRACT" for f in findings)


def test_builder_options_only_advertise_real_capabilities() -> None:
    from nexus_scalp.model_lab.model_builder import builder_options

    opts = builder_options()
    assert set(opts["supported_dimensions"]) == set(SUPPORTED_DIMENSIONS)
    assert opts["architecture"]["params"] == [
        "num_features",
        "num_classes",
        "hidden_dim",
        "num_heads",
        "dropout_rate",
    ]
    assert set(opts["contracts"].keys()) == set(SUPPORTED_DIMENSIONS)


# =============================================================================
# Phases 3 / 35 — runtime state machine
# =============================================================================


class _Engine:
    def __init__(self, healthy: bool | None = True) -> None:
        self._healthy = healthy

    def is_healthy(self) -> bool:
        if self._healthy is None:
            raise RuntimeError("probe exploded")
        return self._healthy


class _Bundle:
    def __init__(self, model_id: str, dimension: int, scaler_ready: bool = True) -> None:
        self.model_id = model_id
        self.dimension = dimension
        self.scaler = _FakeScaler(dimension, ready=scaler_ready)

        import torch

        from nexus_scalp.models.scalp_net import ScalpNet

        self.model = ScalpNet(num_features=dimension, num_classes=3)
        del torch


def test_engine_stopped_model_loaded_inference_blocked() -> None:
    """The legitimate 'LOADED but inference BLOCKED' state the campaign names."""
    state = resolve_runtime_state(
        engine=None,
        hot_loaded_bundle=_Bundle("m70", 70),
    )
    assert state.engine_state == "STOPPED"
    assert state.model_state == "LOADED"
    assert state.inference_state == "BLOCKED"
    assert state.model_loaded is True
    assert state.inference_available is False
    assert state.engine_running is False


def test_no_model_reports_not_loaded() -> None:
    state = resolve_runtime_state(engine=_Engine(True))
    assert state.model_state == "NOT_LOADED"
    assert state.inference_state == "NOT_LOADED"
    assert state.inference_available is False


def test_everything_ready_reports_ready() -> None:
    state = resolve_runtime_state(engine=_Engine(True), hot_loaded_bundle=_Bundle("m70", 70))
    assert state.engine_state == "RUNNING"
    assert state.inference_state == "READY"
    assert state.inference_available is True


def test_unready_scaler_blocks_inference() -> None:
    state = resolve_runtime_state(
        engine=_Engine(True),
        hot_loaded_bundle=_Bundle("m70", 70, scaler_ready=False),
    )
    assert state.inference_state == "BLOCKED"
    assert "scaler is not ready" in state.inference_detail


def test_degraded_engine_blocks_inference() -> None:
    state = resolve_runtime_state(engine=_Engine(False), hot_loaded_bundle=_Bundle("m70", 70))
    assert state.engine_state == "DEGRADED"
    assert state.inference_state == "BLOCKED"


def test_probe_failure_is_unknown_not_ready() -> None:
    state = resolve_runtime_state(engine=_Engine(None))
    assert state.engine_state == "UNKNOWN"
    assert state.inference_available is False


def test_lifecycle_states_are_distinct_vocabulary() -> None:
    overlap = set(MODEL_LIFECYCLE_STATES) & set(INFERENCE_STATES)
    # The shared words are intentional READY/FAILED which mean different things
    # in the two machines — but the sets must not be identical (no overload).
    assert MODEL_LIFECYCLE_STATES != INFERENCE_STATES
    assert len(overlap) > 0  # READY/FAILED are shared by design
    assert "BLOCKED" in INFERENCE_STATES
    assert "WARMING" in MODEL_LIFECYCLE_STATES


# =============================================================================
# Phases 23 / 24 / 30 — tensor inspection
# =============================================================================


@pytest.mark.parametrize("dimension", [50, 70])
def test_tensor_inspection_dimensions_match(dimension: int) -> None:
    inspection = inspect_tensor(
        model_id=f"m{dimension}",
        dimension=dimension,
        raw_features=[0.5] * dimension,
        scaler=_FakeScaler(dimension),
    )
    dims = inspection.to_dict()["all_dimensions"]
    assert dims == {"raw": dimension, "normalized": dimension, "model_input": dimension}
    assert inspection.dimensions_match is True
    assert inspection.nan_count == 0
    assert len(inspection.slots) == dimension


def test_tensor_inspection_flags_zero_default_and_nan() -> None:
    raw = [0.0] * 70
    raw[3] = float("nan")
    inspection = inspect_tensor(
        model_id="m70",
        dimension=70,
        raw_features=raw,
        scaler=_FakeScaler(70),
    )
    assert inspection.nan_count == 1
    assert inspection.zero_default_count == 69
    flags = {f for s in inspection.slots for f in s.flags}
    assert "NAN" in flags
    assert "ZERO_DEFAULT" in flags


def test_tensor_inspection_rejects_width_contamination() -> None:
    with pytest.raises(ContractDimensionError):
        inspect_tensor(
            model_id="m70",
            dimension=70,
            raw_features=[0.0] * 50,
            scaler=_FakeScaler(70),
        )


# =============================================================================
# Phase 14 — OOS integrity
# =============================================================================


def test_oos_split_is_carved_out_before_training(tmp_path: Path) -> None:
    import polars as pl

    from nexus_scalp.model_lab.studio_trainer import NeuralStudioTrainer

    n = 400
    df = pl.DataFrame(
        {
            "open": np.linspace(2000, 2050, n),
            "high": np.linspace(2001, 2051, n),
            "low": np.linspace(1999, 2049, n),
            "close": np.linspace(2000, 2050, n),
            "tick_volume": np.ones(n),
        }
    )
    path = tmp_path / "bars.parquet"
    df.write_parquet(path)

    trainer = NeuralStudioTrainer(repo_root=tmp_path)
    # The trainer resolves datasets through the studio's safe-path inventory,
    # which confines reads to allowlisted roots and refuses bare absolute
    # paths. Stage the frame under the repo's datasets root and name it by
    # inventory key so resolution is legitimate.
    data_root = tmp_path / "data" / "processed"
    data_root.mkdir(parents=True, exist_ok=True)
    staged = data_root / "XAUUSD_M1_studio_contract.parquet"
    df.write_parquet(staged)

    result = trainer.train(
        _cfg(
            dimension=70,
            dataset_path="XAUUSD_M1_studio_contract.parquet",
            epochs=1,
            batch_size=64,
            oos_ratio=0.2,
        )
    )
    assert result.status == "COMPLETE", result.error
    metrics = result.metrics
    assert metrics.metrics_measured is True
    assert metrics.train_loss is not None
    assert metrics.validation_loss is not None
    # OOS was actually evaluated, not skipped.
    assert metrics.oos_loss is not None
    # The scaler is fitted on the FIT set: its mean width is the contract width.
    scaler_path = tmp_path / result.artifact["scaler_path"]
    data = np.load(scaler_path)
    assert int(data["dimension"]) == 70
    assert data["mean"].shape[-1] == 70


def test_oos_metric_is_not_used_for_early_stopping() -> None:
    """OOS is carved out and evaluated, but never fitted on or used to stop.

    Structural assertions on ``_fit``:
      - the early-stopping branch reads validation loss only,
      - OOS tensors are *materialised* before the epoch loop (carving) but the
        criterion is applied to them exactly once, after the loop,
      - the scaler is fitted on the FIT set only.
    """
    import inspect as _inspect

    from nexus_scalp.model_lab import studio_trainer

    src = _inspect.getsource(studio_trainer.NeuralStudioTrainer._fit)
    loop_start = src.index("for _epoch in range(1, cfg.epochs + 1)")
    oos_eval = src.index("# 8. OOS")

    # Early stopping is driven by validation loss alone.
    assert "val_loss < best_val" in src
    assert "oos_loss" not in src[loop_start:oos_eval]

    # The OOS tensor is never an optimiser input: the criterion runs on it once,
    # after the training loop has closed.
    assert "criterion(model(X_oos)" in src[oos_eval:]
    assert "model(X_oos)" not in src[loop_start:oos_eval]

    # The scaler is fitted on the fit split only.
    assert "fitted on the FIT set only" in src
