"""Progress/device plumbing seam tests, NOT training-quality evidence.

Uses the already-installed torch; no downloads or dependency installation.
Builder/publisher/hardware seams are explicitly doubled where noted.
"""

from unittest.mock import Mock

import polars as pl
import pytest
import torch

from nexus_scalp.model_generation import three_model
from nexus_scalp.training.walk_forward_trainer import WalkForwardTrainer


def test_feature_events_bracket_actual_builder_seam(monkeypatch, tmp_path):
    """Builder and labeler doubles inspect the real train_variant call order."""
    events = []
    frame = pl.DataFrame({"close": [1.0] * three_model.SMOKE_MIN_ROWS})

    def build(*args):
        assert events == [{"stage": "features", "status": "running"}]
        return frame

    def label(*args):
        assert events[-1] == {"stage": "features", "status": "done"}
        raise RuntimeError("label seam reached")

    monkeypatch.setattr(three_model, "build_feature_frame", build)
    monkeypatch.setattr(three_model, "_label_frame", label)
    with pytest.raises(RuntimeError, match="label seam reached"):
        three_model.train_variant(
            "70d_liquidity",
            frame,
            smoke=True,
            output_dir=tmp_path,
            progress_cb=events.append,
        )
    assert [e["stage"] for e in events] == ["features", "features"]


