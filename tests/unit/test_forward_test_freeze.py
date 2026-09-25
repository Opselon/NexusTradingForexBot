"""Unit tests: FORWARD TEST freeze + provenance immutability (CHG-0035).

Covers the user research-completion brief's freeze/isolation contracts:

    §7/§70  ForwardTestFreeze captures model/scaler/strategy/schema/commit
            identity at the cutoff; verified AFTER a run (freeze re-check).
    §8/§10  Future-data isolation: events at/before the cutoff are excluded
            (strict >), events after stream through; the pre-cutoff world
            cannot be influenced by post-cutoff data because the sliced
            source never yields it.
    §34/§71 Provenance immutability: a completed run's snapshot identity is
            unchanged when the ACTIVE model config changes afterwards; the
            frozen artifact dir keeps its own byte copies.
    §76     Empty / future-only ranges produce honest outcomes.

The heavy engine integration (real 70D inference over synthetic tick
streams) lives in tests/integration/test_research_execution_stack.py; these
unit tests exercise the freeze contracts with a stub artifacts bundle so
they run fast and offline.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import torch

from nexus_scalp.research.event_source import TickEvent, TickEventSource
from nexus_scalp.research.forward_test import (
    EXPERIMENT_TYPE,
    ForwardTestExperiment,
    ForwardTestPolicy,
    _resolve_model_version,
)
from nexus_scalp.research.streaming_replay import (
    ModelArtifacts,
    ReplayExecutionConfig,
    ReplaySessionConfig,
    StreamingReplayEngine,
)

# ---------------------------------------------------------------------------
# Stub artifacts + deterministic sources
# ---------------------------------------------------------------------------


class _TinyNet(torch.nn.Module):
    """Deterministic ScalpNet-compatible stand-in (width 70, 4 classes).

    Builds a REAL ScalpNet(num_features=70, num_classes=4, hidden_dim=128),
    then re-initializes its weights with a tiny deterministic seed so the
    loader exercises the EXACT strict path it uses for the production 70D
    bundle — no shape fakes, no subset state-dicts.
    """

    def __init__(self) -> None:
        super().__init__()
        from nexus_scalp.models.scalp_net import ScalpNet

        self.num_features = 70
        self._net = ScalpNet(num_features=70, num_classes=4, hidden_dim=128)
        torch.manual_seed(7)
        for p in self._net.parameters():
            p.data.uniform_(-0.01, 0.01)
        self._net.eval()

    def load_state_dict(self, *a: Any, **k: Any) -> Any:  # delegate to the real net
        return self._net.load_state_dict(*a, **k)

    def state_dict(self, *a: Any, **k: Any) -> Any:  # delegate to the real net
        return self._net.state_dict(*a, **k)

    def eval(self) -> _TinyNet:
        self._net.eval()
        return self

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self._net(x)


@pytest.fixture()
def stub_artifacts(tmp_path: Path) -> ModelArtifacts:
    torch.manual_seed(7)
    net = _TinyNet()
    torch.save(net._net.state_dict(), tmp_path / "model.pt")
    mean = np.zeros(70, dtype=np.float64)
    std = np.ones(70, dtype=np.float64)
    np.savez(tmp_path / "model.scaler.npz", mean=mean, std=std)
    return ModelArtifacts(
        model_path=tmp_path / "model.pt",
        scaler_path=tmp_path / "model.scaler.npz",
        model_fingerprint="f" * 32,
        scaler_fingerprint="s" * 32,
        num_features=70,
        model=net._net,
        scaler_mean=mean,
        scaler_std=std,
    )


def _tick(minutes: int, price: float = 3300.0, spread: float = 0.2) -> TickEvent:
    t0 = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
    ts = t0 + timedelta(minutes=minutes)
    return TickEvent(timestamp=ts, bid=price, ask=price + spread, volume=3.0)


# ---------------------------------------------------------------------------
# §4/§11/§62 — the experiment IS a dedicated FORWARD_TEST identity
# ---------------------------------------------------------------------------


def test_experiment_type_is_dedicated_forward_test(stub_artifacts, tmp_path) -> None:
    assert EXPERIMENT_TYPE == "FORWARD_TEST"
    exp = ForwardTestExperiment.create(
        cutoff=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        model_artifact_path=stub_artifacts.model_path,
        policy_params={"confidence_threshold": 0.35},
        storage_root=tmp_path / "ft",
    )
    assert exp.engine.config.experiment_type == "FORWARD_TEST"
    exported = exp.export_json()
    assert exported["experiment_type"] == "FORWARD_TEST"
    assert exported["freeze"]["cutoff"] == "2026-09-01T12:00:00+00:00"


# ---------------------------------------------------------------------------
# §7 — freeze capture fields
# ---------------------------------------------------------------------------


def test_freeze_captures_full_identity(stub_artifacts, tmp_path) -> None:
    exp = ForwardTestExperiment.create(
        cutoff=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        model_artifact_path=stub_artifacts.model_path,
        policy_params={"confidence_threshold": 0.35, "cooldown_seconds": 3.0},
        strategy_id="scalp_v3_70d",
        strategy_version="1.2.3",
        storage_root=tmp_path / "ft",
    )
    f = exp.freeze
    # frozen-copy fingerprints are the sha256 of the actual frozen bytes
    # (NOT the stub's placeholder identity values — bytes are the truth)
    from nexus_scalp.research.streaming_replay import _sha256_file

    assert f.model_fingerprint == _sha256_file(Path(f.frozen_artifact_dir) / "model.pt")
    assert f.scaler_fingerprint == _sha256_file(Path(f.frozen_artifact_dir) / "model.scaler.npz")
    assert f.model_fingerprint != ""
    assert f.scaler_fingerprint != ""
    assert f.feature_dim == 70
    assert f.schema_id == "scalp_v3"
    assert f.schema_hash != ""
    assert f.strategy_id == "scalp_v3_70d"
    assert f.strategy_version == "1.2.3"
    assert f.strategy_fingerprint != ""
    assert f.execution_model_fingerprint != ""
    # frozen artifact bytes exist on disk (not symlinks to the live path)
    assert (Path(f.frozen_artifact_dir) / "model.pt").is_file()
    assert (Path(f.frozen_artifact_dir) / "freeze.json").is_file()
    meta = json.loads((Path(f.frozen_artifact_dir) / "freeze.json").read_text(encoding="utf-8"))
    assert meta["model_fingerprint"] == f.model_fingerprint


# ---------------------------------------------------------------------------
# §8/§10/§68 — cutoff isolation (future-data boundary)
# ---------------------------------------------------------------------------


def test_cutoff_slicing_is_strictly_greater(stub_artifacts) -> None:
    src = TickEventSource(
        [
            {"timestamp": _tick(0).timestamp, "bid": 1.0, "ask": 1.2},  # pre-cutoff
            {"timestamp": _tick(60).timestamp, "bid": 2.0, "ask": 2.2},  # == cutoff
            {"timestamp": _tick(61).timestamp, "bid": 3.0, "ask": 3.2},  # future
            {"timestamp": _tick(90).timestamp, "bid": 4.0, "ask": 4.2},  # future
        ]
    )
    cutoff = datetime(2026, 9, 1, 1, 0, tzinfo=UTC)
    sliced = ForwardTestPolicy(cutoff).slice_source(src)
    seen = [ev.timestamp for ev in sliced.events()]
    assert len(seen) == 2
    assert all(ts > cutoff for ts in seen)
    # the cutoff event itself is EXCLUDED (evaluation starts strictly after)


def test_future_only_and_empty_ranges_are_honest(stub_artifacts, tmp_path) -> None:
    # future-only: works, zero events
    exp = ForwardTestExperiment.create(
        cutoff=datetime(2026, 9, 1, 0, 0, tzinfo=UTC),
        model_artifact_path=stub_artifacts.model_path,
        storage_root=tmp_path / "ft-future",
    )
    empty_src = TickEventSource(
        [{"timestamp": _tick(-30).timestamp, "bid": 1.0, "ask": 1.2}]  # entirely pre-cutoff
    )
    result = exp.run(empty_src)
    assert result["result"]["events_seen"] == 0
    assert result["result"]["trade_count"] == 0
    assert result["freeze_verified_after_run"] is True


# ---------------------------------------------------------------------------
# §70/§71 — freeze verification + provenance immutability
# ---------------------------------------------------------------------------


def test_freeze_verified_after_run_and_drift_detected(stub_artifacts, tmp_path) -> None:
    exp = ForwardTestExperiment.create(
        cutoff=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        model_artifact_path=stub_artifacts.model_path,
        storage_root=tmp_path / "ft-drift",
    )
    src = TickEventSource(
        [
            {"timestamp": _tick(61).timestamp, "bid": 3300.0, "ask": 3300.2},
            {"timestamp": _tick(62).timestamp, "bid": 3300.1, "ask": 3300.3},
        ]
    )
    result = exp.run(src)
    assert result["freeze_verified_after_run"] is True

    # Tamper with the FROZEN copy -> verify_freeze must raise (FREEZE_DRIFTED)
    frozen_model = Path(exp.freeze.frozen_artifact_dir) / "model.pt"
    frozen_model.write_bytes(frozen_model.read_bytes() + b"tampered")
    with pytest.raises(RuntimeError, match="FREEZE_DRIFTED"):
        exp.verify_freeze()


def test_completed_run_identity_survives_active_champion_change(stub_artifacts, tmp_path) -> None:
    """§34/§71: rerun-config change must NOT rewrite the completed result."""
    exp = ForwardTestExperiment.create(
        cutoff=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        model_artifact_path=stub_artifacts.model_path,
        storage_root=tmp_path / "ft-immutable",
    )
    src = TickEventSource(
        [
            {"timestamp": _tick(61).timestamp, "bid": 3300.0, "ask": 3300.2},
            {"timestamp": _tick(62).timestamp, "bid": 3300.5, "ask": 3300.7},
        ]
    )
    result = exp.run(src)
    result_path = exp.storage_dir / "result.json"
    before = json.loads(result_path.read_text(encoding="utf-8"))

    # "activate a different model": mutate a NEW live artifact; the frozen
    # experiment dir + result must remain byte-identical.
    torch.manual_seed(99)
    torch.save(_TinyNet().eval().state_dict(), tmp_path / "model.pt")  # overwrite LIVE path

    after = json.loads(result_path.read_text(encoding="utf-8"))
    assert after == before
    assert after["freeze"]["model_fingerprint"] == result["freeze"]["model_fingerprint"]
    # frozen bytes unchanged (verify passes again)
    assert exp.verify_freeze() is True


# ---------------------------------------------------------------------------
# §36 — model version from authoritative metadata only
# ---------------------------------------------------------------------------


def test_model_version_never_invented_from_filename(stub_artifacts, tmp_path) -> None:
    # no meta side-car -> NOT_RECORDED (empty), even though dir says 70d_liquidity
    assert _resolve_model_version(stub_artifacts.model_path) == ""
    meta = stub_artifacts.model_path.with_suffix(".meta.json")
    meta.write_text(
        json.dumps({"feature_schema_id": "scalp_v3", "model_version": "7.7.7"}), encoding="utf-8"
    )
    assert _resolve_model_version(stub_artifacts.model_path) == "7.7.7"


# ---------------------------------------------------------------------------
# Engine determinism on the shared engine (unit-level; integration extends)
# ---------------------------------------------------------------------------


def test_replay_engine_deterministic_on_same_source(stub_artifacts) -> None:
    cfg = ReplaySessionConfig(
        model_artifact_path=str(stub_artifacts.model_path),
        policy_params={"confidence_threshold": 0.35},
        execution=ReplayExecutionConfig(),
        decide_on="every_tick",
    )
    engine = StreamingReplayEngine(cfg, artifacts=stub_artifacts)
    records = [
        {
            "timestamp": _tick(i).timestamp,
            "bid": 3300.0 + (i % 7) * 0.05,
            "ask": 3300.2 + (i % 7) * 0.05,
            "volume": 2.0,
        }
        for i in range(1, 260)
    ]
    r1 = engine.run(TickEventSource(list(records)), run_id="DET-1")
    r2 = engine.run(TickEventSource(list(records)), run_id="DET-2")
    # different run ids, SAME ledger/event hashes (§16)
    assert r1.event_hash == r2.event_hash
    assert r1.ledger_hash == r2.ledger_hash
    assert r1.trades == r2.trades
    assert r1.orders == r2.orders
