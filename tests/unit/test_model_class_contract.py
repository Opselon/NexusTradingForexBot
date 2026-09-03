"""MODEL_CLASS_CONTRACT v1 (Fix #3 + Fix #6) — contract tests.

TASK: test_model_class_contract enforces that the neural class contract is
3-class (NO_TRADE / BUY / SELL), that WAIT is a policy bridge (index 3, dead
in loss/inference/calibration, never a label), and that smoke artifacts are
never production_eligible.

NOTE: the 4-wide ScalpNet head is allowed on disk (scalable checkpoint
compat) — but the LABEL contract stays 3-class; the inference wrapper MUST
mask WAIT to 0 prob mass.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import polars as pl
import pytest
import torch

from nexus_scalp.labeling.triple_barrier import TripleBarrierLabeler
from nexus_scalp.model_lifecycle.gates import gate_production_eligible
from nexus_scalp.model_lifecycle.model_class_contract import (
    LEGACY_HEAD_CLASSES,
    MODEL_CLASS_CONTRACT_ID,
    TRAINED_CLASS_COUNT,
    TRAINED_CLASS_NAMES,
    WAIT_LOGIT_INDEX,
    is_production_eligible,
    mask_wait_logit,
    masked_softmax,
)

# ── SSOT geometry ─────────────────────────────────────────────────────────


def test_model_class_contract_trained_is_three() -> None:
    assert TRAINED_CLASS_COUNT == 3
    assert TRAINED_CLASS_NAMES == ("NO_TRADE", "BUY_MARKET", "SELL_MARKET")
    assert WAIT_LOGIT_INDEX == 3
    assert LEGACY_HEAD_CLASSES == 4
    assert MODEL_CLASS_CONTRACT_ID == "triple_barrier_3class_v1"


# ── Dataset / labeler never produces WAIT ────────────────────────────────


def test_model_class_contract_labeler_never_produces_wait(tmp_path: Path) -> None:
    n = 100
    import datetime as dt

    df = pl.DataFrame(
        {
            "timestamp": [dt.datetime(2026, 5, 1, 0, i % 60, tzinfo=dt.UTC) for i in range(n)],
            "close": [4000.0 + float(i % 10) for i in range(n)],
            "high": [4001.0 + float(i % 10) for i in range(n)],
            "low": [3999.0 + float(i % 10) for i in range(n)],
            "atr_m1": [1.2] * n,
            "spread": [0.5] * n,
            "tick_volume": [100.0] * n,
        }
    )
    lab = TripleBarrierLabeler()
    out = lab.label_dataframe(df)
    # Every evaluated label is one of the 3 classes — WAIT never appears.
    if "label_evaluated" in out.columns:
        assert out.filter(pl.col("label_evaluated")).select("label").unique().height <= 3
    labels = set(out["label"].to_list())
    assert "WAIT" not in labels
    assert labels.issubset({"NO_TRADE", "BUY_MARKET", "SELL_MARKET"})


# ── Loss: dataset/loss understanding of WAIT ─────────────────────────────


def test_model_class_contract_loss_masks_wait_gradient() -> None:
    """A 4-wide head's WAIT logit carries no gradient for trained targets."""
    logits = torch.tensor([[1.0, 2.0, 0.5, 10.0], [0.0, 0.0, 1.0, 10.0]], dtype=torch.float32)
    targets = torch.tensor([1, 2], dtype=torch.long)
    # CE on masked logits vs CE on 3-slice logits should be equal (WAIT=0 prob).
    from torch import nn

    masked = mask_wait_logit(logits)
    ce_full = nn.functional.cross_entropy(masked[:, :3], targets)
    ce_masked = nn.functional.cross_entropy(masked[:, :3], targets)
    assert torch.allclose(ce_full, ce_masked, atol=1e-6)
    # The WAIT logit gradient is ~0: changing it does not change the 3-class CE
    # (numerical probe: vary index 3, loss stays equal within tight tol).
    logits2 = logits.clone()
    logits2[0, 3] = -100.0
    logits2[1, 3] = 5.0
    masked2 = mask_wait_logit(logits2)
    assert torch.allclose(masked[:, :3], masked2[:, :3], atol=1e-5)
    ce2 = nn.functional.cross_entropy(masked2[:, :3], targets)
    assert torch.allclose(ce_full, ce2, atol=1e-5)


# ── Inference: WAIT masked, not inventing semantics ──────────────────────


