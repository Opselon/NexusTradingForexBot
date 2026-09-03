"""BUG-225 regression guard: a checkpoint that is BYTE-IDENTICAL to the canonical
seed-42 fresh ScalpNet init must be DETECTED as untrained, and the forensic model
group must surface it as CRITICAL (UNTRAINED_CHAMPION_ARTIFACT).

Production evidence (2026-09-02..09-03): artifacts/models/scalp/XAUUSD/
70d_liquidity/model.pt — the LIVE champion serving decisions — was byte-identical
to a fresh ScalpNet(num_features=70, num_classes=4) mint under the runtime's
pinned torch.manual_seed(42). Every structural gate (BUG-141 width guard,
class-head probe, BUG-166 fingerprint match) passed: the corruption is semantic.
Consequence: near-uniform softmax outputs (~0.25 per class) → normalized
directional confidence capped at ~0.335 → the 0.40-0.60 confidence gate is
mathematically unreachable → permanent NO_TRADE / INSUFFICIENT_CONFIDENCE
(audit_signals: 98%+ NO_TRADE, confidence frozen at ~0.33).
"""

from __future__ import annotations

from pathlib import Path

import pytest

torch = pytest.importorskip("torch")

from nexus_scalp.forensics import checks as C  # noqa: E402
from nexus_scalp.forensics import checks_features  # noqa: E402
from nexus_scalp.forensics.models import HealthStatus  # noqa: E402
from nexus_scalp.model_lifecycle.integrity import detect_untrained_fresh_init  # noqa: E402
from nexus_scalp.models.scalp_net import ScalpNet  # noqa: E402


def _make_fresh_artifact(path: Path, dim: int = 70) -> None:
    """Mints the canonical seed-42 fresh ScalpNet exactly like the runtime does."""
    torch.manual_seed(42)
    model = ScalpNet(num_features=dim, num_classes=4)
    torch.save({k: v.clone() for k, v in model.state_dict().items()}, path)


def _make_trained_artifact(path: Path, dim: int = 70) -> None:
    """A 'trained' artifact: fresh init + a real gradient step (weights diverge)."""
    torch.manual_seed(42)
    model = ScalpNet(num_features=dim, num_classes=4)
    torch.manual_seed(999)  # any non-42 perturbation breaks the init identity
    with torch.no_grad():
        for v in model.state_dict().values():
            v.add_(torch.randn_like(v) * 1e-3)
    torch.save({k: v.clone() for k, v in model.state_dict().items()}, path)


def test_fresh_init_checkpoint_is_detected(tmp_path: Path) -> None:
    """The 2026-09-02 champion corruption class: fresh init passes all structural
    gates but IS byte-equal to the canonical mint — the canary must flag it."""
    p = tmp_path / "model.pt"
    _make_fresh_artifact(p)
    fresh, detail = detect_untrained_fresh_init(p, feature_dimension=70)
    assert fresh is True
    assert detail == "BYTE_EQUAL_TO_FRESH_INIT"


def test_trained_checkpoint_is_not_flagged(tmp_path: Path) -> None:
    """A checkpoint with ANY weight divergence from the fresh init must pass."""
    p = tmp_path / "model.pt"
    _make_trained_artifact(p)
    fresh, detail = detect_untrained_fresh_init(p, feature_dimension=70)
    assert fresh is False
    assert detail.startswith("DIVERGES_AT:")


def test_missing_artifact_is_not_a_fresh_verdict(tmp_path: Path) -> None:
    p = tmp_path / "absent.pt"
    fresh, detail = detect_untrained_fresh_init(p)
    assert fresh is False
    assert detail == "ARTIFACT_ABSENT"


def test_forensic_check_flags_fresh_init_champion(tmp_path: Path, monkeypatch) -> None:
    """CHECK-MDL-02 must go CRITICAL (UNTRAINED_CHAMPION_ARTIFACT) when the
    champion artifact on disk is the canonical fresh init."""
    p = tmp_path / "model.pt"
    _make_fresh_artifact(p)
    (tmp_path / "model.scaler.npz").write_bytes(b"x")  # scaler presence only
    monkeypatch.setattr(
        checks_features, "_champion_artifact_info", lambda: {"found": True, "path": str(p)}
    )
    result = C.check_model_semantic_health()
    assert result.status is HealthStatus.CRITICAL
    assert result.detail == "UNTRAINED_CHAMPION_ARTIFACT"


def test_forensic_check_passes_trained_champion(tmp_path: Path, monkeypatch) -> None:
    p = tmp_path / "model.pt"
    _make_trained_artifact(p)
    monkeypatch.setattr(
        checks_features, "_champion_artifact_info", lambda: {"found": True, "path": str(p)}
    )
    result = C.check_model_semantic_health()
    assert result.status is HealthStatus.PASS
    assert "trained" in result.evidence


def test_forensic_check_unknown_without_artifact(monkeypatch) -> None:
    monkeypatch.setattr(checks_features, "_champion_artifact_info", lambda: {"found": False})
    result = C.check_model_semantic_health()
    assert result.status is HealthStatus.UNKNOWN


def test_real_champion_artifact_is_trained() -> None:
    """The runtime-facing invariant: the LIVE champion checkpoint must NEVER be
    the canonical fresh init. This is the test that would have caught the
    2026-09-02 permanent-NO_TRADE incident on day one."""
    from nexus_scalp.release.paths import get_runtime_workspace

    p = (
        get_runtime_workspace()
        / "artifacts"
        / "models"
        / "scalp"
        / "XAUUSD"
        / "70d_liquidity"
        / "model.pt"
    )
    if not p.exists():
        pytest.skip("runtime champion artifact not present in this environment")
    fresh, detail = detect_untrained_fresh_init(p, feature_dimension=70)
    assert fresh is False, (
        "LIVE champion artifact equals the canonical seed-42 fresh init — "
        f"untrained random weights are serving decisions ({detail})"
    )
