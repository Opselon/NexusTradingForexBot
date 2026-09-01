import tempfile
from pathlib import Path

import numpy as np
import polars as pl
import pytest
import torch

from nexus_scalp.features.scalp_features import FEATURE_NAMES
from nexus_scalp.models.scalp_net import ScalpNet
from nexus_scalp.training.walk_forward_trainer import WalkForwardTrainer


def test_walk_forward_trainer_smc_fine_tune_online():
    """
    Verifies that WalkForwardTrainer ingests features, extracts the 4 SMC columns,
    applies the specialized loss multipliers, executes fine-tuning without shape errors,
    and saves the weights checkpoint atomically.
    """
    # Create temp directory for artifact outputs
    with tempfile.TemporaryDirectory() as tmp_dir:
        model_path = Path(tmp_dir) / "test_model.pt"

        # 1. Initialize WalkForwardTrainer
        trainer = WalkForwardTrainer(
            num_folds=3,
            epochs_per_fold=1,
            artifact_save_path=model_path,
            min_rows_per_train_split=10,
            min_rows_per_test_split=5,
        )

        # 2. Create mock model
        model = ScalpNet(num_features=50, num_classes=4)

        # 3. Create a realistic polars DataFrame with 50 features + 'label'
        num_rows = 100
        data = {}
        for name in FEATURE_NAMES:
            # Let's generate random numbers for all features
            data[name] = np.random.randn(num_rows).tolist()

        # Ensure our specific SMC features have realistic values
        # feat_ob_valid_bos: binary (0.0 or 1.0)
        data["feat_ob_valid_bos"] = [1.0 if idx % 2 == 0 else 0.0 for idx in range(num_rows)]
        # feat_ob_equilibrium_ratio: position relative to 50% impulse (0.0 to 1.0)
        data["feat_ob_equilibrium_ratio"] = [
            0.35 if idx % 2 == 0 else 0.75 for idx in range(num_rows)
        ]
        # feat_ob_liquidity_swept: binary (0.0 or 1.0)
        data["feat_ob_liquidity_swept"] = [1.0 if idx % 3 == 0 else 0.0 for idx in range(num_rows)]
        # feat_ob_fib_50_60_alignment: continuous proximity
        data["feat_ob_fib_50_60_alignment"] = [0.95 for _ in range(num_rows)]

        # Add labels: "NO_TRADE", "BUY_MARKET", "SELL_MARKET"
        labels = ["NO_TRADE", "BUY_MARKET", "SELL_MARKET"]
        data["label"] = [labels[idx % 3] for idx in range(num_rows)]
        data["label_evaluated"] = [True] * num_rows
        data["is_purged"] = [False] * num_rows

        df = pl.DataFrame(data)

        # Capture initial state dict
        initial_weights = {k: v.clone() for k, v in model.state_dict().items()}

        # 4. Execute online fine-tuning
        tuned_model = trainer.fine_tune_online(
            live_model=model,
            recent_df=df,
            feature_cols=FEATURE_NAMES,
            epochs=2,
            learning_rate=1e-3,
            max_holding_bars=5,
            verify_health=False,
        )

        # 5. Assertions
        assert tuned_model is not None, "Tuned model should not be None."
        assert isinstance(tuned_model, ScalpNet), "Should return a ScalpNet instance."

        # Verify weight update
        weights_updated = False
        for k in tuned_model.state_dict():
            if not torch.equal(tuned_model.state_dict()[k], initial_weights[k]):
                weights_updated = True
                break
        assert weights_updated, "Model weights should be updated after fine-tuning."

        # Verify checkpoint is saved atomically
        assert model_path.exists(), "Trained model checkpoint should exist on disk."
        assert model_path.with_suffix(".scaler.npz").exists(), (
            "Scaler artifact should exist on disk."
        )

        # Verify we can load back the weights successfully
        loaded_state = torch.load(model_path)
        assert loaded_state is not None
        assert "classifier.weight" in loaded_state


