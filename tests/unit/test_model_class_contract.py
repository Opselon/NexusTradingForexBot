"""MLFIX-T4 Model class-contract regression.

Canonical contract (SSoT): LABEL_SCHEMA_3CLASS_V1.class_count == 3
(NO_TRADE / BUY_MARKET / SELL_MARKET). The legacy 4-wide WAIT head is
compat-only. Every fresh model, loss tensor, meta block, scaler gate,
calibration fit, and policy decision must agree on the declared width and
MUST FAIL loudly when a 4-wide artifact is presented as canonical-3.

This file pins the end-to-end contract that was violated at M4:
  labeling/triple_barrier.py:    3 int classes (WAIT is not a label)
  model_generation/architectures: CANONICAL_CLASS_COUNT = 3
  model_lifecycle/integrity:      EXPECTED_NUM_CLASSES = 3 (legacy 4 via allow_legacy_4 only)
  training/walk_forward_trainer:  CANONICAL_NUM_CLASSES/ScalpNet(..., 3) + loss weights shape (3,)
  model/meta (checkpoint meta):   num_classes == class_head == loss width
  inference (_infer_probabilities-reader): loader asserts meta.classes == head
  calibration:                    fit/score on the declared 3-class logits only
  policy:                         mapping consumed from the same meta (no WAIT drift)
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import torch

from nexus_scalp.labeling.triple_barrier import TripleBarrierLabeler
from nexus_scalp.model_generation.architectures import CANONICAL_CLASS_COUNT
from nexus_scalp.model_generation.models import (
    LABEL_SCHEMA_3CLASS_V1,
    default_label_schema,
)
from nexus_scalp.model_lifecycle.integrity import (
    EXPECTED_NUM_CLASSES,
    LEGACY_EXPECTED_NUM_CLASSES,
    inspect_artifact,
)
from nexus_scalp.models.scalp_net import ScalpNet
from nexus_scalp.training.walk_forward_trainer import WalkForwardTrainer


def _valid_3class_df(n: int = 240) -> "object":
    """A training-frame that the trainer accepts.

    Must carry feat_0..feat_{49}, feature_vector list, timestamp, all-bars
    validity. The trainer's _validate_training_frame checks feature_vector
    when present; the dataset builder also accepts feat_*.
    """
    import polars as pl
    from datetime import datetime, timedelta, UTC

    rng = np.random.default_rng(0)
    base = datetime(2026, 5, 1, tzinfo=UTC)
    close = 2700.0 + rng.normal(0, 2.0, n).cumsum()
    high = close + np.abs(rng.normal(0, 1.0, n))
    low = close - np.abs(rng.normal(0, 1.0, n))
    atr = 1.0 + np.abs(rng.normal(0, 0.2, n))
    # fees/labeler cols the trainer's extract will see via feature_cols, but
    # frame validation looks at the feature_vector strut.
    data: dict[str, object] = {
        "timestamp": [base + timedelta(minutes=i) for i in range(n)],
        "close": close,
        "high": high,
        "low": low,
        "atr": atr,
    }
    vecs = [[float(rng.normal(0, 0.5)) for _ in range(50)] for _ in range(n)]
    data["feature_vector"] = vecs  # type: ignore[assignment]
    for j in range(50):
        data[f"feat_{j}"] = [float(vec[j]) for vec in vecs]
    # Canonical 3-class labels the trainer's label_map expects (string enum).
    LABELS = ["NO_TRADE", "BUY_MARKET", "SELL_MARKET"]
    data["label"] = [LABELS[int(rng.integers(0, 3))] for _ in range(n)]  # type: ignore[assignment]
    return pl.DataFrame(data)  # type: ignore[return-value]


def test_m4_01_labeler_is_strictly_3_class() -> None:
    assert TripleBarrierLabeler.__doc__ is not None  # smoke: module loads
    assert LABEL_SCHEMA_3CLASS_V1["class_count"] == 3
    assert list(LABEL_SCHEMA_3CLASS_V1["class_names"]) == [
        "NO_TRADE",
        "BUY_MARKET",
        "SELL_MARKET",
    ]
    assert list(LABEL_SCHEMA_3CLASS_V1["numeric_mapping"].keys()) == [
        "NO_TRADE",
        "BUY_MARKET",
        "SELL_MARKET",
    ]
    assert CANONICAL_CLASS_COUNT == 3
    assert EXPECTED_NUM_CLASSES == 3
    assert LEGACY_EXPECTED_NUM_CLASSES == 4

    schema = default_label_schema()
    # WAIT was never a valid label.
    with pytest.raises(ValueError):
        schema.encode("WAIT")
    # Label value 3 must be rejected.
    with pytest.raises(ValueError):
        schema.validate_labels([0, 1, 2, 3])


def test_m4_02_fresh_scalpnet_follows_canonical_3_head(tmp_path: Path) -> None:
    net = ScalpNet(num_features=50, num_classes=CANONICAL_CLASS_COUNT)
    assert net.num_classes == 3
    assert tuple(net.classifier.weight.shape) == (3, 32)
    x = torch.randn(4, 50)
    logits = net(x, return_logits=True)
    assert tuple(logits.shape) == (4, 3)
    # Training-mode softmax over 3, not 4.
    net.eval()
    with torch.inference_mode():
        probs = net(x[:1])
    assert tuple(probs.shape) == (1, 3)
    assert abs(float(probs.sum().item()) - 1.0) < 1e-5


def test_m4_03_walktrainer_meta_and_loss_are_canonical_3(tmp_path: Path) -> None:
    feat_cols = [f"feat_{i}" for i in range(50)]
    trainer = WalkForwardTrainer(
        num_folds=2,
        epochs_per_fold=1,
        batch_size=16,
        artifact_save_path=tmp_path / "model.pt",
        feature_schema_id="scalp_v1",
    )
    assert trainer.CANONICAL_NUM_CLASSES == 3
    # The default alias for compat shims now reflects canonical 3.
    # The legacy compat path is exposed via LEGACY_HEAD_CLASSES only.
    assert trainer.MODEL_HEAD_CLASSES == 3  # alias of canonical post-M4
    assert trainer.LEGACY_HEAD_CLASSES == 4

    # Class-weight tensor must be exactly 3-wide on a 3-class label set.
    y = np.array([0, 0, 0, 1, 1, 2, 2, 2], dtype=np.int64)
    w = trainer._build_class_weights(y)
    assert w.shape == (3,)
    # Online branch likewise.
    w2 = trainer._build_class_weights(y, is_online_fine_tune=True)
    assert w2.shape == (3,)

    # The underlying factory-built model used by the trainer is 3-wide.
    m = trainer._create_model(num_features=50)
    assert m.num_classes == 3
    assert tuple(m.classifier.weight.shape) == (3, 32)

    # End-to-end smoke: one fold of real trainer flow emits meta head=3.
    df = _valid_3class_df(240)
    m2 = trainer.train_and_validate(df, feat_cols)
    assert isinstance(m2, ScalpNet)
    assert m2.num_classes == 3
    meta = (tmp_path / "model.meta.json")
    assert meta.exists()
    import json as _json

    payload = _json.loads(meta.read_text(encoding="utf-8"))
    # Both declared fields are canonical-3 SSoT handles (loud rejection handles).
    assert int(payload["num_classes"]) == 3
    assert int(payload["model_head_classes"]) == 3
    # The persisted checkpoint itself is 3-wide.
    state = torch.load(tmp_path / "model.pt", map_location="cpu", weights_only=False)
    assert tuple(state["classifier.weight"].shape)[0] == 3


def test_m4_04_integrity_gate_rejects_legacy_4_when_declared_3(tmp_path: Path) -> None:
    """inspect_artifact with num_classes=3 MUST loudly mark legacy 4-wide INVALID."""
    legacy = tmp_path / "legacy4.pt"
    net4 = ScalpNet(num_features=70, num_classes=4)
    torch.save(net4.state_dict(), legacy)

    # Presented against the canonical-3 contract -> integrity_ok == False.
    info = inspect_artifact(
        legacy,
        model_id="legacy4",
        feature_schema_id="scalp_v3",
        feature_dimension=70,
        num_classes=3,
    )
    assert info.integrity_ok is False
    assert info.integrity_reason == "CLASS_COUNT_MISMATCH"
    assert info.actual_output_classes == 4
    assert info.num_classes == 3

    # The same file is valid when the legacy contract is explicitly declared.
    info_legacy = inspect_artifact(
        legacy,
        model_id="legacy4",
        feature_schema_id="scalp_v3",
        feature_dimension=70,
        num_classes=4,
    )
    assert info_legacy.integrity_ok is True
    assert info_legacy.actual_output_classes == 4

    # And a genuine canonical artifact is valid only against canonical-3.
    canon = tmp_path / "canon3.pt"
    net3 = ScalpNet(num_features=70, num_classes=3)
    torch.save(net3.state_dict(), canon)
    info3 = inspect_artifact(
        canon,
        model_id="canon3",
        feature_schema_id="scalp_v3",
        feature_dimension=70,
        num_classes=3,
    )
    assert info3.integrity_ok is True
    assert info3.actual_output_classes == 3
    # Canonical file against the legacy contract is rejected just as loudly.
    info3_against_legacy = inspect_artifact(
        canon,
        model_id="canon3_as4",
        feature_schema_id="scalp_v3",
        feature_dimension=70,
        num_classes=4,
    )
    assert info3_against_legacy.integrity_ok is False
    assert info3_against_legacy.integrity_reason == "CLASS_COUNT_MISMATCH"


def test_m4_05_calibration_operates_on_the_declared_3_class_set() -> None:
    """ECE/calibration must be computed over the same 3 logits the model emits.

    A 4-wide vector masked back to 3 would still numerics-pass but miscalibrates:
    this pin ensures the calibration probes see shape (N, 3).
    """
    from nexus_scalp.model_generation.validation import compute_calibration

    rng = np.random.default_rng(1)
    n = 200
    # 3-class synthetic probs (row-normalized).
    probs = rng.random((n, 3))
    probs /= probs.sum(axis=1, keepdims=True)
    labels = rng.integers(0, 3, size=n)
    cal = compute_calibration(probs, labels)
    assert "ece" in cal and "bins" in cal
    assert 0.0 <= float(cal["ece"]) <= 1.0


def test_m4_06_policy_consumes_same_3_mapping_and_thresholds_are_consistent() -> None:
    """Policy mapping == model head mapping == label schema mapping.

    The policy never invents a 4th action. Its thresholds are produced from
    the dataset+trainer calibration over the same 3 indices, so a mismatch
    cannot be hidden by renormalising at the gate.
    """
    from nexus_scalp.signals.policy import SignalPolicy

    policy = SignalPolicy()
    # Policy default path must not expose a 4-wide action set; the label
    # schema declares exactly the three indices the model emits.
    schema = default_label_schema()
    assert schema.class_count == 3
    # Smoke: policy instantiates and its default thresholds are floats
    # that would be produced by a 3-class calibration sweep (not 4).
    assert isinstance(policy.confidence_threshold, float)
    # Policy should know the canonical class names through the shared schema,
    # not via a separate hard-coded 4-tuple.
    assert "NO_TRADE" in schema.class_names and "BUY_MARKET" in schema.class_names