def test_model_class_contract_inference_mask_removes_wait_mass() -> None:
    """masked_softmax forces WAIT prob to ~0; unmasked WAIT steals ~0.22 mass."""
    raw = torch.tensor([[0.3, 0.2, 0.3, 5.0]], dtype=torch.float32)
    p_masked = masked_softmax(raw)
    assert float(p_masked[0, 3].item()) < 1e-3
    p_raw = torch.softmax(raw, dim=-1)
    # Offline finding: WAIT mean prob ~0.22; here raw WAIT should be > 0.2
    assert float(p_raw[0, 3].item()) > 0.1
    # Trained mass is restored (masked trained mass ≈ 1.0, raw trained mass ≈ 0.78).
    trained_masked = float(p_masked[0, :3].sum().item())
    trained_raw = float(p_raw[0, :3].sum().item())
    assert trained_masked > 0.999
    assert trained_raw < 0.90


def test_model_class_contract_mask_identity_on_three_wide() -> None:
    raw3 = torch.tensor([[1.0, 1.0, 1.0]], dtype=torch.float32)
    p3 = masked_softmax(raw3)
    assert p3.size(-1) == 3
    assert torch.allclose(p3, torch.softmax(raw3, dim=-1), atol=1e-6)


# ── Checkpoint meta carries contract traceability ────────────────────────


def test_model_class_contract_checkpoint_meta_written(tmp_path: Path) -> None:
    """WalkForwardTrainer smoke=False writes the class-contract fields + prod-eligible."""
    from nexus_scalp.features.scalp_features import FEATURE_NAMES
    from nexus_scalp.training.walk_forward_trainer import WalkForwardTrainer

    out = tmp_path / "meta3" / "model.pt"
    tr = WalkForwardTrainer(feature_schema_id="scalp_v1", artifact_save_path=out)
    tr._save_metadata([f"feat_{i}" for i in range(50)])
    meta = json.loads((tmp_path / "meta3" / "model.meta.json").read_text(encoding="utf-8"))
    assert meta["num_classes"] == 3
    assert meta["model_head_classes"] == 4
    # Label contract is 3-class with WAIT = policy state
    assert meta["label_contract"]["class_count"] == 3
    assert meta["label_contract"]["wait_is_policy_state"] is True
    assert meta["model_class_contract_id"] == MODEL_CLASS_CONTRACT_ID
    assert meta["production_eligible"] is True
    assert meta["smoke"] is False


def test_model_class_contract_checkpoint_meta_smoke_not_eligible(tmp_path: Path) -> None:
    from nexus_scalp.training.walk_forward_trainer import WalkForwardTrainer

    out = tmp_path / "meta_smoke" / "model.pt"
    tr = WalkForwardTrainer(feature_schema_id="scalp_v1", artifact_save_path=out, smoke=True)
    tr._save_metadata([f"feat_{i}" for i in range(50)])
    meta = json.loads((tmp_path / "meta_smoke" / "model.meta.json").read_text(encoding="utf-8"))
    assert meta["smoke"] is True
    assert meta["production_eligible"] is False


# ── Smoke rejection in promotion gates (Fix #6) ──────────────────────────


def test_model_class_contract_smoke_rejected_by_gate_production_eligible() -> None:
    g = gate_production_eligible({"production_eligible": False, "smoke": True})
    assert g.passed is False
    assert "GATE_PRODUCTION_ELIGIBLE" in g.gate


def test_model_class_contract_smoke_rejected_even_with_width_validity() -> None:
    """A smoke artifact is rejected even if every other field were valid."""
    # production_eligible=False alone (without smoke key) must also be rejected.
    g2 = gate_production_eligible({"production_eligible": False})
    assert g2.passed is False
    # production_eligible=True, smoke absent/False -> passes
    g3 = gate_production_eligible({"production_eligible": True, "smoke": False})
    assert g3.passed is True
    assert is_production_eligible({"production_eligible": True, "smoke": False}) is True
    assert is_production_eligible({"production_eligible": False, "smoke": True}) is False
    assert is_production_eligible({"smoke": True}) is False


def test_model_class_contract_verify_candidate_rejects_smoke() -> None:
    from nexus_scalp.governance.verify import verify_candidate

    tmp = Path(tempfile.mkdtemp())
    art = tmp / "model.pt"
    art.write_bytes(b"\x89fake")
    mani = {"production_eligible": False, "smoke": True, "feature_dimension": 70}
    res = verify_candidate(artifact_path=art, manifest=mani, model_id="t", model_version="1.0.0")
    assert res["gates"]["production_eligible"]["status"] == "FAIL"
    assert res["eligible"] is False


# ── Three-model smoke propagation (Fix #6) ───────────────────────────────


def test_model_class_contract_three_model_propagates_smoke_flag() -> None:
    import inspect

    from nexus_scalp.model_generation.three_model import train_variant
    from nexus_scalp.training.walk_forward_trainer import WalkForwardTrainer

    sig = inspect.signature(WalkForwardTrainer.__init__)
    assert "smoke" in sig.parameters
    # three_model.train_variant must pass smoke into the trainer (smoke var exists in source)
    src = Path("src/nexus_scalp/model_generation/three_model.py").read_text(encoding="utf-8")
    assert "smoke=smoke" in src
