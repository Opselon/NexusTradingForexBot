"""Unit tests for the MLFix-T5 directional sensitivity metric."""

from __future__ import annotations

from pathlib import Path

import pytest

torch = pytest.importorskip("torch")

from nexus_scalp.model_lifecycle.integrity import check_model_behavioral_health  # noqa: E402
from nexus_scalp.models.scalp_net import ScalpNet  # noqa: E402


def _make_fresh(path: Path, dim: int = 70) -> None:
    torch.manual_seed(42)
    m = ScalpNet(num_features=dim, num_classes=4)
    torch.save({k: v.clone() for k, v in m.state_dict().items()}, path)


def test_fresh_init_low_sensitivity(tmp_path: Path) -> None:
    """Fresh init should show near-zero sensitivity (degenerate)."""
    p = tmp_path / "fresh.pt"
    _make_fresh(p)
    healthy, detail, metrics = check_model_behavioral_health(p, feature_dimension=70)
    assert healthy is False
    assert "sensitivity" in detail.lower()


def test_trained_champion_has_sensitivity():
    """Champion artifact must show positive sensitivity on the ±3 sweep."""
    from pathlib import Path

    champ = Path("artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt")
    if not champ.exists():
        pytest.skip("Champion artifact not present")
    healthy, detail, metrics = check_model_behavioral_health(champ, feature_dimension=70)
    assert healthy is True
    assert metrics["margin_sensitivity"] > 0.02
