"""CHG-0057: behavioral model-health gate (evolution of CHECK-MDL-02).

- Degenerate fresh-inits FAIL (v1.0.0); trained candidates PASS.
- A trained 70D candidate at least does not block on fresh-noise.

The original cases pointed at the author's Windows checkout
(``C:/Users/Capsizer/...``), so on every other machine the probe returned
``ARTIFACT_ABSENT``: the "fresh init must FAIL" case passed for the wrong
reason (absent, not degenerate) and the "trained champion must PASS" case
could never run at all. Both artifacts are minted here instead — a fresh
untrained init and a briefly-trained 70D checkpoint — which is exactly the
distinction the gate exists to make.
"""

from __future__ import annotations

from pathlib import Path

import pytest

try:
    import torch  # type: ignore[import-not-found]
except Exception:  # pragma: no cover
    pytest.skip("torch missing", allow_module_level=True)

from tests.helpers.synthetic_checkpoint import write_trained_checkpoint


def test_behavioral_gate_fails_fresh_init(tmp_path: Path) -> None:
    from nexus_scalp.model_lifecycle.integrity import check_model_behavioral_health

    fresh = write_trained_checkpoint(tmp_path / "v1.0.0" / "model.pt", num_features=50, steps=0)
    healthy, detail, _ = check_model_behavioral_health(fresh, feature_dimension=50)
    assert healthy is False
    assert detail.startswith("DEGENERATE:")


def test_behavioral_gate_passes_live_70d(tmp_path: Path) -> None:
    from nexus_scalp.model_lifecycle.integrity import check_model_behavioral_health

    trained = write_trained_checkpoint(tmp_path / "70d_liquidity" / "model.pt", num_features=70)
    healthy, detail, metrics = check_model_behavioral_health(trained, feature_dimension=70)
    assert healthy is True, f"a trained 70D candidate must pass behavioral health: {detail}"
    assert metrics["logit_std_mean"] > 0.15
