# PURPOSE     : Regression + contract tests for the provisioning control-plane
#               additions: run identity/state truth on train/start,
#               train/progress and train/cancel, and the model inventory
#               read-plane exposed at GET /api/provisioning/models.
# OWNER       : model-provisioning-control-plane lane (owns this file +
#               src/nexus_scalp/web/provisioning_routes.py +
#               src/nexus_scalp/model_provisioning/inventory.py).
# CONSUMES    : nexus_scalp.web.provisioning_routes.register_provisioning_routes
#               on a bare FastAPI app (TestClient); _TrainRun + _run_state +
#               _new_run_id from the same module;
#               nexus_scalp.model_provisioning.inventory.list_model_inventory.
# PROVIDES    : Pins for
#               (1) run_id stability + presence in train/start and train/cancel,
#               (2) the derived run STATE machine (QUEUED/RUNNING/CANCELLING/
#                   COMPLETE/FAILED/CANCELLED) and that CANCELLED is only
#                   reported once the worker acknowledged it,
#               (3) structured progress fields (percent/fold/epoch/loss) being
#                   taken from measured events and never synthesized,
#               (4) inventory: two-plane separation (serving vs studio), exactly
#                   one active row, metadata-only default (no hash reads),
#                   pagination, and fail-closed listing on a broken registry.
# INVariANTS  : no artifact is LOADED into memory (torch weights are never
#               opened); hashing is opt-in; a Studio registry row marked
#               CHAMPION is never reported as the live/active model; the
#               inventory never invents a model, a status or a path.
# EXTEND      : add cases HERE when the provisioning contract grows.
"""Contract pins for the provisioning control-plane additions."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI

from nexus_scalp.model_provisioning import inventory as inv
from nexus_scalp.web import provisioning_routes as routes

_MODELS = "/api/provisioning/models"


# --------------------------------------------------------------------------- #
# run identity + state truth
# --------------------------------------------------------------------------- #


def _run_with_events(events: list[tuple[str, str]]) -> routes._TrainRun:
    """A _TrainRun carrying (stage, status) events, without a worker thread."""
    from nexus_scalp.model_provisioning import TrainingRequest

    run = routes._TrainRun(TrainingRequest(source_file=Path("x.csv")))  # type: ignore[arg-type]
    for stage, status in events:
        run.record(routes.ProgressEvent(stage=stage, status=status))
    return run


def test_new_run_id_is_unique_and_sortable() -> None:
    """Run ids are distinct, nameable and monotonically sequenced."""
    ids = [routes._new_run_id() for _ in range(5)]
    assert len(set(ids)) == 5
    for value in ids:
        assert value.startswith("run-")
        assert value.count("-") >= 3  # run-<stamp>-<pid>-<seq>
    # Same pid, same second: the trailing sequence still orders them.
    assert ids == sorted(ids)


def test_run_state_is_queued_before_any_event() -> None:
    run = _run_with_events([])
    snap = run.snapshot()
    assert snap["state"] == "QUEUED"
    assert snap["phase"] is None
    assert snap["percent"] is None  # never 0-for-unknown


def test_run_state_is_running_after_first_event() -> None:
    run = _run_with_events([("environment", "active")])
    assert run.snapshot()["state"] == "RUNNING"
    assert run.snapshot()["phase"] == "environment"


def test_run_state_cancelling_reflects_request_not_completion() -> None:
    """A cancel REQUEST with the worker still working is CANCELLING, not
    CANCELLED — the operator must not be told a live run is over."""
    run = _run_with_events([("train", "progress")])
    run.cancel.set()
    snap = run.snapshot()
    assert snap["state"] == "CANCELLING"
    assert snap["cancel_requested"] is True
    assert snap["cancel_acknowledged"] is False


def test_run_state_terminal_outcomes_map_to_the_state_machine() -> None:
    cases = {
        "CANCELLED": "CANCELLED",
        "INSTALLED": "COMPLETE",
        "VALIDATION_FAILED": "FAILED",
        "TRAINING_ENV_BLOCKED": "FAILED",
        "DATASET_BLOCKED": "FAILED",
    }
    for outcome, expected in cases.items():
        run = _run_with_events([("train", "done")])
        run.result = {"outcome": outcome}
        run.done.set()
        assert run.snapshot()["state"] == expected, outcome


def test_cancelled_is_only_reported_after_worker_acknowledgement() -> None:
    """The ack flag is what separates 'requested' from 'observed'."""
    run = _run_with_events([("train", "cancelled")])
    run.result = {"outcome": "CANCELLED"}
    run.done.set()
    # Before the worker acked, the snapshot must not claim acknowledgement.
    assert run.snapshot()["cancel_acknowledged"] is False
    run.cancel_acknowledged = True
    assert run.snapshot()["cancel_acknowledged"] is True


def test_progress_fields_come_from_measured_events_only() -> None:
    """percent/fold/epoch/loss are read from ProgressEvent.fraction/metrics.

    Nothing is interpolated: with no numeric fraction the percent stays None,
    and the metric payload is passed through verbatim.
    """
    from nexus_scalp.model_provisioning import TrainingRequest

    run = routes._TrainRun(TrainingRequest(source_file=Path("x.csv")))  # type: ignore[arg-type]
    run.record(routes.ProgressEvent(stage="prepare", status="start"))  # fraction None
    assert run.snapshot()["percent"] is None
    run.record(
        routes.ProgressEvent(
            stage="train",
            status="progress",
            fraction=0.55,
            metrics={"fold": 2, "folds": 6, "epoch": 3, "epochs": 4, "loss": 0.42, "val_loss": 0.5},
        )
    )
    snap = run.snapshot()
    assert snap["percent"] == 55.0
    assert (snap["fold"], snap["total_folds"]) == (2, 6)
    assert (snap["epoch"], snap["total_epochs"]) == (3, 4)
    assert snap["loss"] == 0.42
    assert snap["validation_loss"] == 0.5


def test_progress_prefers_a_payload_that_actually_has_training_counters() -> None:
    """A stage-forwarding payload must not mask the real per-epoch metrics."""
    from nexus_scalp.model_provisioning import TrainingRequest

    run = routes._TrainRun(TrainingRequest(source_file=Path("x.csv")))  # type: ignore[arg-type]
    run.record(
        routes.ProgressEvent(
            stage="train",
            status="progress",
            metrics={"fold": 1, "epoch": 2, "loss": 0.9},
        )
    )
    run.record(
        routes.ProgressEvent(
            stage="verify", status="start", metrics={"stage": "verify", "status": "start"}
        )
    )
    snap = run.snapshot()
    assert snap["loss"] == 0.9
    assert snap["epoch"] == 2


def test_snapshot_carries_run_identity_and_timestamps() -> None:
    run = _run_with_events([("environment", "active")])
    snap = run.snapshot()
    assert snap["run_id"] == run.run_id
    assert snap["started"]
    assert snap["updated"] >= snap["started"]


# --------------------------------------------------------------------------- #
# route-level wiring
# --------------------------------------------------------------------------- #


@pytest.fixture
def client() -> Any:
    from fastapi.testclient import TestClient

    app = FastAPI()
    routes.register_provisioning_routes(
        app, lambda **kw: {"success": False, "error": kw}, lambda *a, **kw: None
    )
    return TestClient(app)


def test_progress_route_reports_run_block_when_idle(client: Any) -> None:
    body = client.get("/api/provisioning/train/progress").json()
    assert body["success"] is True
    assert body["active"] is False
    assert body["events"] == []


def test_cancel_route_reports_no_active_run_when_idle(client: Any) -> None:
    body = client.post("/api/provisioning/train/cancel", json={}).json()
    assert body["success"] is True
    assert body["cancel"] == "NO_ACTIVE_RUN"


def test_progress_route_surfaces_the_structured_run_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """When a run is registered, progress answers with its `run` snapshot."""
    from fastapi.testclient import TestClient

    app = FastAPI()
    routes.register_provisioning_routes(
        app, lambda **kw: {"success": False, "error": kw}, lambda *a, **kw: None
    )
    run = _run_with_events([("train", "progress")])
    run.record(
        routes.ProgressEvent(stage="train", status="progress", fraction=0.25, metrics={"epoch": 1})
    )
    monkeypatch.setattr(routes, "_ACTIVE", run, raising=False)
    body = TestClient(app).get("/api/provisioning/train/progress?after=0").json()
    assert body["active"] is True
    assert body["run"]["run_id"] == run.run_id
    assert body["run"]["state"] == "RUNNING"
    assert body["run"]["percent"] == 25.0


# --------------------------------------------------------------------------- #
# model inventory read-plane
# --------------------------------------------------------------------------- #


def _bundle(root: Path, name: str, *, dim: int = 70, classes: int = 3) -> Path:
    """A minimal bundle directory: model.pt + manifest.json + model.meta.json."""
    d = root / name
    d.mkdir(parents=True)
    (d / "model.pt").write_bytes(b"\x00" * 64)  # contents are never read
    (d / "manifest.json").write_text(json.dumps({"input_dim": dim}), encoding="utf-8")
    (d / "model.meta.json").write_text(
        json.dumps(
            {
                "feature_schema_dimension": dim,
                "num_features": dim,
                "model_head_classes": classes,
                "feature_schema_id": "scalp_v3",
            }
        ),
        encoding="utf-8",
    )
    return d


@pytest.fixture
def workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Hermetic runtime workspace with one serving + one candidate bundle.

    The Studio registry is stubbed out (no DB in this test) so the assertions
    describe the DISK plane deterministically.
    """
    ws = tmp_path / "ws"
    _bundle(ws / "artifacts" / "models" / "scalp" / "XAUUSD" / "70d_liquidity", "", dim=70)
    # The bundle dir above is created via the helper with an empty name; make a
    # second, distinct candidate bundle for pagination/plane coverage.
    _bundle(ws / "artifacts" / "model_generation" / "models", "cand_a", dim=50)
    monkeypatch.setattr(inv, "_studio_registry_rows", lambda **kw: [], raising=True)
    return ws


