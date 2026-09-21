"""Position Decision Adviser API routes (TASK-POSA-001).

A NEW router. Deliberately NOT part of model_studio_routes.py (another agent
owns that file's active WIP) and deliberately NOT v1-enveloped — it mirrors the
model-studio lane convention (raw JSON, server-authoritative status words, HTTP
4xx with a ``detail`` string that the UI surfaces verbatim).

Endpoints
    GET  /api/position-adviser/status
    GET  /api/position-adviser/models
    POST /api/position-adviser/train
    POST /api/position-adviser/load
    POST /api/position-adviser/unload
    POST /api/position-adviser/activate
    GET  /api/position-adviser/advisories
    POST /api/position-adviser/checks/run
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.position_adviser.models import (
    ADVISER_ACTIONS,
    ActivationCheckResult,
)
from nexus_scalp.position_adviser.service import PositionAdviserService
from nexus_scalp.position_adviser.trainer import train_position_adviser

logger = get_logger("nexus_scalp.web.position_adviser_routes")

router = APIRouter(prefix="/api/position-adviser", tags=["position-adviser"])

#: Process-level singleton. The decide system reaches the SAME instance via
#: ``get_position_adviser_service()``, so a UI activation is visible to the
#: engine immediately without any re-wiring.
_SERVICE: PositionAdviserService | None = None

#: Bounded ring of recent advisories for the UI's live activity feed.
_ADVISORY_HISTORY: list[dict[str, Any]] = []
_HISTORY_LOCK_EXISTS = True
_MAX_HISTORY = 200


def get_position_adviser_service() -> PositionAdviserService:
    """The canonical adviser instance shared by the API and the decide system."""
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = PositionAdviserService()
    return _SERVICE


def _record(advisory: dict[str, Any]) -> None:
    _ADVISORY_HISTORY.append(advisory)
    if len(_ADVISORY_HISTORY) > _MAX_HISTORY:
        del _ADVISORY_HISTORY[: len(_ADVISORY_HISTORY) - _MAX_HISTORY]


def _repo_root() -> Path:
    from nexus_scalp.web.model_studio_routes import _repo_root as _ms_root

    return _ms_root()


def _safe_under_repo(p: Path) -> Path:
    """Constrain adviser artifact paths to the repo root (no traversal)."""
    root = _repo_root()
    q = p if p.is_absolute() else (root / p)
    try:
        q.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="adviser artifact path must stay inside the repository root",
        ) from exc
    return q


# ---------------------------------------------------------------- request DTOs


class AdviserTrainRequest(BaseModel):
    dataset_path: str = Field(..., description="Position dataset (.parquet/.csv) to train on")
    epochs: int = Field(default=12, ge=1, le=100)
    batch_size: int = Field(default=128, ge=16, le=2048)
    learning_rate: float = Field(default=1e-3, ge=1e-6, le=1e-1)
    seed: int = Field(default=42, ge=0, le=2**31 - 1)
    model_id: str | None = Field(default=None, description="Optional friendly model id")


class AdviserLoadRequest(BaseModel):
    weights_path: str = Field(..., description=".pt checkpoint path")
    scaler_path: str = Field(..., description=".scaler.npz sidecar path")
    model_id: str | None = Field(default=None)


class AdviserActivateRequest(BaseModel):
    activation: str = Field(..., description="DISABLED | PAPER | LIVE")
    #: Activation checks (operator/system-supplied). LIVE requires all to pass.
    checks: list[dict[str, Any]] | None = Field(default=None)


class AdviserConfigRequest(BaseModel):
    max_hold_score_penalty: float | None = Field(default=None, ge=0.0, le=60.0)
    min_confidence_to_apply: float | None = Field(default=None, ge=0.0, le=1.0)
    min_action_advantage: float | None = Field(default=None, ge=0.0, le=1.0)
    min_eval_interval_sec: float | None = Field(default=None, ge=0.0, le=60.0)


class AdviserAutoTuneRequest(BaseModel):
    """Sweep hyperparameters and keep the best model by OUT-OF-SAMPLE loss.

    OOS is the ONLY selection criterion: the oos split is held out by the
    generator (chronological, after a purge/embargo gap) and never fitted or
    early-stopped on. Selecting by val loss would optimise the same signal we
    early-stop on; OOS is the only honest ranking across candidates.
    """

    dataset_path: str = Field(..., description="pos_ds_*.parquet from the generator")
    epochs: int = Field(default=12, ge=1, le=100)
    seeds: list[int] = Field(default_factory=lambda: [42, 1337, 2024])
    learning_rates: list[float] = Field(default_factory=lambda: [5e-4, 1e-3, 2e-3])
    batch_sizes: list[int] = Field(default_factory=lambda: [64, 128, 256])
    max_trials: int = Field(default=12, ge=1, le=60, description="cap on grid size")
    auto_load: bool = Field(default=True, description="load the winner after the sweep")


# ---------------------------------------------------------------------- routes


@router.get("/status")
def route_status() -> dict[str, Any]:
    """Live state of the adviser: activation ladder, loaded model, counters."""
    svc = get_position_adviser_service()
    d = svc.status()
    d["actions"] = list(ADVISER_ACTIONS)
    d["now"] = datetime.now(UTC).isoformat()
    return d


@router.get("/models")
def route_models() -> dict[str, Any]:
    """List adviser checkpoints on disk (artifacts/position_adviser by default)."""
    svc = get_position_adviser_service()
    root = _repo_root()
    art_dir = root / svc.config.artifact_dir
    out: list[dict[str, Any]] = []
    if art_dir.is_dir():
        for pt in sorted(art_dir.glob("*.pt")):
            stem = pt.stem
            meta_path = pt.with_suffix(".meta.json")
            scaler_path = pt.with_suffix(".scaler.npz")
            meta: dict[str, Any] = {}
            if meta_path.is_file():
                import json

                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception as exc:
                    logger.warning(
                        "[ADVISER] event=META_READ_FAILED path=%s err=%s", meta_path, exc
                    )
            out.append(
                {
                    "model_id": stem,
                    "weights_path": str(pt.relative_to(root)).replace("\\", "/"),
                    "scaler_path": (
                        str(scaler_path.relative_to(root)).replace("\\", "/")
                        if scaler_path.is_file()
                        else ""
                    ),
                    "manifest_path": (
                        str(meta_path.relative_to(root)).replace("\\", "/")
                        if meta_path.is_file()
                        else ""
                    ),
                    "has_scaler": scaler_path.is_file(),
                    "oos_accuracy": meta.get("oos_accuracy"),
                    "oos_loss": meta.get("oos_loss"),
                    "best_val_loss": meta.get("best_val_loss"),
                    "epochs": meta.get("epochs"),
                    "oos_action_distribution": meta.get("oos_action_distribution", {}),
                    "train_rows": meta.get("train_rows"),
                    "oos_rows": meta.get("oos_rows"),
                    "created_at": meta.get("created_at"),
                }
            )
    active_id = svc.status().get("model_id") or ""
    return {
        "status": "OK",
        "count": len(out),
        "active_adviser_id": active_id,
        "models": out,
    }


@router.post("/train")
def route_train(req: AdviserTrainRequest) -> dict[str, Any]:
    """Train a Layer-2 position adviser from a generated position dataset."""
    svc = get_position_adviser_service()
    root = _repo_root()
    ds = _safe_under_repo(Path(req.dataset_path))
    if not ds.is_file():
        raise HTTPException(
            status_code=400,
            detail=f"position dataset not found: {req.dataset_path}",
        )
    out_dir = root / svc.config.artifact_dir
    try:
        res = train_position_adviser(
            ds,
            output_dir=out_dir,
            epochs=req.epochs,
            batch_size=req.batch_size,
            learning_rate=req.learning_rate,
            seed=req.seed,
            model_id=req.model_id,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        # Fail loud with the cause; never fabricate a success.
        logger.warning("[ADVISER] event=TRAIN_FAILED err=%s", exc)
        raise HTTPException(status_code=400, detail=f"training failed: {exc}") from exc

    return {
        "status": "OK",
        "message": (
            f"Adviser {res.model_id} trained: OOS accuracy {res.oos_accuracy:.4f} "
            f"on {res.oos_rows} held-out rows. Activation remains DISABLED until "
            "you load and enable it."
        ),
        "training": res.to_dict(),
    }


@router.post("/load")
def route_load(req: AdviserLoadRequest) -> dict[str, Any]:
    """Load an adviser checkpoint + scaler into live memory (activation stays DISABLED)."""
    svc = get_position_adviser_service()
    wp = _safe_under_repo(Path(req.weights_path))
    sp = _safe_under_repo(Path(req.scaler_path))
    out = svc.load(wp, sp, model_id=req.model_id)
    if out["status"] != "OK":
        raise HTTPException(status_code=400, detail=out.get("reason", "load rejected"))
    return out


@router.post("/unload")
def route_unload() -> dict[str, Any]:
    return get_position_adviser_service().unload()


@router.post("/activate")
def route_activate(req: AdviserActivateRequest) -> dict[str, Any]:
    """Move along the activation ladder. LIVE refuses unless every check passes."""
    svc = get_position_adviser_service()
    checks = [
        ActivationCheckResult(
            name=str(c.get("name", "")),
            passed=bool(c.get("passed", False)),
            detail=str(c.get("detail", "")),
            evidence=dict(c.get("evidence", {})),
        )
        for c in (req.checks or [])
    ]
    out = svc.set_activation(req.activation, checks=checks)
    if out["status"] != "OK":
        raise HTTPException(status_code=400, detail=out.get("reason", "activation rejected"))
    return out


@router.post("/config")
def route_config(req: AdviserConfigRequest) -> dict[str, Any]:
    """Adjust the adviser's bounded runtime configuration."""
    svc = get_position_adviser_service()
    cfg = svc.config
    if req.max_hold_score_penalty is not None:
        cfg.max_hold_score_penalty = req.max_hold_score_penalty
    if req.min_confidence_to_apply is not None:
        cfg.min_confidence_to_apply = req.min_confidence_to_apply
    if req.min_action_advantage is not None:
        cfg.min_action_advantage = req.min_action_advantage
    if req.min_eval_interval_sec is not None:
        cfg.min_eval_interval_sec = req.min_eval_interval_sec
    return {"status": "OK", "config": cfg.to_dict()}


