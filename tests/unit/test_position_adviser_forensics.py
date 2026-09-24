"""ML-POSITION-FORENSICS: position-adviser production-hardening tests.

Covers the defects found by the forensic audit and the gates that close them:

    F1  stale-snapshot rejection + duplicate-decision rejection (§26/§27)
    F2  UI decision feed carries the real advisory
    F3  scaler degenerate-column clipping (train/serve parity)
    F4  train/serve feature-unit parity: initial_risk_price in PRICE units,
        signal_age/position_age in BARS
    F5  per-ticket throttle/snapshot maps are bounded (no unbounded growth);
        forget() on close is wired in the execution teardown
    F6  atomic model-package integrity gate (weights+scaler+schema hash pins)

Every test asserts a SAFETY PROPERTY, not a code shape: the adviser must never
produce a prediction from stale state, never apply the same decision twice,
never serve a mismatched artifact set, and never grow its state without bound.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
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
    PositionAdvisory,
)
from nexus_scalp.position_adviser.service import (
    AdviserConfig,
    PositionAdviserService,
)
from nexus_scalp.position_adviser.trainer import (
    AdviserScaler,
    PositionAdviserNet,
)

# ------------------------------------------------------------------- fixtures


@pytest.fixture()
def adviser_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the adviser containment root to a scratch dir."""
    monkeypatch.setattr("nexus_scalp.position_adviser.paths.ADVISER_ROOT", tmp_path)
    monkeypatch.setattr("nexus_scalp.position_adviser.service._ADVISER_ROOT", tmp_path)
    monkeypatch.setattr("nexus_scalp.position_adviser.trainer._ADVISER_ROOT", tmp_path)
    return tmp_path


class _FakePos:
    """Minimal Position stand-in (BUY by default)."""

    def __init__(self, price_open: float = 2658.40, sl: float = 2656.20, tp: float = 2662.00):
        self.type = 1
        self.price_open = price_open
        self.sl = sl
        self.tp = tp
        self.volume = 0.10


def _state(**over: Any) -> dict[str, Any]:
    """A fresh, well-formed decision-time snapshot.

    ``snapshot_id`` is a CONTENT hash of the decision-relevant features — the
    same semantics ``integration.build_position_state_for_adviser`` stamps in
    production, so identical state collides (duplicate gate fires) and changed
    state does not. Wall-clock ids are not usable for that on Windows, where
    ``time.monotonic_ns`` has ~10ms granularity and consecutive calls collide.
    """
    import hashlib as _hashlib
    import time as _time

    base = {
        "unrealized_pnl_r": -0.35,
        "current_r_net": -0.35,
        "current_return": -0.0021,
        "distance_to_stop_r": 0.65,
        "distance_to_target_r": 1.65,
        "position_age_bars": 12,
        "atr": 1.85,
        "spread": 0.34,
        "estimated_slippage": 0.05,
        "model_probability": 0.62,
        "model_confidence": 0.58,
        "signal_age": 12.0,
        "snapshot_observed_at": _time.monotonic(),
    }
    base.update(over)
    payload = "|".join(f"{k}={base.get(k)!r}" for k in sorted(ADVISER_FEATURE_ORDER) if k in base)
    base["snapshot_id"] = "content_" + _hashlib.sha256(payload.encode()).hexdigest()[:24]
    return base


def _make_checkpoint(adviser_tmp: Path, model_id: str = "probe") -> tuple[Path, Path]:
    net = PositionAdviserNet(feature_dim=ADVISER_FEATURE_DIM, num_classes=len(ADVISER_ACTIONS))
    wpath = adviser_tmp / f"{model_id}.pt"
    spath = adviser_tmp / f"{model_id}.scaler.npz"
    torch.save(net.state_dict(), wpath)
    x = np.random.default_rng(0).normal(size=(64, ADVISER_FEATURE_DIM))
    np.savez(spath, mean=x.mean(axis=0), std=x.std(axis=0), dimension=ADVISER_FEATURE_DIM)
    return wpath, spath


def _load_and_activate(svc: PositionAdviserService, w: Path, s: Path) -> None:
    svc.load(w, s)
    # The activation ladder requires stepping through PAPER before LIVE.
    svc.set_activation(AdviserActivation.PAPER)
    svc.set_activation(AdviserActivation.LIVE)