def test_inventory_is_metadata_only_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """model.pt must not be read unless hashes are explicitly requested."""
    ws = tmp_path / "ws"
    bundle = _bundle(ws / "artifacts" / "model_generation" / "models", "cand_meta", dim=70)
    calls: list[Path] = []

    def _spy(weights: Path, limit: int = 16) -> str | None:
        calls.append(weights)
        return "deadbeefdeadbeef"

    monkeypatch.setattr(inv, "_hash_prefix", _spy)
    monkeypatch.setattr(inv, "_active_artifact_path", lambda: None)
    monkeypatch.setattr(inv, "get_runtime_workspace", lambda: ws, raising=False)
    monkeypatch.setattr(inv, "_studio_registry_rows", lambda **kw: [], raising=True)
    monkeypatch.setattr("nexus_scalp.release.paths.get_runtime_workspace", lambda: ws, raising=True)

    body = inv.list_model_inventory(limit=10)
    assert body["available"] is True
    assert calls == []  # NO artifact was hashed
    row = body["models"][0]
    assert row["hash"] is None
    assert row["hash_pending"] is True
    assert row["dimension"] == 70
    assert row["classes"] == 3
    assert row["artifact"] == str(bundle / "model.pt")

    inv.list_model_inventory(limit=10, include_hashes=True)
    assert calls == [bundle / "model.pt"]  # opt-in reads it exactly once


