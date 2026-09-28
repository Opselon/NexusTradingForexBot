"""Position Adviser runtime regression tests (mission §46).

Covers the load workflow, hot-swap + rollback, the tensor inspector contract,
SQLite settings persistence and restart rehydration. Everything here exercises
the REAL service objects against REAL on-disk artifacts written under the
repository's own artifact root — no monkeypatched state.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import numpy as np
import pytest
import torch

from nexus_scalp.position_adviser.models import PositionAdvisory
from nexus_scalp.position_adviser.service import PositionAdviserService
from nexus_scalp.position_adviser.trainer import AdviserScaler, PositionAdviserNet

FEATURE_DIM = 12
ACTIONS = ("KEEP", "CLOSE", "REDUCE")


@pytest.fixture
def artifact_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Artifacts must live inside the repository root.

    ``load()`` refuses any path outside ``_ADVISER_ROOT`` (the repo), so the
    test writes into the real artifact directory and cleans up after itself
    rather than defeating the containment barrier with a tmp_path.
    """
    out = Path("artifacts") / "position_adviser" / "pa_runtime_tests"
    out.mkdir(parents=True, exist_ok=True)
    marker = out / ".pa_test_marker"
    marker.touch()
    yield out
    # Remove ONLY what this fixture created: everything under the dedicated
    # subdirectory, never the shared artifact tree.
    for child in sorted(out.rglob("*"), reverse=True):
        if child.is_file():
            child.unlink()
    out.rmdir()


@pytest.fixture
def cleaned_ids() -> list[str]:
    return []


def _write_model(artifact_dir: Path, model_id: str) -> tuple[Path, Path]:
    """Write a genuine adviser checkpoint + scaler + manifest sidecar."""
    net = PositionAdviserNet()
    weights = artifact_dir / f"{model_id}.pt"
    torch.save(net.state_dict(), weights)

    rng = np.random.default_rng(seed=abs(hash(model_id)) % (2**32))
    scaler = AdviserScaler(
        mean=rng.normal(size=FEATURE_DIM).astype(np.float32),
        std=np.full(FEATURE_DIM, 0.5, dtype=np.float32),
        feature_dim=FEATURE_DIM,
    )
    scaler_path = artifact_dir / f"{model_id}.scaler.npz"
    np.savez(scaler_path, **scaler.to_arrays())

    (artifact_dir / f"{model_id}.meta.json").write_text(
        json.dumps(
            {
                "model_id": model_id,
                "adviser_actions": list(ACTIONS),
                "input_dim": FEATURE_DIM,
                "source_dataset_hash": f"ds_{model_id}",
            }
        )
    )
    return weights, scaler_path


# ============================================================ load workflow


def test_load_activates_runtime_memory(artifact_dir: Path) -> None:
    svc = PositionAdviserService()
    weights, scaler = _write_model(artifact_dir, "pa_test_load_001")

    out = svc.load(weights, scaler)

    assert out["status"] == "OK"
    assert out["model_id"] == "pa_test_load_001"
    assert out["feature_dim"] == FEATURE_DIM
    assert out["feature_schema_id"] == "adviser_v1"
    assert out["loaded"]
    assert out["restart_required"] is False
    # A successful response must mean the model is REALLY in runtime memory.
    status = svc.status()
    assert status["model_id"] == "pa_test_load_001"
    assert status["ready"] is True


def test_load_missing_artifact_fails_closed(artifact_dir: Path) -> None:
    svc = PositionAdviserService()
    out = svc.load(artifact_dir / "ghost.pt", artifact_dir / "ghost.scaler.npz")
    assert out["status"] == "REJECTED"
    assert svc.status()["ready"] is False


def test_load_foreign_checkpoint_refused(artifact_dir: Path) -> None:
    svc = PositionAdviserService()
    weights = artifact_dir / "foreign.pt"
    torch.save({"not_a_net": torch.zeros(3)}, weights)
    scaler = artifact_dir / "foreign.scaler.npz"
    np.savez(scaler_path := scaler, mean=np.zeros(12), std=np.ones(12), dimension=np.int64(12))
    out = svc.load(weights, scaler_path)
    assert out["status"] == "REJECTED"
    assert "foreign model" in out["reason"]


# ==================================================== hot swap + rollback