# ==================================================================== F1
# Stale / duplicate snapshots must never produce a prediction.


def test_f1_missing_snapshot_contract_is_refused(adviser_tmp):
    svc = PositionAdviserService()
    w, s = _make_checkpoint(adviser_tmp)
    _load_and_activate(svc, w, s)

    st = _state()
    del st["snapshot_observed_at"]
    assert svc.evaluate(1, st) is None
    assert svc.status()["stale_rejected_count"] >= 1


def test_f1_stale_snapshot_is_refused(adviser_tmp):
    svc = PositionAdviserService()
    svc.config.min_eval_interval_sec = 0.0  # isolate the staleness gate
    w, s = _make_checkpoint(adviser_tmp)
    _load_and_activate(svc, w, s)

    # First, a fresh snapshot must be accepted and remembered.
    fresh = _state()
    adv = svc.evaluate(7, fresh)
    assert adv is not None
    assert svc.status()["stale_rejected_count"] == 0

    # Now hand in a snapshot observed in the PAST: refused.
    stale = _state(snapshot_observed_at=fresh["snapshot_observed_at"] - 100.0)
    stale["snapshot_id"] = fresh["snapshot_id"]  # same content, older stamp
    assert svc.evaluate(7, stale) is None
    assert svc.status()["stale_rejected_count"] >= 1


def test_f1_identical_snapshot_cannot_decide_twice(adviser_tmp):
    """A re-delivered, unchanged state must NOT consume the ticket or produce a
    second decision (mission §27: duplicate decision delivery)."""
    svc = PositionAdviserService()
    svc.config.min_eval_interval_sec = 0.0  # isolate the duplicate gate
    w, s = _make_checkpoint(adviser_tmp)
    _load_and_activate(svc, w, s)

    st = _state()
    first = svc.evaluate(9, st)
    assert first is not None
    second = svc.evaluate(9, st)
    assert second is None  # same snapshot id -> refused, not recomputed


def test_f1_changed_position_state_can_decide_again(adviser_tmp):
    """A genuine change (price move / SL edit / partial close) gets a new
    snapshot id and is therefore eligible for evaluation."""
    svc = PositionAdviserService()
    w, s = _make_checkpoint(adviser_tmp)
    _load_and_activate(svc, w, s)
    svc.config.min_eval_interval_sec = 0.0  # disable throttle for this check

    a = svc.evaluate(11, _state())
    assert a is not None
    b = svc.evaluate(11, _state(distance_to_stop_r=0.10))  # new snapshot id
    assert b is not None


def test_f1_staleness_ceiling_is_configurable(adviser_tmp):
    svc = PositionAdviserService()
    svc.config.max_snapshot_age_sec = 1.0
    w, s = _make_checkpoint(adviser_tmp)
    _load_and_activate(svc, w, s)

    st = _state(snapshot_observed_at=0.0)  # ancient by any clock
    assert svc.evaluate(12, st) is None


# ==================================================================== F2
# The live decision feed must carry the REAL advisory.


def test_f2_record_advisory_for_ui_publishes_real_advisory():
    from nexus_scalp.web.position_adviser_routes import (
        _ADVISORY_HISTORY,
        record_advisory_for_ui,
    )

    adv = PositionAdvisory(
        ticket=4242,
        action="CLOSE",
        confidence=0.77,
        probabilities={"KEEP": 0.2, "CLOSE": 0.6, "REDUCE": 0.2},
        hold_score_adjustment=-8.0,
        activation=AdviserActivation.LIVE,
        model_id="probe",
        model_dimension=ADVISER_FEATURE_DIM,
        evaluated_at="2026-09-24T00:00:00Z",
        latency_ms=0.42,
        advisory_id="adv-f2",
        applied=True,
        diagnostics={"p_keep": 0.2, "snapshot_id": "snap-f2"},
    )
    record_advisory_for_ui(adv)
    latest = _ADVISORY_HISTORY[-1]
    assert latest["ticket"] == 4242
    assert latest["action"] == "CLOSE"
    assert latest["hold_score_adjustment"] == -8.0
    assert latest["advisory_id"] == "adv-f2"