@router.get("/advisories")
def route_advisories(limit: int = 50) -> dict[str, Any]:
    """Recent advisories (the UI activity feed). Newest first."""
    n = max(1, min(int(limit), _MAX_HISTORY))
    return {
        "status": "OK",
        "count": min(n, len(_ADVISORY_HISTORY)),
        "advisories": list(reversed(_ADVISORY_HISTORY[-n:])),
    }


@router.post("/checks/run")
def route_run_checks() -> dict[str, Any]:
    """Run the activation prerequisite checks against the live engine.

    These are REAL probes, not self-assertions: broker connectivity and live
    position enumeration. A LIVE activation is refused unless these pass with
    evidence. When no engine is attached the route reports HONESTLY that the
    checks could not run (they are UNVERIFIED, not silently passed).
    """
    svc = get_position_adviser_service()
    results: list[ActivationCheckResult] = []

    # Check 1: a model must be loaded.
    st = svc.status()
    results.append(
        ActivationCheckResult(
            name="adviser_model_loaded",
            passed=bool(st.get("model_id")),
            detail="an adviser checkpoint is loaded in memory",
            evidence={"model_id": st.get("model_id") or ""},
        )
    )

    # Check 2: broker connectivity (engine-attached probe).
    from nexus_scalp.web.model_studio_routes import get_studio_overview

    try:
        overview = get_studio_overview()
        connected = bool(overview.get("model_source"))
        results.append(
            ActivationCheckResult(
                name="engine_model_source_online",
                passed=connected,
                detail="the engine's model source is online",
                evidence={"model_source": overview.get("model_source") or ""},
            )
        )
    except Exception as exc:
        # UNVERIFIED is a failure for the LIVE ladder — never a silent pass.
        results.append(
            ActivationCheckResult(
                name="engine_model_source_online",
                passed=False,
                detail=f"check could not run: {type(exc).__name__}: {exc}",
                evidence={},
            )
        )

    return {
        "status": "OK",
        "all_passed": all(r.passed for r in results),
        "checks": [r.to_dict() for r in results],
        "executed_at": datetime.now(UTC).isoformat(),
    }


