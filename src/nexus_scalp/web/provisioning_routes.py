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
    GET  /api/provisioning/datasets          allowed-root dataset browser (additive, GAP-6)

Privacy invariant preserved: the train route accepts a SERVER-SIDE PATH to a
file the user already placed on this machine (or a prior upload endpoint);
market data is never POSTed through HTTP bodies and never leaves the host.
Training itself is fully local. Security note: ``file`` is validated to be a
readable .csv/.parquet under the configured data import root — arbitrary
paths are refused (no path-traversal into the training pipeline). The
datasets browser is READ-ONLY over the SAME allowlist: stat metadata only
(contents never read), .csv/.parquet only, capped at 500 per root
newest-mtime-first, and an optional ``?root=`` filter that must itself
resolve inside an allowed root (``DATASETS_ROOT_REJECTED`` otherwise).
"""

from __future__ import annotations

import re as _re
import threading
from datetime import UTC, datetime
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
from nexus_scalp.position_adviser.paths import resolve_within_trusted_roots

logger = get_logger("nexus_scalp.web.provisioning_routes")

_IMPORT_ROOTS_ENV = "NEXUS_IMPORT_ROOTS"  # os.pathsep-separated allowed roots
_DATASET_MAX_FILES_PER_ROOT = 500  # datasets browser: per-root cap, newest mtime first
_DATASET_EXTS = (".csv", ".parquet")  # datasets browser: the ONLY listed extensions


class _TrainRun:
    """One active background training run (single-flight: one at a time)."""

    def __init__(
        self,
        request: TrainingRequest,
        *,
        source: str = "file",
        dataset_adapter: Any = None,
        prepare_environment: bool = False,
    ) -> None:
        # Route-level choices (kept OUT of the shared TrainingRequest — the
        # pipeline owns that dataclass; this web layer owns its own fields).
        self.source = source
        self.dataset_adapter = dataset_adapter
        self.prepare_environment = prepare_environment
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
_OFFICIAL_ACTIVE = False  # single-flight official download/install (shared reservation)


def _backend(raw: Any) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError("accepted: auto | cpu | cuda")
    value = raw.strip().lower()
    if value in ("", "auto"):
        return None
    if value not in ("cpu", "cuda"):
        raise ValueError("accepted: auto | cpu | cuda")
    return value


def _allowed_import_roots() -> list[Path]:
    """One resolved allowlist for containment and operator copy instructions."""
    import os

    roots_env = str(os.environ.get(_IMPORT_ROOTS_ENV, "")).strip()
    roots: list[Path] = []
    seen: set[Path] = set()
    for raw in [*roots_env.split(os.pathsep), "data/imports", "data/raw"]:
        cleaned = raw.strip()
        if not cleaned:
            continue
        # SEC (py/path-injection #1149): validate shape before resolving
        # operator-supplied env paths so traversal and shell tokens are refused.
        if ".." in cleaned or "\x00" in cleaned or not _IMPORT_PATH_SHAPE.fullmatch(cleaned):
            continue
        try:
            resolved = Path(cleaned).expanduser().resolve()
        except (ValueError, RuntimeError):
            continue
        if resolved not in seen:
            seen.add(resolved)
            roots.append(resolved)
    return roots


def _validate_import_path_shape(s: str) -> None:
    """SEC (py/path-injection #1098): reject a raw import path that can carry a
    path component, BEFORE it is resolved.

    The ``..`` / null-byte checks already ran, so this is the shape barrier:
    every component must be an identifier segment, which admits legitimate
    nested imports (``data/raw/xauusd_M1.csv.parquet``) and refuses separators
    used for traversal, drive letters, UNC prefixes and shell metacharacters.
    The message never echoes the payload.
    """
    if not _IMPORT_PATH_SHAPE.fullmatch(s):
        raise ValueError("import path has characters outside the safe set")


def _resolve_within_import_roots(raw: str) -> Path:
    """Canonical untainted resolution for a user-supplied import file path.

    Delegates to ``resolve_within_trusted_roots`` so the returned Path is the
    canonical sanitizer output: absolute, symlink-followed and strictly
    contained in one of the allowed import roots. An escaping or unresolvable
    value raises ``ValueError`` fail-closed.

    The import is at module scope on purpose: resolving a path lazily pulled
    ``position_adviser`` (and torch) into the middle of a request/test where
    ``threading.Thread`` may be monkeypatched, crashing tqdm's monitor import.
    """
    s = str(raw).strip()
    _validate_import_path_shape(s)
    resolved = resolve_within_trusted_roots(s, _allowed_import_roots())
    if resolved is None:
        raise ValueError("import path is outside allowed import roots")
    return resolved


#: A relative or absolute filename whose every component is an identifier
#: segment. Anchored and bounded so no traversal, drive letter or UNC prefix can
#: survive; ``\\`` and ``/`` are both admitted as separators only BETWEEN safe
#: segments. ``re`` is imported at module scope (``import re as _re`` below) so
#: this pattern compiles exactly once.
_IMPORT_PATH_SHAPE = _re.compile(
    r"(?:[A-Za-z]:[\\/]{1,2})?/?"  # optional Windows drive and/or POSIX root slash
    r"(?:[A-Za-z0-9_ -][A-Za-z0-9_ . -]{0,127}[\\/])*"  # safe nested dirs (spaces permitted)
    r"[A-Za-z0-9_ -][A-Za-z0-9_ . -]{0,191}"  # final file name (incl. extension dots and spaces)
)


def _allowed_import_path(raw: str) -> Path:
    """Confine the user-supplied training-file path to the import roots.

    Defense layers (closes the CodeQL py/path-injection taint + a real
    prefix-bypass class a naive str.startswith check carries — "data/rawx"
    starts-with "data/raw"):
      1. reject null bytes, empty values and any ``..`` traversal segment
         BEFORE any path is constructed or resolved;
      2. reduce the raw string to a whitelist-only relative-or-absolute form so
         it cannot carry a path component at all (this is what lets the resolve
         below run on a value that is already untainted, rather than relying on
         a suppression at the sink);
      3. resolve to an absolute real path (symlinks followed);
      4. containment via Path.is_relative_to (not string prefix);
      5. the pipeline then only ever READS the file (training input).
    """
    import os

    s = str(raw).strip()
    if not s or "\x00" in s:
        raise ValueError("empty or malformed file path")
    parts = Path(s).parts
    if any(part == ".." for part in parts) or (os.altsep and ".." in s.split(os.altsep)):
        raise ValueError("path traversal segments are refused")
    # The string must be a plain name under an import root or an absolute path
    # the operator chose; either way every component must be a safe identifier
    # segment, so separators can smuggle no traversal.
    _validate_import_path_shape(s)
    # Resolve through the canonical safe-path sanitizer so the resulting Path
    # is recognized as untainted (canonicalized + in-root) before the suffix /
    # containment checks below read it.
    p = _resolve_within_import_roots(s)
    if p.suffix.lower() not in (".csv", ".parquet"):
        raise ValueError("unsupported file type (accepted: .csv, .parquet)")
    roots = _allowed_import_roots()
    for r in roots:
        if p.is_relative_to(r) and p != r:
            if not p.exists():
                raise ValueError(f"file not found: {p}")
            return p
    raise ValueError(
        "file outside allowed import roots "
        f"({', '.join(str(r) for r in roots)}; extend via {_IMPORT_ROOTS_ENV})"
    )


def _scan_dataset_root(root: Path) -> dict[str, Any]:
    """Read-only listing of ONE allowed root for GET /api/provisioning/datasets.

    A missing root answers ``exists:false`` — never an error. Security
    invariants (same class as ``_allowed_import_path``):
      1. containment, NOT prefix: every directory entry is resolved (symlinks
         followed) and admitted only via ``resolved.is_relative_to(root)`` —
         a symlink escaping the root is skipped, never listed nor descended;
      2. the recursive scan admits ``.csv``/``.parquet`` regular files ONLY
         (case-insensitive); directories are traversed, never listed;
      3. file CONTENTS are never read — ``stat`` metadata only (size, mtime);
      4. hard cap ``_DATASET_MAX_FILES_PER_ROOT`` per root, sorted mtime
         descending (newest first), ties beyond the cap dropped silently.
    """
    exists = root.is_dir()  # Path.is_dir swallows OSError -> missing/unreadable = absent
    found: list[tuple[float, int, Path]] = []
    if exists:
        stack = [root]
        visited: set[Path] = {root}
        while stack:
            current = stack.pop()
            try:
                entries = sorted(current.iterdir())
            except OSError:
                continue  # unreadable directory: keep what was listed so far
            for entry in entries:
                try:
                    resolved = entry.resolve()
                    if not resolved.is_relative_to(root):
                        continue  # symlink (or traversal) escaping the root
                    if resolved.is_dir():
                        if resolved not in visited:  # visited-set breaks symlink cycles
                            visited.add(resolved)
                            stack.append(resolved)
                    elif resolved.is_file() and resolved.suffix.lower() in _DATASET_EXTS:
                        stat = resolved.stat()
                        found.append((stat.st_mtime, stat.st_size, resolved))
                except OSError:
                    continue  # deleted mid-scan / stat failure: skip, never fail the list
    found.sort(key=lambda item: item[0], reverse=True)  # newest mtime first
    files = [
        {
            "name": path.name,
            "path": str(path),
            "ext": path.suffix[1:].lower(),
            "size_bytes": size,
            "modified_iso": datetime.fromtimestamp(mtime, tz=UTC).isoformat(timespec="seconds"),
        }
        for mtime, size, path in found[:_DATASET_MAX_FILES_PER_ROOT]
    ]
    return {"root": str(root), "exists": exists, "count": len(files), "files": files}


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
                "allowed_import_roots": [str(root) for root in _allowed_import_roots()],
            }
        except Exception as exc:
            _log_err(exc, "provisioning status failed", endpoint="/api/provisioning/status")
            return _err(code="PROVISIONING_STATUS_ERROR")

    @router.get("/api/provisioning/datasets")
    def provisioning_datasets(root: str | None = None) -> dict[str, Any]:
        """READ-ONLY dataset browser over the allowed import roots (GAP-6).

        Legacy raw-JSON lane (NO v1 envelope): one listing entry per allowed
        root — ``exists:false`` for a missing root, never an error — plus
        ``total`` = summed per-root counts. Security invariants mirror
        ``_allowed_import_path``:
          1. roots come from ``_allowed_import_roots()`` — this route NEVER
             widens the allowlist (extend it via ``NEXUS_IMPORT_ROOTS``);
          2. the optional ``?root=`` filter is resolved (traversal collapsed,
             symlinks followed) and must be CONTAINED in an allowed root via
             ``is_relative_to`` (containment, not string prefix) — otherwise
             the typed ``DATASETS_ROOT_REJECTED`` envelope;
          3. listings are stat metadata only (contents never read), capped at
             500/root newest-mtime-first, entries escaping their root skipped;
          4. unexpected failures are logged server-side and answer the stable
             ``PROVISIONING_DATASETS_ERROR`` envelope — no raw exception text
             (py/stack-trace-exposure discipline).
        """
        import os

        try:
            allowed = _allowed_import_roots()
            targets = allowed
            if root is not None and str(root).strip():
                raw = str(root).strip()
                candidate: Path | None = None
                if "\x00" not in raw:
                    # SEC (py/path-injection #1149): resolve through the
                    # canonical helper so the value scanned below is the
                    # sanitizer's output — an escaping or unresolvable root
                    # stays ``None`` and is rejected as outside, never scanned.
                    candidate = resolve_within_trusted_roots(raw, allowed)
                    if candidate is None:
                        # Naming an allowed root VERBATIM is legitimate (it
                        # narrows the listing to that root). Compared with pure
                        # string normalization — never a resolve of the raw
                        # value — so no tainted path is ever constructed.
                        _norm = os.path.normcase(os.path.normpath(raw))
                        for _root in allowed:
                            if _norm == os.path.normcase(str(_root)):
                                candidate = _root
                                break
                if candidate is None or not any(
                    candidate.is_relative_to(allowed_root) for allowed_root in allowed
                ):
                    return _err(
                        code="DATASETS_ROOT_REJECTED",
                        message=(
                            "root must resolve inside an allowed import root "
                            f"({', '.join(str(r) for r in allowed)})"
                        ),
                        remedy=f"pick an allowed root or extend {_IMPORT_ROOTS_ENV}",
                    )
                targets = [candidate]
            listings = [_scan_dataset_root(target) for target in targets]
            return {
                "success": True,
                "roots": listings,
                "total": sum(entry["count"] for entry in listings),
            }
        except Exception as exc:
            _log_err(exc, "dataset listing failed", endpoint="/api/provisioning/datasets")
            return _err(code="PROVISIONING_DATASETS_ERROR")

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
            be = _backend(backend)
        except ValueError:
            return _err(code="TRAIN_BACKEND_INVALID", message="accepted: auto | cpu | cuda")
        try:
            manager = TrainingEnvironmentManager()
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
        try:
            backend = _backend(body.get("backend"))
        except ValueError:
            return _err(code="TRAIN_BACKEND_INVALID", message="accepted: auto | cpu | cuda")
        with _ACTIVE_LOCK:
            if _ACTIVE is not None and not _ACTIVE.done.is_set():
                return _err(
                    code="TRAIN_ALREADY_RUNNING", message="training or preparation is active"
                )
            if _INSTALL_ACTIVE:
                return _err(
                    code="INSTALL_ALREADY_RUNNING",
                    message="an environment install is already running",
                )
            if _OFFICIAL_ACTIVE:
                return _err(
                    code="OFFICIAL_ALREADY_RUNNING",
                    message="official model download or installation is active",
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
        global _OFFICIAL_ACTIVE  # noqa: PLW0603 (single-flight registry)
        if getattr(app.state, "engine", None) is not None:
            return _err(
                code="MODEL_INSTALL_ENGINE_RUNNING",
                message="Stop the engine before installing a model; use the stopped-engine setup or CLI.",
            )
        body = payload or {}
        with _ACTIVE_LOCK:
            if _ACTIVE is not None and not _ACTIVE.done.is_set():
                return _err(
                    code="TRAIN_ALREADY_RUNNING",
                    message="training or preparation is active",
                )
            if _INSTALL_ACTIVE:
                return _err(
                    code="INSTALL_ALREADY_RUNNING",
                    message="an environment install is already running",
                )
            if _OFFICIAL_ACTIVE:
                return _err(
                    code="OFFICIAL_ALREADY_RUNNING",
                    message="an official model download or install is already running",
                )
            _OFFICIAL_ACTIVE = True
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
        finally:
            with _ACTIVE_LOCK:
                _OFFICIAL_ACTIVE = False

    @router.post("/api/provisioning/train/start")
    def provisioning_train_start(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        global _ACTIVE  # noqa: PLW0603 (single-flight registry)
        body = payload or {}
        source = str(body.get("source", "file") or "file")
        if source not in ("file", "broker"):
            return _err(code="TRAIN_INPUT_REJECTED", detail="source must be file or broker")
        try:
            req_backend = _backend(body.get("backend"))
        except ValueError:
            return _err(code="TRAIN_BACKEND_INVALID", message="accepted: auto | cpu | cuda")
        try:
            file = None if source == "broker" else _allowed_import_path(str(body.get("file", "")))
        except ValueError as ve:
            return _err(code="TRAIN_INPUT_REJECTED", detail=str(ve))
        from nexus_scalp.model_provisioning.dataset_source import validate_dataset_request

        symbol = str(body.get("symbol", "XAUUSD"))
        timeframe = str(body.get("timeframe", "M1"))
        try:
            validate_dataset_request(
                source=source,
                source_file=file,
                symbol=symbol,
                timeframe=timeframe,
                candles=body.get("candles"),
            )
            for key, default in (("folds", 6), ("epochs", 4)):
                value = body.get(key, default)
                if type(value) is not int or not 1 <= value <= 1000:
                    raise ValueError(f"{key} must be an integer between 1 and 1000")
        except (ValueError, OSError) as exc:
            return _err(code="TRAIN_INPUT_REJECTED", detail=str(exc))
        state = getattr(router, "state", None)
        engine = getattr(state, "engine", None)
        adapter = getattr(engine, "adapter", None) if engine is not None else None
        adapter = adapter or getattr(state, "training_history_adapter", None)
        if source == "broker" and engine is not None and adapter is None:
            return _err(
                code="TRAIN_INPUT_REJECTED",
                detail="Active engine has no borrowable history provider; use a file",
            )
        with _ACTIVE_LOCK:
            if _OFFICIAL_ACTIVE:
                return _err(
                    code="OFFICIAL_ALREADY_RUNNING",
                    message="official model download or installation is active",
                )
            if _INSTALL_ACTIVE:
                return _err(
                    code="INSTALL_ALREADY_RUNNING", message="environment installation is active"
                )
            if _ACTIVE is not None and not _ACTIVE.done.is_set():
                return _err(code="TRAIN_ALREADY_RUNNING", detail="one local training run at a time")
            candles = body.get("candles")
            if source == "broker" and candles is None:
                return _err(
                    code="TRAIN_INPUT_REJECTED",
                    detail="broker source requires an explicit candle count (3000..100000)",
                )
            request = TrainingRequest(
                source_file=file,
                candles=int(candles) if candles else None,
                folds=int(body.get("folds", 6)),
                epochs=int(body.get("epochs", 4)),
                install=bool(body.get("install", True)),
                backend=req_backend,
            )
            run = _TrainRun(
                request,
                source=source,
                dataset_adapter=adapter,
                prepare_environment=body.get("prepare_environment") is True,
            )
            _ACTIVE = run

        def _worker() -> None:
            stage = "environment"
            try:
                from nexus_scalp.model_provisioning.training_env import TrainingEnvironmentManager

                run.record(
                    ProgressEvent(
                        stage="environment",
                        status="active",
                        message="Checking training environment",
                    )
                )
                manager = TrainingEnvironmentManager()
                gate = manager.status(backend=request.backend)
                if run.cancel.is_set():
                    run.result = {"outcome": "CANCELLED"}
                    run.record(
                        ProgressEvent(
                            stage=stage, status="cancelled", message="Preparation cancelled"
                        )
                    )
                    return
                if not gate.training_ready and run.prepare_environment:
                    run.record(
                        ProgressEvent(
                            stage="environment",
                            status="active",
                            message="Installing pinned dependencies (explicit consent)",
                        )
                    )
                    manager.install(backend=request.backend)
                    gate = manager.status(backend=request.backend)
                if not gate.training_ready:
                    failures = gate.failing()
                    run.result = {
                        "outcome": "TRAINING_ENV_BLOCKED",
                        "reason": "Training environment is not ready",
                        "remedy": failures[0].remedy
                        if failures
                        else "Re-check the training environment",
                        "report": gate.as_dict(),
                    }
                    run.record(
                        ProgressEvent(
                            stage="environment", status="failed", message="TRAINING_ENV_BLOCKED"
                        )
                    )
                    return
                if run.cancel.is_set():
                    run.result = {"outcome": "CANCELLED"}
                    run.record(
                        ProgressEvent(
                            stage=stage, status="cancelled", message="Preparation cancelled"
                        )
                    )
                    return
                run.record(
                    ProgressEvent(
                        stage="environment", status="done", message="Training environment READY"
                    )
                )
                from nexus_scalp.model_provisioning.dataset_source import prepare_training_dataset
                from nexus_scalp.model_provisioning.pipeline import TrainingCancelledError

                stage = "dataset"
                try:
                    request.source_file = prepare_training_dataset(
                        source=run.source,
                        symbol=symbol,
                        timeframe=timeframe,
                        source_file=request.source_file,
                        adapter=run.dataset_adapter,
                        candles=request.candles,
                        progress=run.record,
                        cancel_event=run.cancel,
                    )
                except TrainingCancelledError:
                    run.result = {"outcome": "CANCELLED"}
                    run.record(
                        ProgressEvent(
                            stage=stage, status="cancelled", message="Dataset preparation cancelled"
                        )
                    )
                    return
                except Exception as exc:
                    _log_err(
                        exc, "dataset preparation failed", endpoint="/api/provisioning/train/start"
                    )
                    run.result = {
                        "outcome": "DATASET_BLOCKED",
                        "reason": "Dataset is not usable — check the CSV/Parquet file or connected broker history",
                    }
                    run.record(
                        ProgressEvent(stage=stage, status="failed", message="DATASET_BLOCKED")
                    )
                    return
                stage = "train"
                from nexus_scalp.model_provisioning.pipeline import train_local_model

                run.result = train_local_model(request, progress=run.record)
            except Exception as exc:  # defensive: train_local_model should not raise
                # Web-exposed payload carries the SAFE CATEGORY only (full
                # detail stays server-side in the log) — py/stack-trace-exposure.
                category = type(exc).__name__
                logger.error("[PROVISION-WEB] event=TRAIN_WORKER_CRASH category=%s", category)
                from nexus_scalp.model_provisioning.training_env import TrainingEnvironmentError

                run.result = {
                    "outcome": "TRAINING_ENV_BLOCKED"
                    if stage == "environment"
                    else "VALIDATION_FAILED",
                    "reason": f"unexpected {category}",
                }
                if isinstance(exc, TrainingEnvironmentError):
                    run.result.update(code=exc.code.value, reason=exc.detail, remedy=exc.remedy)
                run.record(
                    ProgressEvent(
                        stage=stage, status="failed", message=str(run.result.get("code", category))
                    )
                )
            finally:
                run.done.set()

        try:
            threading.Thread(target=_worker, name="nexus-local-train", daemon=True).start()
        except Exception as exc:
            with _ACTIVE_LOCK:
                _ACTIVE = None
            _log_err(exc, "training worker launch failed", endpoint="/api/provisioning/train/start")
            return _err(code="TRAIN_WORKER_START_ERROR")
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