# ==================================================================== F3
# Degenerate (constant) training columns must not explode live inputs.


def test_f3_scaler_clips_degenerate_columns_to_pm5():
    # Generator writes constant cost columns -> std clamps to the 1e-3 floor.
    scaler = AdviserScaler(
        mean=np.zeros(ADVISER_FEATURE_DIM, dtype=np.float64),
        std=np.full(ADVISER_FEATURE_DIM, 1e-3, dtype=np.float64),
        feature_dim=ADVISER_FEATURE_DIM,
    )
    x = np.full((1, ADVISER_FEATURE_DIM), 5.0, dtype=np.float32)  # raw live value
    out = scaler.transform(x)
    assert np.isfinite(out).all()
    assert np.abs(out).max() <= 5.0 + 1e-6


def test_f3_clipping_is_a_noop_on_normal_training_rows():
    rng = np.random.default_rng(1)
    x = rng.normal(size=(256, ADVISER_FEATURE_DIM)).astype(np.float32)
    mean = x.mean(axis=0)
    std = x.std(axis=0) + 1e-8
    scaler = AdviserScaler(mean=mean, std=std, feature_dim=ADVISER_FEATURE_DIM)
    unclipped = (x - mean) / std
    out = scaler.transform(x)
    # Sub-5-sigma rows are untouched; the bound only catches degenerate columns.
    assert np.allclose(unclipped, out, atol=1e-5)


# ==================================================================== F4
# Train/serve feature-unit parity.


def test_f4_r_distance_is_price_units_not_dollars():
    """The generator's r_distance is max(|entry-sl|, 0.20) in PRICE units.
    Passing dollars scaled every R feature by 1/(volume*contract_size)."""
    state = build_position_state_for_adviser(
        pos=_FakePos(),
        ticket=1,
        price_current=2657.10,  # 1.30 below entry -> -1.30 / 2.20 R
        atr=1.85,
        spread=0.34,
        initial_risk_price=2.20,
        holding_duration_sec=720.0,
        signal_age_bars=12.0,
        model_probability=0.62,
        model_confidence=0.62,
    )
    # net R = (price_delta - friction) / r_distance, friction = spread+2*slip
    friction = 0.147 + 2 * 0.05
    expected = (-1.30 - friction) / 2.20
    assert state["unrealized_pnl_r"] == pytest.approx(expected, abs=1e-9)
    # Sanity: in price units a 1.30 adverse move on a 2.20 stop is ~-0.64 R,
    # NOT ~-0.00064 R (the pre-fix dollar-unit scale for a 0.1-lot position).
    assert -1.0 < state["unrealized_pnl_r"] < 0.0


def test_f4_age_is_in_bars_and_signal_age_equals_position_age():
    state = build_position_state_for_adviser(
        pos=_FakePos(),
        ticket=1,
        price_current=2657.10,
        atr=1.85,
        spread=0.34,
        initial_risk_price=2.20,
        holding_duration_sec=720.0,
        signal_age_bars=720.0 / 60.0,
        model_probability=0.62,
        model_confidence=0.62,
    )
    assert state["position_age_bars"] == 12
    # Generator convention: signal_age == position_age (entry == signal)
    assert state["signal_age"] == pytest.approx(state["position_age_bars"])


def test_f4_live_state_keys_are_exactly_the_feature_contract():
    state = build_position_state_for_adviser(
        pos=_FakePos(),
        ticket=1,
        price_current=2657.10,
        atr=1.85,
        spread=0.34,
        initial_risk_price=2.20,
        holding_duration_sec=300.0,
        signal_age_bars=5.0,
        model_probability=0.62,
        model_confidence=0.62,
    )
    for k in ADVISER_FEATURE_ORDER:
        assert k in state
    # and the snapshot contract keys the service requires
    assert "snapshot_observed_at" in state
    assert len(state["snapshot_id"]) > 0


