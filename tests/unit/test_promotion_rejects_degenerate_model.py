"""Unit tests for the MLFix-T5 promotion gate that rejects degenerate models."""

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


def test_promotion_rejects_fresh_init(tmp_path: Path) -> None:
    p = tmp_path / "fresh.pt"
    _make_fresh(p)
    healthy, detail, metrics = check_model_behavioral_health(p, feature_dimension=70)
    assert healthy is False, "Promotion must reject degenerate artifact"


def test_promotion_accepts_trained_champion():
    """Champion artifact must pass the behavioral gate."""
    champ = Path("artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt")
    if not champ.exists():
        pytest.skip("Champion artifact not present")
    healthy, detail, metrics = check_model_behavioral_health(champ, feature_dimension=70)
    assert healthy is True, f"Promotion must accept champion: {detail}"


def test_thresholds_hold_between_fresh_and_trained(tmp_path: Path) -> None:
    """Threshold must discriminate fresh init from a real-training-step model."""
    fresh_p = tmp_path / "fresh.pt"
    _make_fresh(fresh_p)
    trained_p = tmp_path / "trained.pt"

    # Simulate an actual training step: real gradient descent on a small
    # supervised batch (multi-epoch AdamW), which is what "trained" means.
    torch.manual_seed(42)
    m = ScalpNet(num_features=70, num_classes=4)
    m.train()
    torch.manual_seed(999)
    X = torch.randn(256, 70)
    y = torch.randint(0, 4, (256,))
    opt = torch.optim.AdamW(m.parameters(), lr=1e-3)
    for _ in range(30):
        opt.zero_grad()
        loss = torch.nn.functional.cross_entropy(m(X, return_logits=True), y)
        loss.backward()
        opt.step()
    m.eval()
    torch.save({k: v.clone() for k, v in m.state_dict().items()}, trained_p)

    fresh_health, fresh_detail, _ = check_model_behavioral_health(fresh_p, feature_dimension=70)
    trained_health, trained_detail, _ = check_model_behavioral_health(
        trained_p, feature_dimension=70
    )
    assert fresh_health is False, "fresh init must fail"
    assert trained_health is True, f"trained model must pass (detail={trained_detail})"
