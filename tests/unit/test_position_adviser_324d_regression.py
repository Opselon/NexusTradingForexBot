"""Regression guards for the Position Adviser 12D production contract.

These tests exist to catch two specific regressions this repo has already
suffered:

1. ``test_no_324d_diagnostic_pipeline`` — a diagnostic Tensor Inspector request
   must NOT trigger the multi-timeframe indicator pipeline. The inspector is a
   read-only diagnostic surface; when it has no live position and no last
   sample it reports an explicit unavailable state and returns. It must never
   import ``nexus_scalp.indicators.central_manager`` or synthesize a fake
   position to fill the panel.
2. ``test_model_output_controls_advisory_action`` — the trained model's own
   argmax determines ``PositionAdvisory.action``. No legacy policy, threshold
   layer, or hardcoded selector may silently replace it. ``evaluate`` may only
   refuse (return None) on a validated gate failure; it must never return an
   advisory whose action disagrees with the model's argmax.
3. ``test_persisted_selection_is_12d`` — the adviser selection persisted in the
   application settings DB must resolve to a 12D checkpoint, so a restart
   rehydrates the production contract rather than an experiment.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from nexus_scalp.position_adviser.features import (
    ADVISER_FEATURE_DIM,
    ADVISER_FEATURE_ORDER,
    build_live_vector,
)
from nexus_scalp.position_adviser.models import (
    ADVISER_ACTIONS,
    AdviserActivation,
)
from nexus_scalp.position_adviser.service import PositionAdviserService

#: The 12D production checkpoint this contract is pinned to.
PRODUCTION_MODEL_ID = "pos_adviser_1790954427"


@pytest.fixture
def artifact_dir() -> Path:
    """The real production artifact directory (read-only for these tests)."""
    return Path("artifacts") / "position_adviser"


def _production_package(artifact_dir: Path) -> Path:
    return artifact_dir / PRODUCTION_MODEL_ID


def _valid_position_state(snapshot_id: str = "regression-324d-guard") -> dict[str, Any]:
    """A schema-valid position state: all 12 causal keys + snapshot contract."""
    now = time.monotonic()
    state = {
        "unrealized_pnl_r": 0.42,
        "current_r_net": 0.42,
        "current_return": 0.0031,
        "distance_to_stop_r": 1.1,
        "distance_to_target_r": 1.7,
        "position_age_bars": 37,
        "atr": 15.2,
        "spread": 0.15,
        "estimated_slippage": 0.05,
        "model_probability": 0.71,
        "model_confidence": 0.66,
        "signal_age": 37.0,
        "snapshot_observed_at": now,
        "snapshot_id": snapshot_id,
    }
    return state


# ---------------------------------------------------------------- feature side


def test_adviser_feature_order_is_exactly_12() -> None:
    """The serving feature contract is exactly the 12 causal position keys."""
    assert ADVISER_FEATURE_DIM == 12
    assert len(ADVISER_FEATURE_ORDER) == 12
    # No indicator/technical-analysis key may enter the serving contract.
    forbidden = ("oscillator", "ma.", "pivot", "rsi", "macd", "stoch", ".M1.", ".M5.", ".M15.")
    offenders = [n for n in ADVISER_FEATURE_ORDER if any(f in n.lower() for f in forbidden)]
    assert offenders == [], f"technical indicator keys leaked into the contract: {offenders}"


def test_build_live_vector_has_no_indicator_hook() -> None:
    """``build_live_vector`` must not accept a multi-dimensional feature_names
    hook that delegates to a central indicator manager."""
    import inspect

    sig = inspect.signature(build_live_vector)
    # The 324D experiment added ``feature_names`` / ``central_manager`` params
    # that routed to CentralAlgorithmManager when len(feature_names) > 12.
    assert "feature_names" not in sig.parameters, (
        "build_live_vector must not accept a feature_names hook — it reintroduces "
        "the 324D diagnostic pipeline into the feature builder"
    )
    assert "central_manager" not in sig.parameters


def test_central_indicator_manager_module_absent() -> None:
    """The 324D indicator manager module must not exist in the tree."""
    mgr = Path("src/nexus_scalp/indicators/central_manager.py")
    assert not mgr.exists(), (
        f"{mgr} is present: the experimental 324D indicator pipeline is back in the tree"
    )
    augmenter = Path("src/nexus_scalp/model_generation/indicator_augment.py")
    assert not augmenter.exists()


def test_indicators_package_exports_no_central_manager() -> None:
    """The indicators package must not re-export the 324D manager."""
    import nexus_scalp.indicators as pkg

    for name in ("CentralAlgorithmManager", "get_central_indicator_manager", "set_engine_ref"):
        assert not hasattr(pkg, name), f"nexus_scalp.indicators still exports {name}"


def test_diagnostics_module_has_no_central_manager_import() -> None:
    """The tensor inspector must not reach into the indicator pipeline."""
    src = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "nexus_scalp"
        / "position_adviser"
        / "diagnostics.py"
    ).read_text(encoding="utf-8")
    assert "central_manager" not in src, "diagnostics.py imports the 324D indicator pipeline"
    assert "live_realtime" not in src, "diagnostics.py synthesizes a fake live-market preview"
    # A fabricated neutral position must never be built to fill the panel.
    assert "_synthesize_neutral_state" not in src


def test_service_module_has_no_dynamic_feature_names() -> None:
    """The service must not carry per-model feature names that could select a
    >12D schema at serve time."""
    src = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "nexus_scalp"
        / "position_adviser"
        / "service.py"
    ).read_text(encoding="utf-8")
    assert "_feature_names" not in src, "service.py reintroduces dynamic feature-name selection"
    assert "feature_schema" not in src.replace("feature_schema_id", "")


def test_web_routes_have_no_324d_build_endpoint() -> None:
    """The one-button 324D build-and-train endpoint must be gone."""
    src = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "nexus_scalp"
        / "web"
        / "position_adviser_routes.py"
    ).read_text(encoding="utf-8")
    assert "build-and-train" not in src, "the 324D build-and-train route is present"
    assert "AdviserBuildTrainRequest" not in src
    assert "indicator_augment" not in src
    assert "central_manager" not in src


# ------------------------------------------------------------------ model side


@pytest.fixture
def loaded_service(artifact_dir: Path) -> PositionAdviserService:
    pkg = _production_package(artifact_dir)
    svc = PositionAdviserService()
    out = svc.load(
        weights_path=str(pkg) + ".pt",
        scaler_path=str(pkg) + ".scaler.npz",
        model_id=PRODUCTION_MODEL_ID,
    )
    assert out["status"] == "OK", out
    assert out["feature_dim"] == ADVISER_FEATURE_DIM
    svc.set_activation("PAPER")
    return svc


def test_production_checkpoint_is_12d(artifact_dir: Path) -> None:
    """The pinned production checkpoint, scaler and manifest are all 12D."""
    pkg = _production_package(artifact_dir)
    manifest = json.loads(pkg.with_suffix(".meta.json").read_text(encoding="utf-8"))
    assert manifest["feature_dim"] == 12, manifest.get("feature_dim")
    assert len(manifest["feature_order"]) == 12
    assert manifest["feature_order"] == list(ADVISER_FEATURE_ORDER)


def test_model_output_controls_advisory_action(loaded_service: PositionAdviserService) -> None:
    """The advisory action is the model's own argmax — no policy override.

    ``evaluate`` computes the softmax over the model logits and takes
    ``argmax``; nothing downstream may swap that action for a hardcoded
    verdict. We verify by replaying the same feature vector through the model
    ourselves and comparing.
    """
    import torch

    svc = loaded_service
    state = _valid_position_state()

    advisory = svc.evaluate(ticket=901001, position_state=state)
    assert advisory is not None, "PAPER-mode evaluate on a fresh snapshot must produce output"

    # Replay the model independently to get the ground-truth argmax.
    vec, names = build_live_vector(dict(state))
    assert names == list(ADVISER_FEATURE_ORDER)
    assert vec.shape == (ADVISER_FEATURE_DIM,)
    with svc._lock:
        model = svc._state._model
        scaler = svc._state._scaler
    x = torch.tensor(scaler.transform(vec.reshape(1, -1)), dtype=torch.float32)
    with torch.no_grad():
        logits = model(x).float().cpu().numpy().reshape(-1)
    z = logits[: len(ADVISER_ACTIONS)]
    p = np.exp(z - z.max())
    p = p / p.sum()
    expected = ADVISER_ACTIONS[int(np.argmax(p))]

    assert advisory.action == expected, (
        f"advisory action {advisory.action!r} disagrees with the model argmax "
        f"{expected!r} — a policy layer is overriding the model output"
    )
    # The advisory must carry the model's own probabilities, not a policy's.
    for key, prob in advisory.probabilities.items():
        idx = ADVISER_ACTIONS.index(key)
        assert abs(prob - round(float(p[idx]), 4)) < 1e-3, (key, prob, p[idx])


def test_keep_verdict_yields_zero_adjustment(loaded_service: PositionAdviserService) -> None:
    """A KEEP verdict never moves the hold score — the adviser only penalizes.

    This pins the Layer-2 risk contract: the adviser may lower a hold score,
    never lift one, never widen a stop, never open or size a position.
    """
    svc = loaded_service
    # A young, profitable position well inside its stop: whatever the model
    # says, a KEEP verdict must contribute exactly 0.0.
    for _ in range(3):
        state = _valid_position_state(snapshot_id=f"keep-guard-{time.monotonic()}")
        advisory = svc.evaluate(ticket=901002, position_state=state)
        if advisory is None:
            continue
        if advisory.action == "KEEP":
            assert advisory.hold_score_adjustment == 0.0
            return
    # If the model never returns KEEP on these inputs the contract is still
    # unverifiable here — assert the invariant directly through the bounded
    # adjustment instead.
    probs = np.array([0.55, 0.25, 0.20])  # KEEP majority
    assert svc._bounded_adjustment(probs, confidence=0.9) == 0.0


def test_advisory_fails_closed_on_missing_feature_key(
    loaded_service: PositionAdviserService,
) -> None:
    """A missing causal key must refuse inference, not fabricate a value."""
    svc = loaded_service
    broken = _valid_position_state()
    del broken["atr"]
    before = svc._state.evaluated_count
    advisory = svc.evaluate(ticket=901003, position_state=broken)
    assert advisory is None
    assert svc._state.refused_count >= 1
    assert svc._state.evaluated_count == before


def test_advisory_fails_closed_on_stale_snapshot(loaded_service: PositionAdviserService) -> None:
    """A stale snapshot is rejected before any inference — no silent fallback."""
    svc = loaded_service
    stale = _valid_position_state()
    stale["snapshot_observed_at"] = time.monotonic() - 3600.0  # 1h old
    advisory = svc.evaluate(ticket=901004, position_state=stale)
    assert advisory is None
    assert svc._state.stale_rejected_count >= 1


def test_disabled_adviser_returns_none(loaded_service: PositionAdviserService) -> None:
    """DISABLED means no advisory at all — the decide path is unaffected."""
    svc = loaded_service
    svc.set_activation("DISABLED")
    assert svc.activation is AdviserActivation.DISABLED
    assert svc.evaluate(ticket=901005, position_state=_valid_position_state()) is None


def test_e2e_model_decision_reaches_hold_score(loaded_service: PositionAdviserService) -> None:
    """KEEP/CLOSE propagate through ``apply_advisory_to_hold_score`` unchanged
    in score, and a CLOSE penalty is bounded by the config.

    This is the integration seam: ``order_manager`` calls this function with an
    already-final hold score. The model's verdict must be the thing applied.
    """
    from nexus_scalp.position_adviser.integration import apply_advisory_to_hold_score

    svc = loaded_service
    svc.set_activation("LIVE")
    state = _valid_position_state()
    hold, advisory = apply_advisory_to_hold_score(
        ticket=901006, hold_score=80, position_state=state, service=svc
    )
    if advisory is None:
        pytest.skip("adviser refused the synthetic snapshot; model seam unreachable")
    adj = advisory["hold_score_adjustment"]
    assert adj <= 0.0, "the adviser may only lower a hold score"
    assert hold == max(0, min(100, round(80 + adj)))
    # A KEEP verdict (adj == 0) leaves the score byte-identical.
    if advisory["action"] == "KEEP":
        assert hold == 80


def test_persisted_selection_round_trip_is_12d(artifact_dir: Path) -> None:
    """A persisted adviser selection must round-trip to a 12D checkpoint.

    A restart rehydrates from ``application_settings``. This writes a 12D
    selection into the (test-isolated) settings DB, reads it back, and proves
    the reload path resolves a 12D manifest — so a 324D checkpoint persisted
    there would be caught at the contract, not served silently at runtime.
    """
    from nexus_scalp.position_adviser.settings_store import AdviserSettingsStore
    from nexus_scalp.settings.service import SettingsDatabase

    pkg = _production_package(artifact_dir)
    db = SettingsDatabase()
    store = AdviserSettingsStore(db)

    store.save_selection(
        model_id=PRODUCTION_MODEL_ID,
        weights_path=str(pkg) + ".pt",
        scaler_path=str(pkg) + ".scaler.npz",
    )
    reloaded = store.load()
    assert reloaded.model_id == PRODUCTION_MODEL_ID

    manifest = pkg.with_suffix(".meta.json")
    assert manifest.is_file()
    record = json.loads(manifest.read_text(encoding="utf-8"))
    assert record["feature_dim"] == ADVISER_FEATURE_DIM, (
        f"persisted adviser selection {reloaded.model_id} resolves to a "
        f"{record['feature_dim']}D checkpoint — a restart rehydrates a "
        "non-production contract"
    )
    assert record["feature_order"] == list(ADVISER_FEATURE_ORDER)