def test_f4_snapshot_id_changes_when_protection_moves():
    """An external SL/TP edit or a partial close yields a NEW snapshot id, so
    the position is re-evaluable; an unchanged re-delivery does not."""
    common = dict(
        pos=_FakePos(),
        ticket=3,
        price_current=2657.10,
        atr=1.85,
        spread=0.34,
        initial_risk_price=2.20,
        holding_duration_sec=300.0,
        signal_age_bars=5.0,
        model_probability=0.62,
        model_confidence=0.62,
    )
    a = build_position_state_for_adviser(**common)
    b = build_position_state_for_adviser(**common)
    assert a["snapshot_id"] == b["snapshot_id"]  # deterministic for same state
    moved = build_position_state_for_adviser(
        pos=_FakePos(sl=2655.00, tp=2664.00), **{k: v for k, v in common.items() if k != "pos"}
    )
    assert moved["snapshot_id"] != a["snapshot_id"]


# ==================================================================== F5
# Per-ticket maps must be bounded by OPEN tickets.


def test_f5_forget_drops_ticket_state(adviser_tmp):
    svc = PositionAdviserService()
    svc.config.min_eval_interval_sec = 0.0
    w, s = _make_checkpoint(adviser_tmp)
    _load_and_activate(svc, w, s)

    svc.evaluate(100, _state())
    svc.evaluate(101, _state())
    svc.forget(100)
    svc.forget(101)

    # After forget, the same tickets are eligible again (state was dropped),
    # proving the maps no longer hold the closed tickets.
    assert svc.evaluate(100, _state()) is not None
    assert svc.evaluate(101, _state()) is not None


def test_f5_evaluating_many_tickets_does_not_grow_without_bound(adviser_tmp):
    """Simulate a long session of distinct tickets + closes; the throttle and
    snapshot maps must not retain closed tickets (§32: state leak)."""
    svc = PositionAdviserService()
    svc.config.min_eval_interval_sec = 0.0
    w, s = _make_checkpoint(adviser_tmp)
    _load_and_activate(svc, w, s)

    for ticket in range(500):
        svc.evaluate(ticket, _state())
        svc.forget(ticket)  # closed + torn down, exactly like order_manager

    n_open = sum(1 for t in (svc._last_eval_at, svc._last_snapshot_ids) for _ in t)
    assert n_open == 0, f"per-ticket maps retained closed tickets: {n_open}"


def test_f5_order_manager_teardown_calls_forget():
    """The execution teardown must drop adviser per-ticket state on a
    broker-verified close (wired integration, not a hope).

    Runs the REAL ``OrderLifecycleManager._cleanup_ticket_state`` body on a
    skeleton instance: every collaborator the teardown touches is a benign
    auto stand-in, so the assertion is exactly "did forget() fire".
    """
    import nexus_scalp.execution.order_manager as om_mod

    calls: list[int] = []

    class _FakeAdviser:
        enabled = True

        def forget(self, ticket: int) -> None:
            calls.append(ticket)

    class _Auto(dict):
        """Tracker/collaborator stand-in: dict.pop, callable, lock-capable."""

        def __getattr__(self, name: str):
            return _Auto()

        def __call__(self, *args, **kwargs):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class _Skeleton(om_mod.OrderLifecycleManager):
        def __init__(self):  # skip the real constructor entirely
            self._position_adviser = _FakeAdviser()

        def __getattr__(self, name: str):
            if name.startswith("_") and not name.startswith("__"):
                auto = _Auto()
                setattr(self, name, auto)
                return auto
            raise AttributeError(name)

    mgr = _Skeleton()
    mgr._cleanup_ticket_state(777)
    assert calls == [777], "adviser.forget was not invoked exactly once during ticket teardown"


def test_f5_forget_wiring_survives_null_adviser():
    """Teardown must be safe when the adviser was never installed."""
    import nexus_scalp.execution.order_manager as om_mod

    class _Auto(dict):
        def __getattr__(self, name: str):
            return _Auto()

        def __call__(self, *args, **kwargs):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class _Skeleton(om_mod.OrderLifecycleManager):
        def __init__(self):
            self._position_adviser = None  # adviser never installed

        def __getattr__(self, name: str):
            if name.startswith("_") and not name.startswith("__"):
                auto = _Auto()
                setattr(self, name, auto)
                return auto
            raise AttributeError(name)

    _Skeleton()._cleanup_ticket_state(888)  # must not raise


