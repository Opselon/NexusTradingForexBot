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

REMEDY_MISMATCH = (
    "The prepared training environment is a different interpreter — run the printed "
    "training_command under that interpreter."
)

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
_INSTALL_ACTIVE = False  # single-flight env install (explicit user action)


def _allowed_import_path(raw: str) -> Path:
    """Confine the user-supplied training-file path to the import roots.

    Defense layers (closes the CodeQL py/path-injection taint + a real
    prefix-bypass class a naive str.startswith check carries — "data/rawx"
    starts-with "data/raw"):
      1. reject null bytes and any ``..`` traversal segment BEFORE resolving;
      2. resolve to an absolute real path (symlinks followed);
      3. containment via os.path.relpath + explicit '..' scan (relative walk,
         not string prefix);
      4. the pipeline then only ever READS the file (training input).
    """
    import os

    s = str(raw).strip()
    if not s or "\x00" in s:
        raise ValueError("empty or malformed file path")
    parts = Path(s).parts
    if any(part == ".." for part in parts) or (os.altsep and ".." in s.split(os.altsep)):
        raise ValueError("path traversal segments are refused")
    p = Path(s).expanduser().resolve()  # codeql[py/path-injection] traversal segments
    # rejected above; the containment loop below admits ONLY paths under an
    # operator-configured import root (relpath-walk, not string prefix), and
    # the pipeline reads the file — never writes, never executes it.
    if p.suffix.lower() not in (".csv", ".parquet"):
        raise ValueError("unsupported file type (accepted: .csv, .parquet)")
    roots_env = str(os.environ.get(_IMPORT_ROOTS_ENV, "")).strip()
    roots = [Path(r).expanduser().resolve() for r in roots_env.split(os.pathsep) if r.strip()]
    roots.append(Path("data/imports").resolve())
    roots.append(Path("data/raw").resolve())
    for r in roots:
        if p.is_relative_to(r) and p != r:
            if not p.exists():
                raise ValueError(f"file not found: {p}")
            return p
    raise ValueError(
        "file outside allowed import roots "
        f"({', '.join(str(r) for r in roots)}; extend via {_IMPORT_ROOTS_ENV})"
    )


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
    def provisioning_environment(backend: str = "auto") -> dict[str, Any]:
        """DISCOVERY ONLY (BUG-301 contract): the resolved training environment
        as a checklist — python / environment / pytorch / gpu / training_ready.
        NEVER installs anything; provisioning is the explicit POST
        /environment/install action. Typed checks + remedies, no raw
        exception text (py/stack-trace-exposure discipline)."""
        from nexus_scalp.model_provisioning.training_env import (
            TrainingEnvironmentError,
            TrainingEnvironmentManager,
        )

        try:
            manager = TrainingEnvironmentManager()
            be = None if backend in ("", "auto") else backend
            rep = manager.status(backend=be)
            legacy = {
                "python": rep.python.get("version"),
                "torch": rep.pytorch.get("detected"),
                "cuda": bool(rep.gpu.get("available")),
                "gpu_name": rep.gpu.get("name"),
            }
            return {"success": True, "report": rep.as_dict(), "environment": legacy}
        except TrainingEnvironmentError as exc:
            return _err(
                code=exc.code.value, step="environment", message=exc.detail, remedy=exc.remedy
            )
        except Exception as exc:
            _log_err(exc, "environment discovery failed", endpoint="/api/provisioning/environment")
            return _err(code="PROVISIONING_ENVIRONMENT_ERROR")

    @router.post("/api/provisioning/environment/install")
    def provisioning_environment_install(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Explicit OPT-IN training-stack provisioning (pip, pinned variants).

        Runs the TrainingEnvironmentManager.install ladder (python -> venv ->
        pinned torch variant for the chosen backend -> fresh status). Typed
        taxonomy: a network/index/version/permission failure returns its
        exact code + remedy, and training NEVER auto-starts from here."""
        from nexus_scalp.model_provisioning.training_env import (
            TrainingEnvironmentError,
            TrainingEnvironmentManager,
        )

        global _INSTALL_ACTIVE  # noqa: PLW0603 (single-flight registry)
        body = payload or {}
        backend = str(body.get("backend", "") or "") or None
        with _ACTIVE_LOCK:
            if _INSTALL_ACTIVE:
                return _err(
                    code="INSTALL_ALREADY_RUNNING",
                    message="an environment install is already running",
                )
            _INSTALL_ACTIVE = True
        try:
            manager = TrainingEnvironmentManager()
            rep = manager.install(backend=backend)
            return {"success": rep.training_ready, "report": rep.as_dict()}
        except TrainingEnvironmentError as exc:
            # Typed + actionable; the taxonomy is the CONTRACT (never a
            # generic VALIDATION_FAILED hiding the real cause).
            return _err(
                code=exc.code.value, step="environment", message=exc.detail, remedy=exc.remedy
            )
        except Exception as exc:
            _log_err(
                exc, "environment install failed", endpoint="/api/provisioning/environment/install"
            )
            return _err(code="PROVISIONING_INSTALL_ERROR")
        finally:
            with _ACTIVE_LOCK:
                _INSTALL_ACTIVE = False

    @router.post("/api/provisioning/official")
    def provisioning_official(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if getattr(app.state, "engine", None) is not None:
            return _err(
                code="MODEL_INSTALL_ENGINE_RUNNING",
                message="Stop the engine before installing a model; use the stopped-engine setup or CLI.",
            )
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
            # Fail-closed: nothing was installed. The client gets the stable
            # step CODE (safe enum); the full verification detail (URLs,
            # digests, wrapped OS text) stays server-side (py/stack-trace-
            # exposure discipline).
            prov.write_provisioner_state(
                LifecycleState.REJECTED.value, path="official", error=f"{exc.code}: {exc.detail}"
            )
            logger.error("[PROVISION-WEB] event=OFFICIAL_REJECTED code=%s", exc.code)
            return _err(
                code="OFFICIAL_BUNDLE_REJECTED",
                step=exc.code,
                message=(
                    "Official model is not published yet. Train locally or retry after publication."
                    if exc.code == "OFFICIAL_MODEL_NOT_PUBLISHED"
                    else "Verification is incomplete: install the supported runtime and retry."
                    if exc.code == "VERIFICATION_PENDING"
                    else "official bundle failed verification — nothing was installed "
                    "(run `nexus model-official` for the full reason)"
                ),
            )
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
            req_backend = str(body.get("backend", "") or "").strip().lower() or None
            if req_backend not in (None, "cpu", "cuda"):
                return _err(
                    code="TRAIN_BACKEND_INVALID",
                    message=f"accepted: cpu | cuda (got {req_backend!r})",
                )
            request = TrainingRequest(
                source_file=file,
                candles=int(candles) if candles else None,
                folds=int(body.get("folds", 6)),
                epochs=int(body.get("epochs", 4)),
                install=bool(body.get("install", True)),
                backend=req_backend,
            )
            run = _TrainRun(request)
            _ACTIVE = run
        chosen_backend = request.backend

        # TRAINING GATE (BUG-301): the resolved environment must be READY in
        # THIS process before training may start — the exact checklist + the
        # blocking reason return here, never a silent VALIDATION_FAILED later.
        from nexus_scalp.model_provisioning.training_env import TrainingEnvironmentManager

        gate = TrainingEnvironmentManager().status(backend=chosen_backend)
        if not gate.training_ready or not gate.in_process_ready:
            with _ACTIVE_LOCK:
                _ACTIVE = None
            return _err(
                code="TRAINING_ENV_BLOCKED",
                step="environment",
                message="Training cannot start. "
                + "; ".join(f"{c.stage}={c.code or 'FAIL'}" for c in gate.failing())[:300]
                or "environment ready but outside this process",
                remedy=(
                    gate.failing()[0].remedy
                    if gate.failing()
                    else gate.training_command or REMEDY_MISMATCH
                ),
                report=gate.as_dict(),
            )

        from nexus_scalp.model_provisioning.pipeline import train_local_model

        def _worker() -> None:
            try:
                run.result = train_local_model(request, progress=run.record)
            except Exception as exc:  # defensive: train_local_model should not raise
                # Web-exposed payload carries the SAFE CATEGORY only (full
                # detail stays server-side in the log) — py/stack-trace-exposure.
                category = type(exc).__name__
                logger.error("[PROVISION-WEB] event=TRAIN_WORKER_CRASH category=%s", category)
                run.result = {"outcome": "VALIDATION_FAILED", "reason": f"unexpected {category}"}
                run.record(ProgressEvent(stage="train", status="failed", message=category))
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
