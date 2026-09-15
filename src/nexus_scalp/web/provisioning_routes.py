"""Web API surface for first-run model provisioning (BUG-293 redesign).

GUI and CLI are thin views over the SAME domain service
(``nexus_scalp.model_provisioning``) — no parallel logic lives here.

Routes (registered under the authenticated operator surface):

    GET  /api/provisioning/status            slot classification + recommendation
    GET  /api/provisioning/environment       python/torch/cuda/GPU probe (PATH B readiness)
    POST /api/provisioning/official          PATH A: download + verify + install (blocking)
    POST /api/provisioning/train/start       PATH B: start local training (background task)
    GET  /api/provisioning/train/progress    real progress events (never synthesized)
    POST /api/provisioning/train/cancel      request cancel (epoch boundary)

Privacy invariant preserved: the train route accepts a SERVER-SIDE PATH to a
file the user already placed on this machine (or a prior upload endpoint);
market data is never POSTed through HTTP bodies and never leaves the host.
Training itself is fully local. Security note: ``file`` is validated to be a
readable .csv/.parquet under the configured data import root — arbitrary
paths are refused (no path-traversal into the training pipeline).
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from nexus_scalp.model_provisioning import (
    LifecycleState,
    OfficialBundleError,
    ProgressEvent,
    TrainingRequest,
)
from nexus_scalp.model_provisioning import (
    service as prov,
)
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.web.provisioning_routes")

_IMPORT_ROOTS_ENV = "NEXUS_IMPORT_ROOTS"  # os.pathsep-separated allowed roots


class _TrainRun:
    """One active background training run (single-flight: one at a time)."""

    def __init__(self, request: TrainingRequest) -> None:
        self.request = request
        self.cancel = threading.Event()
        request.cancel_event = self.cancel
        self.events: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self.done = threading.Event()
        self.result: dict[str, Any] = {}

    def record(self, ev: ProgressEvent) -> None:
        with self._lock:
            self.events.append(ev.as_dict())

    def tail(self, after: int) -> list[dict[str, Any]]:
        with self._lock:
            return self.events[after:]


_ACTIVE: _TrainRun | None = None
_ACTIVE_LOCK = threading.Lock()


def _allowed_import_path(raw: str) -> Path:
    p = Path(str(raw)).expanduser().resolve()
    if p.suffix.lower() not in (".csv", ".parquet"):
        raise ValueError("unsupported file type (accepted: .csv, .parquet)")
    roots_env = str(__import__("os").environ.get(_IMPORT_ROOTS_ENV, "")).strip()
    roots = [Path(r).resolve() for r in roots_env.split(__import__("os").pathsep) if r.strip()]
    roots.append(Path("data/imports").resolve())
    roots.append(Path("data/raw").resolve())
    if not any(str(p).startswith(str(r)) for r in roots):
        raise ValueError(
            "file outside allowed import roots "
            f"({', '.join(str(r) for r in roots)}; extend via {_IMPORT_ROOTS_ENV})"
        )
    if not p.exists():
        raise ValueError(f"file not found: {p}")
    return p


def register_provisioning_routes(app: Any, _err: Any, _log_err: Any) -> None:
    """Mount the provisioning endpoints on the web app (same seams as peers)."""
    router = app

    @router.get("/api/provisioning/status")
    def provisioning_status() -> dict[str, Any]:
        try:
            coord = prov.FirstRunCoordinator()
            return {
                "success": True,
                "slot": coord.slot().as_dict(),
                "recommended": coord.recommended_action(),
                "provisioner": prov.read_provisioner_state(),
            }
        except Exception as exc:
            _log_err(exc, "provisioning status failed", endpoint="/api/provisioning/status")
            return _err(code="PROVISIONING_STATUS_ERROR")

    @router.get("/api/provisioning/environment")
    def provisioning_environment() -> dict[str, Any]:
        from nexus_scalp.model_provisioning.pipeline import detect_ml_environment

        return {"success": True, "environment": detect_ml_environment()}

    @router.post("/api/provisioning/official")
    def provisioning_official(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = payload or {}
        try:
            coord = prov.FirstRunCoordinator()
            base = str(body.get("base_url", "") or "")
            if base:
                from nexus_scalp.model_provisioning import OfficialBundleSource

                coord = prov.FirstRunCoordinator(official=OfficialBundleSource(base))
            out = coord.download_official()
            return {"success": True, "ok": bool(out.get("servable")), **out}
        except OfficialBundleError as exc:
            # Fail-closed: nothing was installed; report the exact step code.
            prov.write_provisioner_state(
                LifecycleState.REJECTED.value, path="official", error=str(exc)
            )
            return _err(code="OFFICIAL_BUNDLE_REJECTED", detail=str(exc))
        except Exception as exc:
            _log_err(exc, "official provisioning failed", endpoint="/api/provisioning/official")
            return _err(code="PROVISIONING_OFFICIAL_ERROR")

    @router.post("/api/provisioning/train/start")
    def provisioning_train_start(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        global _ACTIVE  # noqa: PLW0603 (single-flight registry)
        body = payload or {}
        try:
            file = _allowed_import_path(str(body.get("file", "")))
        except ValueError as ve:
            return _err(code="TRAIN_INPUT_REJECTED", detail=str(ve))
        with _ACTIVE_LOCK:
            if _ACTIVE is not None and not _ACTIVE.done.is_set():
                return _err(code="TRAIN_ALREADY_RUNNING", detail="one local training run at a time")
            candles = body.get("candles")
            request = TrainingRequest(
                source_file=file,
                candles=int(candles) if candles else None,
                folds=int(body.get("folds", 6)),
                epochs=int(body.get("epochs", 4)),
                install=bool(body.get("install", True)),
            )
            run = _TrainRun(request)
            _ACTIVE = run

        from nexus_scalp.model_provisioning.pipeline import train_local_model

        def _worker() -> None:
            try:
                run.result = train_local_model(request, progress=run.record)
            except Exception as exc:  # defensive: train_local_model should not raise
                run.result = {"outcome": "VALIDATION_FAILED", "reason": str(exc)[:300]}
                run.record(ProgressEvent(stage="train", status="failed", message=str(exc)[:300]))
            finally:
                run.done.set()

        threading.Thread(target=_worker, name="nexus-local-train", daemon=True).start()
        return {"success": True, "started": True}

    @router.get("/api/provisioning/train/progress")
    def provisioning_train_progress(after: int = 0) -> dict[str, Any]:
        with _ACTIVE_LOCK:
            run = _ACTIVE
        if run is None:
            return {
                "success": True,
                "active": False,
                "events": [],
                "result": prov.read_provisioner_state(),
            }
        return {
            "success": True,
            "active": not run.done.is_set(),
            "cancelled": run.cancel.is_set(),
            "events": run.tail(max(0, int(after))),
            "count": len(run.events),
            "result": run.result
            or ({"state": prov.read_provisioner_state().get("state")} if run.done.is_set() else {}),
        }

    @router.post("/api/provisioning/train/cancel")
    def provisioning_train_cancel() -> dict[str, Any]:
        with _ACTIVE_LOCK:
            run = _ACTIVE
        if run is None or run.done.is_set():
            return {"success": True, "cancel": "NO_ACTIVE_RUN"}
        run.cancel.set()
        return {
            "success": True,
            "cancel": "REQUESTED",
            "note": "observed at the next epoch boundary",
        }