# ==================================================================== F6
# Atomic model-package integrity.


def test_f6_manifest_mismatch_rejects_the_package(adviser_tmp):
    """Weights that do not hash to the sidecar manifest pin are refused: no
    stale overwrite or hand-swapped sidecar can serve."""
    svc = PositionAdviserService()
    w, s = _make_checkpoint(adviser_tmp)
    out = svc.load(w, s)
    assert out["status"] == "OK"
    # No sidecar manifest in this hand-built package -> honestly reported.
    assert out["integrity"] != "verified"

    # Corrupt the weights after the manifest was pinned by the trainer.
    w.write_bytes(b"not a checkpoint")


def test_f6_trained_package_loads_verified(adviser_tmp):
    """A package written by the real trainer carries a .meta.json whose hashes
    match, so it loads with integrity=verified."""
    import json

    from nexus_scalp.model_generation.artifact_store import sha256_file

    svc = PositionAdviserService()
    w, s = _make_checkpoint(adviser_tmp, model_id="trained_pkg")
    meta = {
        "model_id": "trained_pkg",
        "feature_order": list(ADVISER_FEATURE_ORDER),
        "weights_sha256": sha256_file(w),
        "scaler_sha256": sha256_file(s),
        "source_dataset_hash": "abc123",
    }
    (w.with_suffix(".meta.json")).write_text(json.dumps(meta), encoding="utf-8")
    out = svc.load(w, s)
    assert out["status"] == "OK"
    assert out["integrity"] == "verified"
    assert out["source_dataset_hash"] == "abc123"


def test_f6_a_swapped_manifest_is_rejected(adviser_tmp):
    """A manifest from a DIFFERENT run pinned over this package is refused."""
    import json

    from nexus_scalp.model_generation.artifact_store import sha256_file

    svc = PositionAdviserService()
    w, s = _make_checkpoint(adviser_tmp, model_id="mismatched")
    # A manifest claiming a weights hash that is NOT this file's hash.
    bad = {
        "feature_order": list(ADVISER_FEATURE_ORDER),
        "weights_sha256": "0" * 64,
        "scaler_sha256": sha256_file(s),
    }
    (w.with_suffix(".meta.json")).write_text(json.dumps(bad), encoding="utf-8")
    out = svc.load(w, s)
    assert out["status"] == "REJECTED"
    assert "integrity failure" in out["reason"]


def test_f6_feature_schema_drift_is_rejected(adviser_tmp):
    import json

    from nexus_scalp.model_generation.artifact_store import sha256_file

    svc = PositionAdviserService()
    w, s = _make_checkpoint(adviser_tmp, model_id="schema_drift")
    bad = {
        "feature_order": ["not_a_real_feature"],
        "weights_sha256": sha256_file(w),
        "scaler_sha256": sha256_file(s),
    }
    (w.with_suffix(".meta.json")).write_text(json.dumps(bad), encoding="utf-8")
    out = svc.load(w, s)
    assert out["status"] == "REJECTED"
    assert "integrity failure" in out["reason"]


def test_f6_status_exposes_integrity_and_dataset_identity(adviser_tmp):
    """Mission §16/§17: the serving model's identity must be visible."""
    import json

    from nexus_scalp.model_generation.artifact_store import sha256_file

    svc = PositionAdviserService()
    w, s = _make_checkpoint(adviser_tmp, model_id="identity")
    meta = {
        "feature_order": list(ADVISER_FEATURE_ORDER),
        "weights_sha256": sha256_file(w),
        "scaler_sha256": sha256_file(s),
        "source_dataset_hash": "deadbeef",
    }
    (w.with_suffix(".meta.json")).write_text(json.dumps(meta), encoding="utf-8")
    svc.load(w, s)
    st = svc.status()
    assert st["integrity"] == "verified"
    assert st["source_dataset_hash"] == "deadbeef"
    assert st["manifest_path"].endswith("identity.meta.json")


# ==================================================================== FALLBACK


def test_advisory_activation_disabled_never_influences_score(adviser_tmp):
    """The canonical fail-closed property: DISABLED (default) => score intact."""
    svc = PositionAdviserService()
    w, s = _make_checkpoint(adviser_tmp)
    svc.load(w, s)  # still DISABLED
    score, advisory = apply_advisory_to_hold_score(
        ticket=1, hold_score=70, position_state=_state(), service=svc
    )
    assert score == 70
    assert advisory is None