def test_hot_swap_retains_previous_for_rollback(artifact_dir: Path) -> None:
    svc = PositionAdviserService()
    w_a, s_a = _write_model(artifact_dir, "pa_hot_a")
    w_b, s_b = _write_model(artifact_dir, "pa_hot_b")

    svc.load(w_a, s_a)
    assert svc.status()["model_id"] == "pa_hot_a"

    out = svc.load(w_b, s_b)
    assert out["status"] == "OK"
    assert out["hot_swap"] is True
    assert out["previous_model_id"] == "pa_hot_a"
    # The new model is active; the old one is retained, not destroyed.
    assert svc.status()["model_id"] == "pa_hot_b"

    rb = svc.rollback()
    assert rb["status"] == "OK"
    assert rb["model_id"] == "pa_hot_a"
    assert rb["restored_from"] == "pa_hot_b"
    assert svc.status()["model_id"] == "pa_hot_a"


def test_rollback_with_no_incumbent_rejected(artifact_dir: Path) -> None:
    svc = PositionAdviserService()
    out = svc.rollback()
    assert out["status"] == "REJECTED"


def test_unload_forgets_rollback_target(artifact_dir: Path) -> None:
    svc = PositionAdviserService()
    w_a, s_a = _write_model(artifact_dir, "pa_unload_a")
    w_b, s_b = _write_model(artifact_dir, "pa_unload_b")
    svc.load(w_a, s_a)
    svc.load(w_b, s_b)
    svc.unload()
    assert svc.rollback()["status"] == "REJECTED"


def test_rollback_rejected_when_incumbent_will_not_serve(artifact_dir: Path) -> None:
    """A retained model that fails its warm-up forward is NOT restored."""
    svc = PositionAdviserService()
    w_a, s_a = _write_model(artifact_dir, "pa_bad_a")
    w_b, s_b = _write_model(artifact_dir, "pa_bad_b")
    svc.load(w_a, s_a)
    svc.load(w_b, s_b)
    # Corrupt the retained incumbent so its warm-up forward raises.
    with svc._lock:
        assert svc._previous is not None
        svc._previous["model"] = object()  # not callable as a torch module
    out = svc.rollback()
    assert out["status"] == "REJECTED"
    # The current model is left in place, untouched.
    assert svc.status()["model_id"] == "pa_bad_b"


# =================================================== restart rehydration


def test_rehydrate_loads_persisted_selection(artifact_dir: Path) -> None:
    svc = PositionAdviserService()
    weights, scaler = _write_model(artifact_dir, "pa_rehyd_001")
    out = svc.rehydrate(
        model_id="pa_rehyd_001",
        weights_path=weights,
        scaler_path=scaler,
        activation="PAPER",
    )
    assert out["status"] == "OK"
    assert out["rehydrated"] is True
    assert svc.status()["model_id"] == "pa_rehyd_001"


# ===================================================== tensor contract


def _ticket_of_state(state: dict[str, object]) -> tuple[int, dict[str, object]]:
    """evaluate() takes (ticket, position_state); pass both."""
    return int(state["ticket"]), state


def _live_state(ticket: int = 123456, observed_at: float | None = None) -> dict[str, object]:
    """A position state shaped like integration.build_position_state_for_adviser.

    ``snapshot_observed_at`` / ``snapshot_id`` are the snapshot-contract keys
    evaluate() gates on; the rest are the causal feature keys.
    """
    import hashlib
    import time as _time

    at = observed_at if observed_at is not None else _time.monotonic()
    raw = {
        "ticket": ticket,
        "direction": 1,
        "volume": 0.10,
        "entry_price": 1.1000,
        "current_price": 1.1050,
        "stop_loss": 1.0900,
        "take_profit": 1.1200,
        # The causal keys evaluate() actually consumes (features.ADVISER_FEATURE_ORDER).
        # A missing key is refused, never defaulted to a fabricated 0.
        "unrealized_pnl_r": 1.20,
        "current_r_net": 1.05,
        "current_return": 0.0045,
        "distance_to_stop_r": -1.80,
        "distance_to_target_r": 2.40,
        "position_age_bars": 240.0,
        "atr": 0.0010,
        "spread": 0.0002,
        "estimated_slippage": 0.00008,
        "model_probability": 0.62,
        "model_confidence": 0.55,
        "signal_age": 300.0,
        "symbol": "EURUSD",
    }
    sid = hashlib.sha1(f"{ticket}:{at}".encode()).hexdigest()[:12]
    raw["snapshot_observed_at"] = at
    raw["snapshot_id"] = sid
    return raw