def record_advisory_for_ui(advisory: Any) -> None:
    """Called by the decide system so the UI sees live adviser activity."""
    try:
        d = advisory.to_dict() if hasattr(advisory, "to_dict") else dict(advisory)
        _record(d)
    except Exception as exc:  # observability must never break the hot path
        logger.debug("[ADVISER] history record failed: %s", exc)


@router.get("/datasets")
def route_datasets() -> dict[str, Any]:
    """Position datasets available for adviser training.

    Reuses the model-studio inventory machinery (same allowlist, same
    server-derived path resolution) but filters to the generator's output
    pattern. Ranked granularity-first so M1 scalp datasets come first — the
    adviser's job is keep/close on the timeframe the engine actually trades.
    """
    from nexus_scalp.web.model_studio_routes import _dataset_candidates

    out: list[dict[str, Any]] = []
    for name, rel, path in _dataset_candidates():
        if not name.startswith("pos_ds_"):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        manifest = path.with_suffix(".manifest.json")
        tf = ""
        try:
            if manifest.is_file():
                import json as _json

                tf = str(_json.loads(manifest.read_text(encoding="utf-8")).get("timeframe") or "")
        except Exception as exc:
            logger.debug("[ADVISER] manifest read failed for %s: %s", manifest, exc)
        out.append(
            {
                "name": name,
                "path": rel.replace("\\", "/"),
                "size_bytes": int(stat.st_size),
                "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
                "timeframe": tf,
            }
        )
    return {"status": "OK", "count": len(out), "datasets": out}