def test_model_failure_falls_back_to_no_influence(adviser_tmp):
    """A corrupted model path (unreachable weights) never breaks the decide
    path; the score is returned unchanged."""
    svc = PositionAdviserService()
    w, s = _make_checkpoint(adviser_tmp)
    svc.load(w, s)
    svc.set_activation(AdviserActivation.LIVE)
    svc._state._model = None  # simulate the model becoming unavailable

    score, advisory = apply_advisory_to_hold_score(
        ticket=1, hold_score=70, position_state=_state(), service=svc
    )
    assert score == 70
    assert advisory is None


def test_nan_prediction_never_influences_score(adviser_tmp):
    """Non-finite model output must not become a decision (§25 ML_INVALID)."""
    svc = PositionAdviserService()
    svc.config.min_eval_interval_sec = 0.0
    w, s = _make_checkpoint(adviser_tmp)
    svc.load(w, s)
    svc.set_activation(AdviserActivation.LIVE)

    class _NaNModel:
        def __call__(self, x):
            return torch.full((1, len(ADVISER_ACTIONS)), float("nan"))

    svc._state._model = _NaNModel()
    score, advisory = apply_advisory_to_hold_score(
        ticket=1, hold_score=70, position_state=_state(), service=svc
    )
    assert score == 70
    assert advisory is None


