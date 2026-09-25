"""Unit tests for the MLFix-T5 behavioral model health gate."""

from __future__ import annotations

from pathlib import Path

import pytest

torch = pytest.importorskip("torch")

from nexus_scalp.model_lifecycle.integrity import (  # noqa: E402
    check_model_behavioral_health,
    detect_untrained_fresh_init,
)
from nexus_scalp.models.scalp_net import ScalpNet  # noqa: E402


def test_behavioral_health_rejects_fresh_init(tmp_path: Path) -> None:
    """Byte-equal fresh init must FAIL the behavioral gate."""
    p = tmp_path / "fresh.pt"
    torch.manual_seed(42)
    m = ScalpNet(num_features=70, num_classes=4)
    torch.save({k: v.clone() for k, v in m.state_dict().items()}, p)
    healthy, detail, metrics = check_model_behavioral_health(p, feature_dimension=70)
    assert healthy is False
    assert "DEGENERATE:" in detail


def test_behavioral_health_provenance_probe_champion():
    """CHG-0057 champion at re-persist (logit std 0.40) must PASS the behavioral gate."""
    from pathlib import Path

    champ = Path("artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt")
    if not champ.exists():
        pytest.skip("Champion artifact not present")
    healthy, detail, metrics = check_model_behavioral_health(champ, feature_dimension=70)
    assert healthy is True, f"Champion must pass behavioral gate: {detail}"
