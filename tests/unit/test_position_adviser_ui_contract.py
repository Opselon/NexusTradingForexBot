"""ML-POSITION-FORENSICS §21/§28 (UI phase): the API contract the Position
Adviser page renders must not silently regress.

The forensic phase made the adviser honest; this phase makes that honesty
*visible* (mission §21/§28: no black boxes, no fake feature values). Each
field below is consumed by ``frontend/src/features/position-adviser`` and
must survive every future edit of the route:

    * ``classes_absent`` — the F8 absent classes (zero-weight, never emittable).
      Without it the operator cannot distinguish "the model ruled this out"
      from "the model never saw this class" — exactly the black box §21 bans.
    * ``integrity`` / ``source_dataset_hash`` / ``manifest_path`` — the F6
      artifact-integrity verdict, rendered as the live "Package integrity"
      metric. A missing key here would render a blank where a verdict belongs.
    * ``stale_rejected_count`` — the F1 staleness-gate counter, rendered as
      "Stale refused" so a misconfigured gate is visible from the UI.
    * ``max_snapshot_age_sec`` — the operator-editable F1 gate; the field's
      existence in *status.config* is what lets the page mirror the server
      value instead of guessing locally.
    * ``diagnostics`` — the §22 decision trace (p_keep / snapshot id / age)
      shown under every advisory so no verdict is an unexplained number.

These call the route functions directly (the router is prefix-mounted and
self-contained: ``route_models`` takes no request state), so the test proves
the contract without depending on full app wiring.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import torch

from nexus_scalp.position_adviser.features import (
    ADVISER_FEATURE_DIM,
    ADVISER_FEATURE_ORDER,
)
from nexus_scalp.position_adviser.integration import (
    apply_advisory_to_hold_score,
    build_position_state_for_adviser,
)
from nexus_scalp.position_adviser.models import (
    ADVISER_ACTIONS,
    AdviserActivation,
)
from nexus_scalp.position_adviser.service import PositionAdviserService
from nexus_scalp.position_adviser.trainer import PositionAdviserNet
from nexus_scalp.web.position_adviser_routes import route_models


@pytest.fixture()
def ui_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the adviser containment root AND the web layer's repo root.

    ``route_models`` scans ``_repo_root() / artifact_dir``, and ``_repo_root``
    reads ``model_studio_routes.REPO_ROOT`` through its accessor — so both must
    point at the scratch dir or the route scans the real repo and finds nothing.
    """
    monkeypatch.setattr("nexus_scalp.position_adviser.paths.ADVISER_ROOT", tmp_path)
    monkeypatch.setattr("nexus_scalp.position_adviser.service._ADVISER_ROOT", tmp_path)
    monkeypatch.setattr("nexus_scalp.position_adviser.trainer._ADVISER_ROOT", tmp_path)
    monkeypatch.setattr("nexus_scalp.web.model_studio_routes.REPO_ROOT", tmp_path)
    return tmp_path


def _checkpoint(root: Path, model_id: str = "m1") -> Path:
    """Write a minimal (untrained) package into the adviser artifact dir.

    ``route_models`` scans the service's configured ``artifact_dir`` (repo
    root + ``artifacts/position_adviser``), so the package is written there —
    not into the scratch root directly.
    """
    from nexus_scalp.model_generation.artifact_store import sha256_file

    art = root / "artifacts" / "position_adviser"
    art.mkdir(parents=True, exist_ok=True)
    w = art / f"{model_id}.pt"
    s = art / f"{model_id}.scaler.npz"
    net = PositionAdviserNet(feature_dim=ADVISER_FEATURE_DIM, num_classes=len(ADVISER_ACTIONS))
    torch.save(net.state_dict(), w)
    x = np.random.default_rng(0).normal(size=(64, ADVISER_FEATURE_DIM))
    np.savez(s, mean=x.mean(axis=0), std=x.std(axis=0), dimension=ADVISER_FEATURE_DIM)
    w.with_suffix(".meta.json").write_text(
        json.dumps(
            {
                "feature_order": list(ADVISER_FEATURE_ORDER),
                "weights_sha256": sha256_file(w),
                "scaler_sha256": sha256_file(s),
                "source_dataset_hash": "deadbeef",
                "classes_absent": ["TRIM"],
            }
        ),
        encoding="utf-8",
    )
    return w


class _Pos:
    """Minimal open BUY position stand-in."""

    type = 1
    price_open = 2658.40
    sl = 2656.20
    tp = 2662.00
    volume = 0.10