def test_explicit_cpu_overrides_available_cuda_hardware_seam(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(WalkForwardTrainer, "_set_seed", lambda *a: None)
    trainer = WalkForwardTrainer(backend="cpu")
    assert str(trainer.device) == "cpu"


@pytest.mark.parametrize("backend", [None, "cuda"])
def test_cuda_selection_hardware_seam(monkeypatch, backend):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(WalkForwardTrainer, "_set_seed", lambda *a: None)
    assert str(WalkForwardTrainer(backend=backend).device) == "cuda"


def test_cuda_unavailable_fails_without_fallback_hardware_seam(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    with pytest.raises(RuntimeError, match="CUDA backend requested"):
        WalkForwardTrainer(backend="cuda")


@pytest.mark.parametrize("backend", ["auto", "mps", "CUDA", ""])
def test_invalid_explicit_backend_rejected(backend):
    with pytest.raises(ValueError, match="backend must be"):
        WalkForwardTrainer(backend=backend)


def test_variant_forwards_backend_to_trainer_seam(monkeypatch, tmp_path):
    """Builder/labeler/trainer doubles: no numerical training claimed."""
    cols = three_model.variant_feature_columns("70d_liquidity")
    frame = pl.DataFrame({c: [0.0] * three_model.SMOKE_MIN_ROWS for c in cols})
    monkeypatch.setattr(three_model, "build_feature_frame", lambda *a: frame)
    monkeypatch.setattr(three_model, "_label_frame", lambda *a: frame)
    trainer = Mock(side_effect=RuntimeError("trainer constructor reached"))
    monkeypatch.setattr(three_model, "WalkForwardTrainer", trainer)
    with pytest.raises(RuntimeError, match="trainer constructor reached"):
        three_model.train_variant(
            "70d_liquidity", frame, smoke=True, output_dir=tmp_path, backend="cpu"
        )
    assert trainer.call_args.kwargs["backend"] == "cpu"


def test_real_cpu_epochs_and_oos_boundaries_with_publication_seam(monkeypatch, tmp_path):
    """Real CPU fitting/OOS prediction; publisher mocked to avoid bundle side effects.

    Synthetic rows exercise feedback only, never profitability or model eligibility.
    """
    import numpy as np

    events = []
    trainer = WalkForwardTrainer(
        num_folds=2,
        epochs_per_fold=1,
        backend="cpu",
        smoke=True,
        label_origin="CLEAN_HISTORICAL",
        progress_cb=events.append,
        artifact_save_path=tmp_path / "candidate" / "model.pt",
    )
    cols = list(trainer.feature_schema.columns)
    rng = np.random.default_rng(42)
    frame = pl.DataFrame({c: rng.normal(size=400) for c in cols}).with_columns(
        pl.Series("label", [i % 3 for i in range(400)])
    )
    monkeypatch.setattr(trainer, "bind_dataset_provenance_for_frame", lambda *a: None)
    publish = Mock()
    monkeypatch.setattr(trainer, "_publish_candidate_bundle", publish)
    predict = trainer._predict_classes
    train_epoch = trainer._train_one_epoch
    training_calls = []

    def observed_train(*args):
        training_calls.append(len(training_calls) + 1)
        assert events[-1]["stage"] == "train"
        assert events[-1]["status"] == "running"
        return train_epoch(*args)

    monkeypatch.setattr(trainer, "_train_one_epoch", observed_train)

    def observed_predict(model, loader):
        assert events[-1]["stage"] == "validation"
        assert events[-1]["status"] == "running"
        assert events[-1]["phase"] == "oos"
        result = predict(model, loader)
        assert result
        return result

    monkeypatch.setattr(trainer, "_predict_classes", observed_predict)
    threads = torch.get_num_threads()
    torch.set_num_threads(1)
    try:
        trainer.train_and_validate(frame, cols)
    finally:
        torch.set_num_threads(threads)
    validation = [e for e in events if e.get("stage") == "validation"]
    assert [e["status"] for e in validation] == ["running", "done"] * 2
    assert [e["fold"] for e in validation] == [1, 1, 2, 2]
    assert not any(e.get("stage") == "sequences" for e in events)
    assert len([e for e in events if "val_loss" in e]) == 2
    final_epochs = [e for e in events if e.get("phase") == "final_fit" and "loss" in e]
    assert len(final_epochs) == 1
    assert np.isfinite(final_epochs[0]["loss"])
    assert "val_loss" not in final_epochs[0]
    publish.assert_called_once()
    assert not (tmp_path / "candidate").exists()
    assert len(training_calls) == 3


@pytest.mark.parametrize("broken_callback", [False, True])
def test_failed_features_never_emit_done_seam(monkeypatch, tmp_path, broken_callback):
    events = []

    def callback(event):
        events.append(event)
        if broken_callback:
            raise RuntimeError("UI unavailable")

    builder = Mock(side_effect=RuntimeError("feature computation failed"))
    monkeypatch.setattr(three_model, "build_feature_frame", builder)
    frame = pl.DataFrame({"close": [1.0] * three_model.SMOKE_MIN_ROWS})
    with pytest.raises(RuntimeError, match="feature computation failed"):
        three_model.train_variant(
            "70d_liquidity", frame, smoke=True, output_dir=tmp_path, progress_cb=callback
        )
    builder.assert_called_once()
    assert events == [{"stage": "features", "status": "running"}]


def test_stage_callback_failures_nonfatal_and_no_callback_dormant():
    callback = Mock(side_effect=RuntimeError("UI unavailable"))
    trainer = WalkForwardTrainer(backend="cpu", progress_cb=callback)
    trainer._emit_stage_progress("validation", "running", phase="oos", fold=1)
    callback.assert_called_once_with(
        {"stage": "validation", "status": "running", "phase": "oos", "fold": 1}
    )
    trainer._progress_cb = None
    trainer._emit_stage_progress("validation", "done")
    assert callback.call_count == 1


@pytest.mark.parametrize("boundary", ["start", "last_epoch"])
def test_final_fit_cancellation_aborts_before_publication(monkeypatch, tmp_path, boundary):
    import threading
    from unittest.mock import Mock

    import numpy as np

    from nexus_scalp.training.walk_forward_trainer import TrainingCancelledError, WalkForwardTrainer

    cancel = threading.Event()
    trainer = WalkForwardTrainer(
        num_folds=1,
        epochs_per_fold=1,
        backend="cpu",
        smoke=True,
        label_origin="CLEAN_HISTORICAL",
        cancel_event=cancel,
        artifact_save_path=tmp_path / "candidate" / "model.pt",
    )
    cols = list(trainer.feature_schema.columns)
    rng = np.random.default_rng(42)
    frame = pl.DataFrame({c: rng.normal(size=200) for c in cols}).with_columns(
        pl.Series("label", [i % 3 for i in range(200)])
    )
    monkeypatch.setattr(trainer, "bind_dataset_provenance_for_frame", lambda *a: None)
    published = Mock()
    monkeypatch.setattr(trainer, "_publish_candidate_bundle", published)

    def on_progress(event):
        if event.get("phase") == "final_fit" and event.get("status") == "running":
            if boundary == "start" or "loss" in event:
                cancel.set()

    trainer._progress_cb = on_progress
    with pytest.raises(TrainingCancelledError):
        trainer.train_and_validate(frame, cols)
    published.assert_not_called()
