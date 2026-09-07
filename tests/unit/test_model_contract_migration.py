"""MODEL_CLASS_CONTRACT v1 migration tests (P0 phase 2 — dead WAIT output).

Mission acceptance (Phase 2C):

* NEW models emit exactly the canonical trained class count (3).
* The legacy 4-wide geometry is an explicit opt-in (compat only).
* Training labels match output dimension (labeler never produces WAIT).
* Loaders validate declared width; invalid dimensions fail explicitly.
* 3-wide inference needs no dead-class mask (mask is a no-op for 3-wide).
"""

from __future__ import annotations

import pytest
import torch

from nexus_scalp.model_lifecycle.model_class_contract import (
    LEGACY_HEAD_CLASSES,
    TRAINED_CLASS_COUNT,
    mask_wait_logit,
    masked_softmax,
)
from nexus_scalp.model_generation.model_factory import ModelFactory
from nexus_scalp.models.scalp_net import ScalpNet


def test_fresh_scalpnet_defaults_to_trained_class_count() -> None:
    m = ScalpNet(num_features=70)
    assert m.num_classes == TRAINED_CLASS_COUNT == 3


def test_fresh_scalpnet_forward_is_three_wide() -> None:
    m = ScalpNet(num_features=50)
    m.eval()
    with torch.inference_mode():
        out = m(torch.randn(2, 50))
    assert out.shape == (2, TRAINED_CLASS_COUNT)


def test_legacy_four_head_is_explicit_opt_in() -> None:
    m = ScalpNet(num_features=50, num_classes=LEGACY_HEAD_CLASSES)
    assert m.num_classes == LEGACY_HEAD_CLASSES == 4
    m.eval()
    with torch.inference_mode():
        out = m(torch.randn(2, 50))
    assert out.shape == (2, 4)
    # masked_softmax must strip the dead WAIT mass for legacy geometry
    with torch.inference_mode():
        probs = masked_softmax(out)
    assert probs.shape == (2, 4)
    assert float(probs[:, 3].abs().max()) < 1e-6


def test_factory_legacy_baseline_builds_declared_head() -> None:
    m = ModelFactory(feature_schema_id="scalp_v1").build(
        "LEGACY_SCALPNET_V1", parameters={"input_dim": 50}
    )
    assert m.num_classes == TRAINED_CLASS_COUNT


def test_factory_explicit_legacy_width_opt_in() -> None:
    m = ModelFactory(feature_schema_id="scalp_v1").build(
        "LEGACY_SCALPNET_V1",
        num_classes=3,
        parameters={"input_dim": 50, "num_classes": LEGACY_HEAD_CLASSES},
    )
    assert m.num_classes == LEGACY_HEAD_CLASSES


def test_invalid_head_width_fails_explicitly() -> None:
    with pytest.raises(ValueError, match="MODEL_CLASS_CONTRACT VIOLATION"):
        ScalpNet(num_features=50, num_classes=1)


def test_mask_is_noop_for_trained_class_width() -> None:
    logits = torch.randn(5, TRAINED_CLASS_COUNT)
    masked = mask_wait_logit(logits)
    assert masked is logits or torch.equal(masked, logits)


def test_trained_probs_slice_strips_wait() -> None:
    from nexus_scalp.model_lifecycle.model_class_contract import trained_class_probs

    probs4 = torch.softmax(torch.randn(3, 4), dim=-1)
    probs3 = trained_class_probs(probs4)
    assert probs3.shape == (3, TRAINED_CLASS_COUNT)
    probs3_direct = trained_class_probs(probs3)
    assert probs3_direct.shape == (3, TRAINED_CLASS_COUNT)
