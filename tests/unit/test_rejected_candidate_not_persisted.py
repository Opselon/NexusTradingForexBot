"""BUG-236 / TASK MLFIX-T3 — a rejected / zero-improvement / gate-failed
fine-tune candidate can NEVER be persisted as a new model.

Layer under test (trainer/lifecycle, live_engine-free):
- ``nexus_scalp.model_lifecycle.persist_decision`` — the explicit
  ``should_persist_candidate`` decision API + engine-side
  ``should_persist_model`` reader.
- ``WalkForwardTrainer.fine_tune_online`` — computes and attaches the
  PersistDecision on EVERY outcome; writes NO artifact on persist=False
  (sha256 + mtime of any pre-existing champion stay untouched, no new
  artifact files appear); the accepted path still persists normally.

The LiveEngine wiring (consuming the attached decision to skip
``_save_model_weights_atomic`` + provenance) is delivered as an exact diff in
the MLFIX-T3 report — live_engine.py is foreign-owned this wave.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import polars as pl
import pytest
import torch

from nexus_scalp.features.scalp_features import FEATURE_NAMES
from nexus_scalp.model_lifecycle.persist_decision import (
    REASON_ACCEPTED,
    REASON_HEALTH_GATE_FAILED,
    REASON_NOT_TRAINED_INSUFFICIENT_LABELS,
    REASON_NOT_TRAINED_INSUFFICIENT_ROWS,
    REASON_QUALITY_GATE_FAILED,
    REASON_ZERO_IMPROVEMENT,
    PersistDecision,
    attach_decision,
    decision_of,
    should_persist_candidate,
    should_persist_model,
)
from nexus_scalp.models.scalp_net import ScalpNet
from nexus_scalp.training.walk_forward_trainer import WalkForwardTrainer


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _labeled_frame(n_rows: int = 120, seed: int = 11) -> pl.DataFrame:
    rng = np.random.RandomState(seed)
    labels = ["NO_TRADE", "BUY_MARKET", "SELL_MARKET"]
    data: dict[str, list] = {name: rng.randn(n_rows).tolist() for name in FEATURE_NAMES}
    data["label"] = [labels[i % 3] for i in range(n_rows)]
    data["label_evaluated"] = [True] * n_rows
    data["is_purged"] = [False] * n_rows
    return pl.DataFrame(data)


def _tiny_frame(n_rows: int = 40) -> pl.DataFrame:
    """Below the (purge_len + 30) floor for max_holding_bars=5."""
    return _labeled_frame(n_rows)


def _seed_champion(trainer: WalkForwardTrainer) -> tuple[ScalpNet, str, float]:
    """Write a realistic champion artifact so the rejected-run assertions can
    prove the file is byte-identical (sha256) and untouched (mtime) after a
    rejected fine-tune."""
    base = ScalpNet(num_features=50, num_classes=4)
    trainer._save_checkpoint(base)
    champion_path = trainer.artifact_path
    return base, _sha256(champion_path), champion_path.stat().st_mtime


# ============================================================================
# 1. The decision API itself enforces the invariant.
# ============================================================================


@pytest.mark.parametrize(
    ("kwargs", "expected_reason"),
    [
        ({"trained": False, "insufficient_rows": True}, REASON_NOT_TRAINED_INSUFFICIENT_ROWS),
        ({"trained": False, "insufficient_labels": True}, REASON_NOT_TRAINED_INSUFFICIENT_LABELS),
        ({"trained": True, "zero_improvement": True}, REASON_ZERO_IMPROVEMENT),
        ({"trained": True, "quality_gate_passed": False}, REASON_QUALITY_GATE_FAILED),
        ({"trained": True, "health_ok": False}, REASON_HEALTH_GATE_FAILED),
        # Fail-closed: accepted=False with no specific flags still cannot persist.
        ({"trained": True}, REASON_QUALITY_GATE_FAILED),
    ],
)
def test_persist_decision_rejects_all_gate_failures(kwargs, expected_reason):
    decision = should_persist_candidate(**kwargs)
    assert decision.persist is False
    assert decision.reason == expected_reason
    assert decision.detail, "every rejection must record a human-readable reason"
    assert decision.model_dump()["persist"] is False


def test_persist_decision_accepts_only_full_acceptance():
    decision = should_persist_candidate(
        trained=True,
        quality_gate_passed=True,
        health_ok=True,
        accepted=True,
    )
    assert decision.persist is True
    assert decision.reason == REASON_ACCEPTED
    assert should_persist_model(attach_decision(ScalpNet(num_features=50), decision)) is True


def test_untagged_model_is_fail_closed_for_engine_guard():
    """No PersistDecision attached => the engine-side guard must refuse to
    persist (never trust an untagged model as an accepted replacement)."""
    assert should_persist_model(ScalpNet(num_features=50)) is False


# ============================================================================
# 2. Zero-improvement run: champion artifact is byte-identical, no new files.
# ============================================================================


def test_zero_improvement_rejected_candidate_never_persists(tmp_path, monkeypatch):
    """The BUG-228 trigger (early stop restores baseline) must now ALSO carry
    persist=False and must not touch the pre-existing champion in any way."""
    from unittest.mock import MagicMock

    import nexus_scalp.training.walk_forward_trainer as wf_module

    trainer = WalkForwardTrainer(
        num_folds=3,
        epochs_per_fold=1,
        min_rows_per_train_split=10,
        min_rows_per_test_split=5,
        artifact_save_path=tmp_path / "wf_bug236" / "model.pt",
    )
    base_model, sha_before, mtime_before = _seed_champion(trainer)
    scaler_path = trainer.artifact_path.with_suffix(".scaler.npz")
    trainer._save_scaler(trainer._fit_scaler(np.zeros((1, 50), dtype=np.float32)))
    scaler_sha_before = _sha256(scaler_path)
    artifact_dir = trainer.artifact_path.parent
    snapshot_before = {p.name: p.stat().st_mtime for p in artifact_dir.iterdir()}

    mock_logger = MagicMock()
    monkeypatch.setattr(wf_module, "logger", mock_logger)

    returned = trainer.fine_tune_online(
        live_model=base_model,
        recent_df=_labeled_frame(120),
        feature_cols=FEATURE_NAMES,
        # epochs=3 + divergent lr => early stop with best_state == baseline
        # (the exact zero-improvement condition).
        epochs=3,
        learning_rate=1.0,
        max_holding_bars=5,
        verify_health=True,
    )

    # Decision attached, explicit, persist=False with recorded reason.
    decision = decision_of(returned)
    assert isinstance(decision, PersistDecision)
    assert decision.persist is False
    assert decision.reason == REASON_ZERO_IMPROVEMENT
    # Legacy flags stay in sync for the engine guard.
    assert getattr(returned, "_finetune_accepted", True) is False
    assert getattr(returned, "_finetune_zero_improvement", False) is True

    # model.pt sha256 UNCHANGED and mtime UNCHANGED (no atomic re-save).
    assert _sha256(trainer.artifact_path) == sha_before
    assert trainer.artifact_path.stat().st_mtime == mtime_before
    # Scaler artifact likewise untouched by the rejected run.
    assert _sha256(scaler_path) == scaler_sha_before

    # No NEW artifact files appeared (no .tmp leftovers, no meta writes).
    snapshot_after = {p.name: p.stat().st_mtime for p in artifact_dir.iterdir()}
    assert set(snapshot_after) == set(snapshot_before)
    for name, mtime in snapshot_before.items():
        assert snapshot_after[name] == mtime, f"{name} was rewritten on a rejected run"


def test_quality_gate_rejection_restores_baseline_and_never_persists(tmp_path):
    """A candidate that TRAINED but failed the quality gate: weights roll back
    to baseline in memory, and nothing on disk is written or modified."""
    trainer = WalkForwardTrainer(
        num_folds=3,
        epochs_per_fold=1,
        min_rows_per_train_split=10,
        min_rows_per_test_split=5,
        artifact_save_path=tmp_path / "wf_reject" / "model.pt",
    )
    base_model, sha_before, mtime_before = _seed_champion(trainer)
    artifact_dir = trainer.artifact_path.parent

    # Degenerate buffer (single target class in the val window) forces the
    # gate's degenerate-buffer rejection even if accuracy looks fine.
    df = _labeled_frame(120)
    df = df.with_columns(pl.lit("NO_TRADE").alias("label"))
    returned = trainer.fine_tune_online(
        live_model=base_model,
        recent_df=df,
        feature_cols=FEATURE_NAMES,
        epochs=1,
        learning_rate=1e-3,
        max_holding_bars=5,
        verify_health=True,
    )

    decision = decision_of(returned)
    assert decision is not None and decision.persist is False
    assert decision.reason in (REASON_QUALITY_GATE_FAILED, REASON_ZERO_IMPROVEMENT)
    # Returned weights ARE the baseline (no half-trained state escapes).
    for k, v in base_model.state_dict().items():
        assert torch.equal(returned.state_dict()[k], v.cpu()), (
            f"rejected run leaked non-baseline weight {k}"
        )
    # Disk untouched.
    assert _sha256(trainer.artifact_path) == sha_before
    assert trainer.artifact_path.stat().st_mtime == mtime_before
    assert sorted(p.name for p in artifact_dir.iterdir()) == [
        "model.pt",
        "model.scaler.npz",
    ]


def test_insufficient_buffer_never_persists(tmp_path):
    """Early exits (too few rows / too few labels) also carry persist=False
    and leave the artifact untouched."""
    trainer = WalkForwardTrainer(
        num_folds=3,
        epochs_per_fold=1,
        min_rows_per_train_split=10,
        min_rows_per_test_split=5,
        artifact_save_path=tmp_path / "wf_tiny" / "model.pt",
    )
    base_model, sha_before, mtime_before = _seed_champion(trainer)
    artifact_dir = trainer.artifact_path.parent

    returned = trainer.fine_tune_online(
        live_model=base_model,
        recent_df=_tiny_frame(30),
        feature_cols=FEATURE_NAMES,
        epochs=1,
        learning_rate=1e-3,
        max_holding_bars=5,
        verify_health=True,
    )
    decision = decision_of(returned)
    assert decision is not None and decision.persist is False
    assert decision.reason == REASON_NOT_TRAINED_INSUFFICIENT_ROWS
    assert _sha256(trainer.artifact_path) == sha_before
    assert trainer.artifact_path.stat().st_mtime == mtime_before
    # Only the pre-seeded champion exists; no scaler/meta/tmp written.
    assert [p.name for p in artifact_dir.iterdir()] == ["model.pt"]


# ============================================================================
# 3. The accepted path still persists normally (guard must not over-block).
# ============================================================================


def test_accepted_candidate_still_persists(tmp_path):
    """verify_health=False (documented trainer-authoritative accept) must
    still write the checkpoint + scaler and attach persist=True."""
    trainer = WalkForwardTrainer(
        num_folds=3,
        epochs_per_fold=1,
        min_rows_per_train_split=10,
        min_rows_per_test_split=5,
        artifact_save_path=tmp_path / "wf_accept" / "model.pt",
    )
    base_model = ScalpNet(num_features=50, num_classes=4)
    returned = trainer.fine_tune_online(
        live_model=base_model,
        recent_df=_labeled_frame(100, seed=7),
        feature_cols=FEATURE_NAMES,
        epochs=2,
        learning_rate=1e-3,
        max_holding_bars=5,
        verify_health=False,
    )
    decision = decision_of(returned)
    assert decision is not None and decision.persist is True
    assert decision.reason == REASON_ACCEPTED
    assert trainer.artifact_path.exists(), "accepted run must persist the checkpoint"
    assert trainer.artifact_path.with_suffix(".scaler.npz").exists()
    assert should_persist_model(returned) is True
    saved = torch.load(trainer.artifact_path)
    assert "classifier.weight" in saved


# ============================================================================
# 4. Registry conventions: decision payload is audit-ready (REJECTED row
#    inputs) but never fabricates a replacement claim.
# ============================================================================


def test_decision_payload_is_audit_ready():
    decision = should_persist_candidate(
        trained=True,
        zero_improvement=True,
        metrics={"val_acc": 0.667, "baseline_acc": 0.667},
    )
    dump = decision.model_dump()
    assert dump["persist"] is False
    assert dump["reason"] == REASON_ZERO_IMPROVEMENT
    assert json.dumps(dump)  # JSON-serializable for gate_summary/audit rows