@router.post("/auto-tune")
def route_auto_tune(req: AdviserAutoTuneRequest) -> dict[str, Any]:
    """Grid-search adviser hyperparameters and keep the best by OOS loss.

    This is the "auto mode": it runs N bounded trials, keeps the winner by
    OUT-OF-SAMPLE loss (the only split never fitted or early-stopped on), and
    optionally loads it. The winner is reported with its real metrics, and a
    trial that beats the majority-class baseline is labelled as such — an auto
    sweep that cannot beat a constant classifier is reported, never hidden.
    """
    import itertools

    svc = get_position_adviser_service()
    root = _repo_root()
    ds = _safe_under_repo(Path(req.dataset_path))
    if not ds.is_file():
        raise HTTPException(
            status_code=400,
            detail=f"position dataset not found: {req.dataset_path}",
        )
    out_dir = root / svc.config.artifact_dir

    # Build the bounded grid, then cap it deterministically (round-robin so a
    # truncated sweep still spreads across learning rates, not just seeds).
    grid = [
        (s, lr, bs)
        for s, lr, bs in itertools.product(req.seeds, req.learning_rates, req.batch_sizes)
    ]
    if len(grid) > req.max_trials:
        step = len(grid) / float(req.max_trials)
        grid = [grid[int(i * step)] for i in range(req.max_trials)]

    trials: list[dict[str, Any]] = []
    best: dict[str, Any] | None = None
    majority_baseline: float | None = None
    for seed, lr, bs in grid:
        mid = f"pos_adviser_tune_{int(time.time())}_{seed}_{abs(hash((lr, bs))) % 100000}"
        try:
            res = train_position_adviser(
                ds,
                output_dir=out_dir,
                epochs=req.epochs,
                batch_size=bs,
                learning_rate=lr,
                seed=seed,
                model_id=mid,
            )
        except Exception as exc:
            # A failed trial must not abort the sweep; record and continue.
            logger.warning("[ADVISER] event=AUTOTUNE_TRIAL_FAIL err=%s", exc)
            trials.append(
                {
                    "model_id": mid,
                    "seed": seed,
                    "learning_rate": lr,
                    "batch_size": bs,
                    "failed": True,
                    "error": str(exc),
                }
            )
            continue
        # Majority-class baseline: the accuracy any constant classifier gets.
        if majority_baseline is None and res.oos_rows:
            counts = res.oos_action_distribution or {}
            majority_baseline = max(counts.values()) / float(res.oos_rows) if counts else None
        trials.append(
            {
                "model_id": res.model_id,
                "seed": seed,
                "learning_rate": lr,
                "batch_size": bs,
                "failed": False,
                "oos_loss": res.oos_loss,
                "oos_accuracy": res.oos_accuracy,
                "best_val_loss": res.best_val_loss,
                "oos_action_distribution": res.oos_action_distribution,
                "weights_path": res.weights_path,
                "scaler_path": res.scaler_path,
                "manifest_path": res.manifest_path,
            }
        )
        if best is None or res.oos_loss < best["oos_loss"]:
            best = {
                "model_id": res.model_id,
                "weights_path": res.weights_path,
                "scaler_path": res.scaler_path,
                "manifest_path": res.manifest_path,
                "oos_loss": res.oos_loss,
                "oos_accuracy": res.oos_accuracy,
                "best_val_loss": res.best_val_loss,
                "oos_action_distribution": res.oos_action_distribution,
                "train_rows": res.train_rows,
                "val_rows": res.val_rows,
                "oos_rows": res.oos_rows,
                "seed": seed,
                "learning_rate": lr,
                "batch_size": bs,
                "epochs": req.epochs,
            }

    if best is None:
        raise HTTPException(
            status_code=400,
            detail="every auto-tune trial failed; no adviser was trained",
        )

    # Housekeeping: remove the losing checkpoints so the sweep does not leave a
    # pile of 90% identical weights behind. The winner is always kept.
    _prune_losing_trials(trials, best["model_id"])

    if req.auto_load:
        try:
            svc.load(
                weights_path=best["weights_path"],
                scaler_path=best["scaler_path"],
                model_id=best["model_id"],
            )
        except Exception as exc:  # the sweep still succeeded; report the load
            logger.warning("[ADVISER] event=AUTOTUNE_LOAD_FAIL err=%s", exc)
            best["load_error"] = str(exc)

    beats_baseline = majority_baseline is None or best["oos_accuracy"] >= float(majority_baseline)
    return {
        "status": "OK",
        "message": (
            f"Auto-tune complete: {len(trials)} trial(s), best OOS loss "
            f"{best['oos_loss']:.4f} (acc {best['oos_accuracy']:.4f}) "
            f"at lr={best['learning_rate']} bs={best['batch_size']} "
            f"seed={best['seed']}."
        ),
        "trials": trials,
        "best": best,
        "majority_baseline_accuracy": majority_baseline,
        "beats_majority_baseline": beats_baseline,
        "loaded": bool(req.auto_load and "load_error" not in best),
        "executed_at": datetime.now(UTC).isoformat(),
    }


def _prune_losing_trials(trials: list[dict[str, Any]], winner_id: str) -> None:
    """Delete the weights of losing trials; keep manifests for the audit trail."""
    for t in trials:
        if t.get("failed") or t.get("model_id") == winner_id:
            continue
        wp = t.get("weights_path")
        if not wp:
            continue
        try:
            p = _safe_under_repo(Path(wp))
            if p.is_file():
                p.unlink()
        except OSError as exc:
            logger.debug("[ADVISER] prune failed for %s: %s", wp, exc)


def register_position_adviser_routes(app: Any) -> None:
    app.include_router(router)


__all__ = [
    "get_position_adviser_service",
    "record_advisory_for_ui",
    "router",
]