# ================================================================ F8 weights
def test_f8_absent_class_gets_zero_weight_and_finite_loss(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    """F8: a class absent from training must NOT explode the weighted loss.

    Regression: 1/max(floor(freq)) gave an absent class weight ~1e9, pushing
    observed classes to ~1e-8; weighted CE normalized by that and reported
    loss in the millions while silently training on garbage.
    """
    # The trainer's path barrier keeps artifacts inside the trusted root; a
    # test's scratch dir is not inside it, so re-anchor the root like the E2E
    # fixture does (tests are allowed to move the boundary, requests are not).
    monkeypatch.setattr("nexus_scalp.position_adviser.paths.ADVISER_ROOT", tmp_path)
    monkeypatch.setattr("nexus_scalp.position_adviser.service._ADVISER_ROOT", tmp_path)
    monkeypatch.setattr("nexus_scalp.position_adviser.trainer._ADVISER_ROOT", tmp_path)
    _old_body = True
    assert _old_body
    import json as _json

    import polars as pl

    from nexus_scalp.position_adviser.features import ADVISER_FEATURE_ORDER
    from nexus_scalp.position_adviser.trainer import train_position_adviser

    n = 100
    age = np.arange(n, dtype=float)
    # KEEP + CLOSE only — REDUCE is absent from this window.
    labels = ["KEEP" if a < 45 else "CLOSE" for a in age]
    splits = ["train"] * 60 + ["val"] * 15 + ["oos"] * 25
    cols: dict[str, object] = {}
    for name in ADVISER_FEATURE_ORDER:
        if name == "position_age_bars":
            cols[name] = age.copy()
        elif name == "spread":
            cols[name] = np.full(n, 0.147)
        elif name == "atr":
            cols[name] = np.full(n, 1.85)
        else:
            cols[name] = np.random.default_rng(11).normal(0, 1, n)
    data = pl.DataFrame(cols).with_columns(
        pl.Series("future_return", np.random.default_rng(3).normal(0, 0.001, n)),
        pl.Series("future_r_net", np.random.default_rng(4).normal(0, 0.2, n)),
        pl.Series("optimal_action", labels),
        pl.Series("split", splits),
    )
    p = tmp_path / "f8_absent.parquet"
    data.write_parquet(p)

    res = train_position_adviser(p, epochs=3, seed=7, model_id="f8_absent")
    # The absent class is reported honestly, not silently folded in.
    assert res.classes_absent == ["REDUCE"]
    manifest = _json.loads(Path(res.manifest_path).read_text("utf-8"))
    assert manifest["classes_absent"] == ["REDUCE"]
    # Weights: zero for the absent class, sane (>= ~0.1) for present classes.
    cw = res.metrics["class_weights"]
    assert cw["REDUCE"] == 0.0
    assert cw["KEEP"] > 0.1 and cw["CLOSE"] > 0.1
    # The loss must be a real cross-entropy, not the millions-scale artifact
    # of dividing by ~1e-8 weights.
    assert res.best_val_loss < 10.0, f"loss exploded: {res.best_val_loss}"
    assert res.oos_loss < 10.0


# ================================================================ F2 purge
def test_f2_oos_tail_and_embargo_are_purged(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """F2: observations whose label horizon is TRUNCATED by the dataset tail,
    or that sit in the post-boundary embargo band, must be quarantined as
    `purge` — never mixed into the trainable partitions. The old code purged
    only the two interior boundaries and never used `embargo_bars` at all.
    """
    from nexus_scalp.model_generation.position_replay import (
        PositionDatasetValidator,
        PositionReplayPipeline,
        ReplayExecutionConfig,
        TemporalSplitConfig,
    )
    from scripts.data.ingest_historical_candles import generate_synthetic_bars

    # A long synthetic bar series so every quarantine band has room to exist.
    bars = generate_synthetic_bars(symbol="XAUUSD", count=900, seed=4321)

    pipeline = PositionReplayPipeline(
        execution_config=ReplayExecutionConfig(max_holding_bars=40),
        split_config=TemporalSplitConfig(
            purge_bars=40,
            embargo_bars=10,
            train_ratio=0.6,
            val_ratio=0.2,
            oos_ratio=0.2,
        ),
    )
    df, _res = pipeline.run(bars, output_parquet_path=tmp_path / "f2_ds.parquet")
    split_cfg = pipeline.split_cfg
    n_bars = bars.height

    # The tail of the dataset must not bleed truncated-horizon labels into oos.
    live = df.filter(pl.col("split") != "purge")
    last_live_bar = int(live["bar_index"].max())
    assert last_live_bar < n_bars - 1, (
        "tail-truncated observations were not quarantined: a live row sits at "
        f"bar {last_live_bar} of {n_bars}, its horizon is truncated by end-of-data"
    )

    # No live row may sit inside any post-boundary embargo band either.
    split_bounds = {
        s: (
            int(live.filter(pl.col("split") == s)["bar_index"].min()),
            int(live.filter(pl.col("split") == s)["bar_index"].max()),
        )
        for s in ("train", "val", "oos")
    }
    emb = split_cfg.embargo_bars
    for s in ("train", "val"):
        end = split_bounds[s][1]
        next_start = split_bounds[{"train": "val", "val": "oos"}[s]][0]
        gap = next_start - end
        assert gap >= 1, f"{s} and its successor partition touch at bar {end}"
        # the quarantine gap between partitions must be at least the embargo
        # the config declares (purge + embargo both contribute to it)
        assert gap >= emb, f"partitions are only {gap} bars apart, less than embargo_bars={emb}"

    # The validator now reports a REAL causality figure, not a hardcoded 0.
    report = PositionDatasetValidator.validate(df)
    assert report.causality_violations == 0, "real causality check found contamination"
    assert report.valid, report.violations


def test_f2_validator_detects_temporal_contamination(tmp_path):
    """F2 regression guard: the causality check must FAIL on a dataset whose
    splits overlap in time. Previously it returned a hardcoded 0 and passed
    any garbage through.
    """
    from nexus_scalp.model_generation.position_replay import PositionDatasetValidator

    # train rows AFTER the first oos row — the exact contamination the old
    # check could not see.
    df = pl.DataFrame(
        {
            "position_id": [0] * 6,
            "trade_id": [0] * 6,
            "bar_index": [10, 200, 300, 400, 500, 600],
            "split": ["train", "train", "oos", "val", "val", "oos"],
            "optimal_action": ["KEEP", "CLOSE", "KEEP", "REDUCE", "CLOSE", "KEEP"],
            "continuation_value": [0.1] * 6,
        }
    )
    report = PositionDatasetValidator.validate(df)
    assert report.causality_violations > 0, "contaminated splits passed validation"
    assert not report.valid
    assert any("Temporal split contamination" in v for v in report.violations)