def test_models_route_exposes_absent_classes(ui_tmp: Path) -> None:
    """§21: absent classes are part of the model identity the UI lists — a
    silent 0% must never read as 'the model considered it and ruled it out'."""
    w = _checkpoint(ui_tmp, model_id="absent_cls")
    svc = PositionAdviserService()
    load = svc.load(w, w.with_suffix(".scaler.npz"))
    assert load["status"] in {"LOADED", "OK"}, load

    out: dict[str, Any] = route_models()
    models = out.get("models", [])
    assert models, "route must list the packaged model"
    entry = next((m for m in models if m.get("model_id") == "absent_cls"), None)
    assert entry is not None
    assert entry["classes_absent"] == ["TRIM"]


def test_models_route_defaults_absent_classes_to_empty(ui_tmp: Path) -> None:
    """An older package without the key degrades to 'no classes absent'
    rather than KeyError-ing the whole models endpoint."""
    w = _checkpoint(ui_tmp, model_id="legacy")
    meta = json.loads(w.with_suffix(".meta.json").read_text(encoding="utf-8"))
    meta.pop("classes_absent", None)
    w.with_suffix(".meta.json").write_text(json.dumps(meta), encoding="utf-8")

    svc = PositionAdviserService()
    svc.load(w, w.with_suffix(".scaler.npz"))
    entry = next(m for m in route_models()["models"] if m.get("model_id") == "legacy")
    assert entry["classes_absent"] == []


def test_status_route_exposes_integrity_and_staleness_counters(ui_tmp: Path) -> None:
    """§16/§17 + F1: the integrity verdict and the stale-refusal counter are
    first-class status fields the live counters render."""
    w = _checkpoint(ui_tmp, model_id="identity")
    svc = PositionAdviserService()
    svc.load(w, w.with_suffix(".scaler.npz"))
    st = svc.status()

    assert st["integrity"] == "verified"
    assert st["source_dataset_hash"] == "deadbeef"
    assert str(st["manifest_path"]).endswith("identity.meta.json")
    # F1 counters exist so a misconfigured gate shows up in the UI.
    assert "stale_rejected_count" in st
    assert isinstance(st["stale_rejected_count"], int)


def test_status_config_exposes_the_operator_editable_gate(ui_tmp: Path) -> None:
    """§28: the staleness gate is operator-editable, so it must be present in
    the config payload the page mirrors on refresh — never a local guess.
    ``route_config`` is the real write path the UI's "Stale gate (s)" field
    calls, so drive it through the route, not a made-up service method."""
    from nexus_scalp.web.position_adviser_routes import (
        AdviserConfigRequest,
        get_position_adviser_service,
        route_config,
    )

    svc = get_position_adviser_service()
    try:
        resp = route_config(
            AdviserConfigRequest(min_eval_interval_sec=1.0, max_snapshot_age_sec=7.0)
        )
        assert resp["status"] == "OK"
        cfg = resp["config"]
        assert cfg["max_snapshot_age_sec"] == 7.0
        assert cfg["min_eval_interval_sec"] == 1.0
        # status() must report the same config the route just persisted.
        assert svc.status()["config"]["max_snapshot_age_sec"] == 7.0
    finally:
        # Restore defaults so this test cannot leak a tight gate into siblings.
        route_config(AdviserConfigRequest(max_snapshot_age_sec=5.0, min_eval_interval_sec=2.0))


def test_advisory_diagnostics_carries_the_decision_trace(ui_tmp: Path) -> None:
    """§22: every advisory carries the evidence it was built from — p_keep,
    the exact snapshot id, and how old that snapshot was. The feed renders
    these, so no verdict is an unexplained number."""
    w = _checkpoint(ui_tmp, model_id="trace")
    svc = PositionAdviserService()
    svc.load(w, w.with_suffix(".scaler.npz"))
    svc.set_activation(AdviserActivation.PAPER)

    state = build_position_state_for_adviser(
        pos=_Pos(),
        ticket=1,
        price_current=2659.10,
        atr=1.85,
        spread=0.25,
        initial_risk_price=2.20,
        holding_duration_sec=90.0,
        signal_age_bars=3,
        model_probability=0.72,
        model_confidence=0.61,
    )
    _, advisory = apply_advisory_to_hold_score(
        ticket=1, hold_score=70, position_state=state, service=svc
    )
    if advisory is not None:
        dg = advisory["diagnostics"] if isinstance(advisory, dict) else advisory.diagnostics
        assert "p_keep" in dg
        assert "snapshot_id" in dg
        # The trace must name the exact snapshot it decided on (§22 evidence).
        assert dg["snapshot_id"] == state["snapshot_id"]