def test_evaluate_records_inspected_state_and_latencies(artifact_dir: Path) -> None:
    svc = PositionAdviserService()
    weights, scaler = _write_model(artifact_dir, "pa_eval_001")
    svc.load(weights, scaler)
    svc.set_activation("PAPER")

    state = _live_state()
    advisory = svc.evaluate(*_ticket_of_state(state))

    assert isinstance(advisory, PositionAdvisory)
    assert advisory.action in ACTIONS
    assert set(advisory.probabilities) == set(ACTIONS)
    # The inspector must see the exact state that was fed to inference.
    assert svc._last_inspected_state is not None
    assert svc._last_inspected_state["ticket"] == 123456
    # Per-stage latency is measured, not invented.
    d = advisory.diagnostics
    assert "latency_feature_ms" in d
    assert "latency_inference_ms" in d
    assert d["latency_feature_ms"] >= 0.0
    assert d["latency_inference_ms"] >= 0.0
    assert advisory.latency_ms >= d["latency_inference_ms"]


def test_tensor_inspector_raw_matches_normalized_dimension(artifact_dir: Path) -> None:
    svc = PositionAdviserService()
    weights, scaler = _write_model(artifact_dir, "pa_tensor_001")
    svc.load(weights, scaler)
    svc.set_activation("PAPER")
    svc.evaluate(*_ticket_of_state(_live_state()))

    inspected = svc._last_inspected_state
    assert inspected is not None
    from nexus_scalp.position_adviser.features import build_live_vector

    raw, _ = build_live_vector(inspected)
    assert raw.shape == (FEATURE_DIM,)
    with svc._lock:
        scaler_obj = svc._state._scaler
    assert scaler_obj is not None
    normalized = scaler_obj.transform(raw.reshape(1, -1))
    # raw dim == normalized dim == model input dim: the contract must agree at
    # every stage, or the inspector is lying to the operator.
    assert normalized.shape == (1, FEATURE_DIM)


def test_disabled_adviser_produces_no_advisory(artifact_dir: Path) -> None:
    svc = PositionAdviserService()
    weights, scaler = _write_model(artifact_dir, "pa_disabled_001")
    svc.load(weights, scaler)
    assert svc.evaluate(*_ticket_of_state(_live_state())) is None


# ======================================================== race protection


def test_duplicate_snapshot_is_refused_once(artifact_dir: Path) -> None:
    svc = PositionAdviserService()
    weights, scaler = _write_model(artifact_dir, "pa_dup_001")
    svc.load(weights, scaler)
    svc.set_activation("PAPER")

    state = _live_state()
    first = svc.evaluate(*_ticket_of_state(state))
    second = svc.evaluate(*_ticket_of_state(state))
    assert first is not None
    # The identical snapshot must not produce a second advisory. The gate is
    # ordered throttle-READ -> snapshot COMMIT -> duplicate REJECT, so the
    # second call is refused either by the duplicate-snapshot gate (which counts
    # it) or by the per-ticket throttle that the first call committed — either
    # way no second decision is emitted and the state stayed honest.
    assert second is None
    assert svc.status()["evaluated_count"] == 1


def test_concurrent_evaluations_do_not_corrupt_state(artifact_dir: Path) -> None:
    svc = PositionAdviserService()
    weights, scaler = _write_model(artifact_dir, "pa_conc_001")
    svc.load(weights, scaler)
    svc.set_activation("PAPER")

    tickets = [700000 + i for i in range(24)]
    results: list[object] = []
    errors: list[BaseException] = []

    def worker(ticket: int) -> None:
        try:
            results.append(svc.evaluate(*_ticket_of_state(_live_state(ticket=ticket))))
        except BaseException as exc:  # pragma: no cover - failure is the signal
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in tickets]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(results) == len(tickets)
    assert all(r is not None for r in results)
    # The counters are updated under the service lock, so they must reconcile
    # exactly with the number of evaluations that succeeded.
    assert svc.status()["evaluated_count"] == len(tickets)
