"""CHG-0057: behavioral model-health gate (evolution of CHECK-MDL-02).

- Degenerate fresh-inits FAIL (v1.0.0); trained candidates PASS.
- Live 70d_liquidity at least does not block on fresh-noise.
"""

from __future__ import annotations

import pytest

try:
    import torch  # type: ignore[import-not-found]
except Exception:  # pragma: no cover
    pytest.skip("torch missing", allow_module_level=True)


def test_behavioral_gate_fails_fresh_init() -> None:
    from nexus_scalp.model_lifecycle.integrity import check_model_behavioral_health

    healthy, detail, _ = check_model_behavioral_health(
        r"C:/Users/Capsizer/source/repos/NexusTradingForexBot/artifacts/models/scalp/XAUUSD/v1.0.0/model.pt",
        feature_dimension=50,
    )
    assert healthy is False
    assert detail.startswith("DEGENERATE:")


def test_behavioral_gate_passes_live_70d() -> None:
    from nexus_scalp.model_lifecycle.integrity import check_model_behavioral_health

    healthy, detail, metrics = check_model_behavioral_health(
        r"C:/Users/Capsizer/source/repos/NexusTradingForexBot/artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt",
        feature_dimension=70,
    )
    assert healthy is True, "live 70d champion must pass behavioral health"
    assert metrics["logit_std_mean"] > 0.15