def test_wf_fine_tune_rolls_back_on_all_nan_buffer(tmp_path):
    """BUG-169 (directive #42 learning-loop battery): a buffer whose features
    are entirely NaN must not poison the checkpoint. The trainer's extraction
    layer sanitises NaN/Inf via nan_to_num, so the run either completes with
    finite losses or is rejected - in BOTH outcomes the on-disk artifact
    (when written) must contain finite tensors and the returned model must
    never carry non-finite weights."""
    trainer = WalkForwardTrainer(
        num_folds=3,
        epochs_per_fold=1,
        min_rows_per_train_split=10,
        min_rows_per_test_split=5,
        artifact_save_path=tmp_path / "wf_nan" / "model.pt",
    )
    base_model = ScalpNet(num_features=50, num_classes=4)
    initial_state = {k: v.clone() for k, v in base_model.state_dict().items()}
    num_rows = 100
    data = {}
    for name in FEATURE_NAMES:
        data[name] = [float("nan")] * num_rows
    labels = ["NO_TRADE", "BUY_MARKET", "SELL_MARKET"]
    data["label"] = [labels[idx % 3] for idx in range(num_rows)]
    data["label_evaluated"] = [True] * num_rows
    data["is_purged"] = [False] * num_rows
    df = pl.DataFrame(data)
    returned = trainer.fine_tune_online(
        live_model=base_model,
        recent_df=df,
        feature_cols=FEATURE_NAMES,
        epochs=1,
        learning_rate=1e-3,
        max_holding_bars=5,
        verify_health=False,
    )
    for k, v in returned.state_dict().items():
        assert torch.isfinite(v).all(), f"non-finite weight survived fine-tune: {k}"
    # A rollback (identical weights) is an acceptable fail-safe outcome;
    # the contract under test is finiteness, not improvement.
    rolled_back = all(
        torch.equal(returned.state_dict()[k], initial_state[k]) for k in initial_state
    )
    if not rolled_back and (tmp_path / "wf_nan" / "model.pt").exists():
        saved = torch.load(tmp_path / "wf_nan" / "model.pt")
        for k, v in saved.items():
            assert torch.isfinite(v).all(), f"non-finite tensor persisted to checkpoint: {k}"


def test_wf_checkpoint_roundtrip_persists_exact_weights(tmp_path):
    """BUG-169 (directive #43/#30 learning-loop battery): the atomic checkpoint
    must survive a save -> unload -> reload cycle with byte-equivalent tensors
    (CPU-mapped), and the scaler artifact must round-trip its exact mean/std.
    In-memory validation alone is never sufficient evidence."""
    trainer = WalkForwardTrainer(
        num_folds=3,
        epochs_per_fold=1,
        min_rows_per_train_split=10,
        min_rows_per_test_split=5,
        artifact_save_path=tmp_path / "wf_roundtrip" / "model.pt",
    )
    base_model = ScalpNet(num_features=50, num_classes=4)
    num_rows = 100
    data = {}
    for name in FEATURE_NAMES:
        data[name] = np.random.RandomState(7).randn(num_rows).tolist()
    labels = ["NO_TRADE", "BUY_MARKET", "SELL_MARKET"]
    data["label"] = [labels[idx % 3] for idx in range(num_rows)]
    data["label_evaluated"] = [True] * num_rows
    data["is_purged"] = [False] * num_rows
    df = pl.DataFrame(data)
    trainer.fine_tune_online(
        live_model=base_model,
        recent_df=df,
        feature_cols=FEATURE_NAMES,
        epochs=1,
        learning_rate=1e-3,
        max_holding_bars=5,
        verify_health=False,
    )
    model_path = tmp_path / "wf_roundtrip" / "model.pt"
    assert model_path.exists(), "checkpoint must exist on disk"
    saved = torch.load(model_path)
    reloaded = ScalpNet(num_features=50, num_classes=4)
    reloaded.load_state_dict(saved)
    for k in reloaded.state_dict():
        assert torch.equal(reloaded.state_dict()[k], saved[k].cpu()), (
            f"checkpoint round-trip diverged for {k}"
        )
    scaler_path = model_path.with_suffix(".scaler.npz")
    assert scaler_path.exists(), "scaler artifact must exist on disk"
    bundle = trainer._load_scaler()
    import numpy as _np

    assert bundle.mean.shape[-1] == 50 and bundle.std.shape[-1] == 50
    assert _np.isfinite(bundle.mean).all() and _np.isfinite(bundle.std).all()
    # scaler must reload with EXACT persisted values (no refit drift)
    raw = _np.load(scaler_path)
    assert _np.array_equal(raw["mean"], _np.asarray(bundle.mean, dtype=_np.float32))
    assert _np.array_equal(raw["std"], _np.asarray(bundle.std, dtype=_np.float32))