def test_inventory_lists_real_bundles_with_sidecar_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ws = tmp_path / "ws"
    bundle = _bundle(ws / "artifacts" / "models" / "scalp" / "XAUUSD" / "70d", "", dim=70)
    monkeypatch.setattr(inv, "_active_artifact_path", lambda: bundle / "model.pt")
    monkeypatch.setattr(inv, "_studio_registry_rows", lambda **kw: [], raising=True)
    monkeypatch.setattr("nexus_scalp.release.paths.get_runtime_workspace", lambda: ws, raising=True)

    body = inv.list_model_inventory(limit=10)
    assert body["available"] is True
    assert body["total"] == 1
    row = body["models"][0]
    assert row["active"] is True
    assert row["plane"] == "serving"
    assert row["artifact_exists"] is True
    assert body["planes"]["active_count"] == 1


def test_inventory_never_reports_more_than_one_active(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ws = tmp_path / "ws"
    served = _bundle(ws / "artifacts" / "models" / "scalp" / "XAUUSD" / "70d", "", dim=70)
    _bundle(ws / "artifacts" / "model_generation" / "models", "cand_a", dim=50)
    _bundle(ws / "artifacts" / "model_generation" / "models", "cand_b", dim=50)
    monkeypatch.setattr(inv, "_active_artifact_path", lambda: served / "model.pt")
    monkeypatch.setattr(inv, "_studio_registry_rows", lambda **kw: [], raising=True)
    monkeypatch.setattr("nexus_scalp.release.paths.get_runtime_workspace", lambda: ws, raising=True)

    body = inv.list_model_inventory(limit=50)
    actives = [m for m in body["models"] if m["active"]]
    assert len(actives) == 1
    assert actives[0]["artifact"] == str(served / "model.pt")
    assert body["models"][0]["active"] is True  # active sorts first


def test_inventory_pagination_is_bounded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    for i in range(5):
        _bundle(ws / "artifacts" / "model_generation" / "models", f"cand_{i}", dim=50)
    monkeypatch.setattr(inv, "_active_artifact_path", lambda: None)
    monkeypatch.setattr(inv, "_studio_registry_rows", lambda **kw: [], raising=True)
    monkeypatch.setattr("nexus_scalp.release.paths.get_runtime_workspace", lambda: ws, raising=True)

    body = inv.list_model_inventory(limit=2)
    assert body["total"] == 5
    assert len(body["models"]) == 2
    assert body["limited"] is True
    assert body["limit"] == 2


def test_inventory_fails_closed_when_workspace_is_unresolvable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom() -> Path:
        raise RuntimeError("no workspace")

    monkeypatch.setattr("nexus_scalp.release.paths.get_runtime_workspace", _boom, raising=True)
    body = inv.list_model_inventory(limit=10)
    assert body["available"] is False
    assert body["models"] == []
    assert body["active_artifact"] is None


def test_inventory_studio_rows_never_claim_to_be_the_live_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A Studio registry row marked CHAMPION stays `active: false` unless its
    artifact IS the resolved serving artifact (phase 37 plane separation)."""
    ws = tmp_path / "ws"
    ws.mkdir()
    studio_pt = tmp_path / "studio" / "train_studio_x.pt"
    studio_pt.parent.mkdir(parents=True)
    studio_pt.write_bytes(b"\x00" * 32)

    @dataclass(frozen=True)
    class _Rec:
        id: str = "train_studio_x"
        name: str = "train_studio_x.pt"
        version: str = "1.0.0"
        dimension: int = 70
        weights_path: str = str(studio_pt)
        scaler_path: str = ""
        sha256: str = "a" * 64
        stage: str = "CHAMPION"
        created_at: str = "2026-01-01T00:00:00+00:00"
        loaded_at: str | None = None
        metrics: dict[str, Any] = field(default_factory=lambda: {"final_loss": 0.1})

    monkeypatch.setattr(inv, "_active_artifact_path", lambda: None)
    monkeypatch.setattr("nexus_scalp.release.paths.get_runtime_workspace", lambda: ws, raising=True)
    rows = inv._studio_registry_rows(workspace=ws, active_artifact=None, seen=set())
    # _studio_registry_rows is the pure row builder; assert via it directly.
    assert rows == []  # no live registry in this env -> fail-closed

    # Now exercise the row mapping logic itself with a stubbed registry.
    class _Reg:
        def list_models(self) -> list[Any]:
            return [_Rec()]

    monkeypatch.setattr("nexus_scalp.model_generation.model_registry.get_model_registry", _Reg)
    rows = inv._studio_registry_rows(workspace=ws, active_artifact=None, seen=set())
    assert len(rows) == 1
    row = rows[0]
    assert row["plane"] == "studio"
    assert row["status"] == "CHAMPION"  # studio's own word, reported verbatim
    assert row["active"] is False  # but NOT the live model
    assert row["artifact_exists"] is True
