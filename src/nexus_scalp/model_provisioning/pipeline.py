"""PATH B — LOCAL TRAINING on the user's OWN broker export. Data never leaves.

Pipeline (every stage emits REAL progress events, none fabricated):

    import (CSV/Parquet/MT5 columns)   -> schema detection + normalize_bars_frame
    validate + diagnostics             -> rows in/out, duplicates, invalid, gaps,
                                          time range, selected slice counts
    select candle budget               -> explicit chronological TAIL (most
                                          recent N bars), never random rows
    train                              -> the CANONICAL purged walk-forward
                                          pipeline (three_model.train_variant,
                                          shared with official training) —
                                          time-based splits only, purge +
                                          embargo, leakage-safe by construction
    verify                             -> engine load gates on the candidate
    install (opt-in)                   -> serving slot ONLY if empty or
                                          starter-occupied; otherwise the
                                          bundle stays a local CANDIDATE and
                                          governed promotion remains the only
                                          path to serving (champion protection
                                          preserved — training completion is
                                          never promotion)

Privacy invariant (hard): this module performs ZERO network I/O. Import,
feature generation, training and verification are all local. No telemetry
payload carries market data.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.model_provisioning.pipeline")

ProgressCb = Callable[["ProgressEvent"], None]

#: Candle budget presets offered by the first-setup UI (bar counts).
CANDLE_PRESETS = (1_000, 10_000, 50_000, 100_000)


@dataclass(frozen=True)
class ProgressEvent:
    """One honest progress datum. `fraction`/metrics are None unless the
    stage can actually compute them — the UI must render what is present,
    never interpolate anything the engine did not measure."""

    stage: str  # import|validate|prepare|features|sequences|train|validate_model|verify|install
    status: str  # start|progress|done|failed|cancelled
    message: str = ""
    fraction: float | None = None  # 0..1 within the overall pipeline
    metrics: dict[str, Any] = field(default_factory=dict)
    ts: str = ""

    def __post_init__(self) -> None:
        if not self.ts:
            object.__setattr__(self, "ts", datetime.now(UTC).isoformat(timespec="seconds"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "status": self.status,
            "message": self.message,
            "fraction": self.fraction,
            "metrics": self.metrics,
            "ts": self.ts,
        }


@dataclass
class TrainingRequest:
    """What the user chose in first-setup PATH B."""

    source_file: Path
    candles: int | None = None  # None = all available
    folds: int = 6
    epochs: int = 4
    install: bool = True
    cancel_event: threading.Event | None = None
    #: Training backend CHOICE (user): "cpu" | "cuda"; None = auto
    #: (NEXUS_TRAINING_BACKEND env or NVIDIA-GPU detection). CPU-only torch
    #: installs must pass cpu explicitly — auto prefers cuda when a GPU is
    #: detected, per the OPTIONAL TRAINING SETUP contract.
    backend: str | None = None


@dataclass
class UserBarsImport:
    """Import + diagnostics result (all REAL counts)."""

    frame: Any  # polars.DataFrame (normalized, sorted, unique time)
    rows_total: int
    rows_valid: int
    dropped_duplicates: int
    dropped_invalid: int
    time_range: tuple[str, str]
    selected_rows: int
    missing_bar_gaps: int
    source: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "rows_total": self.rows_total,
            "rows_valid": self.rows_valid,
            "dropped_duplicates": self.dropped_duplicates,
            "dropped_invalid": self.dropped_invalid,
            "time_range": list(self.time_range),
            "selected_rows": self.selected_rows,
            "missing_bar_gaps": self.missing_bar_gaps,
        }


class TrainingCancelledError(RuntimeError):
    """User cancelled at a checked boundary (fold/epoch)."""


#: Back-compat alias (the pipeline re-exports the trainer's cancel class).
TrainingCancelled = TrainingCancelledError


def _emit(cb: ProgressCb | None, ev: ProgressEvent) -> None:
    logger.info("[LOCAL-TRAIN] event stage=%s status=%s %s", ev.stage, ev.status, ev.message)
    if cb is not None:
        with __import__("contextlib").suppress(Exception):  # UI must never break training
            cb(ev)


def import_user_bars(source_file: Path, candles: int | None = None) -> UserBarsImport:
    """Read CSV or Parquet (MT5-export friendly), normalize through the
    canonical runtime cleaner, report REAL diagnostics, and slice the most
    recent `candles` bars (chronological tail — time-series never take
    arbitrary rows)."""
    import polars as pl

    from nexus_scalp.model_generation.bars_normalize import normalize_bars_frame

    path = Path(source_file)
    if not path.exists():
        raise FileNotFoundError(f"input file not found: {path}")
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        raw = pl.read_parquet(path)
    elif suffix in (".csv", ".txt"):
        raw = pl.read_csv(path, infer_schema_length=10_000, ignore_errors=True)
    else:
        raise ValueError(
            f"unsupported format {suffix!r} — accepted: .csv, .parquet "
            "(MT5 exports normalize through the same runtime cleaner)"
        )
    rows_total = raw.height
    # Honest pre-clean diagnostics (real counts, computed from the RAW
    # frame — normalize_bars_frame reports only aggregate drops).
    time_col = next(
        (c for c in ("time_utc", "timestamp", "datetime", "date", "time") if c in raw.columns),
        None,
    )
    dup_count = 0
    invalid_count = 0
    try:
        if time_col is not None:
            tc = raw.get_column(time_col)
            dup_count = int(tc.len() - tc.n_unique())
        price_cols = [c for c in ("open", "high", "low", "close") if c in raw.columns]
        if price_cols:
            bad_mask = None
            for c in price_cols:
                col = raw.get_column(c).cast(float, strict=False)
                m = col.is_null() | (col <= 0)
                try:
                    m = m | ~col.is_finite()
                except AttributeError:  # older polars without is_finite
                    pass
                bad_mask = m if bad_mask is None else (bad_mask | m)
            invalid_count = int(bad_mask.sum()) if bad_mask is not None else 0
    except Exception:
        pass  # diagnostics are best-effort; normalize below remains the authority
    frame, _normalize_stats = normalize_bars_frame(raw)
    rows_valid = frame.height
    # Missing-bar gaps on the selected cadence (M1 assumption for scalp
    # training — honest count of >1-min jumps, not a fabricated resample).
    missing = 0
    try:
        import polars.selectors as cs  # noqa: F401  (keep import error surface explicit)

        diffs = frame.get_column("time").diff().drop_nulls()
        if diffs.len():
            missing = int((diffs > 60).sum())
    except Exception:
        missing = -1  # unknown beats invented

    if candles is not None:
        if candles <= 0:
            raise ValueError(f"candles must be > 0 or None (all), got {candles}")
        frame = frame.tail(int(candles))
    selected = frame.height
    if selected < 3_000:
        raise ValueError(
            f"only {selected} valid bars available — the canonical walk-forward needs "
            ">= 3,000 (smoke floor). Choose more candles or export a longer history."
        )
    t0 = str(frame.get_column("time").min())
    t1 = str(frame.get_column("time").max())
    return UserBarsImport(
        frame=frame,
        rows_total=rows_total,
        rows_valid=rows_valid,
        dropped_duplicates=dup_count,
        dropped_invalid=invalid_count,
        time_range=(t0, t1),
        selected_rows=selected,
        missing_bar_gaps=missing,
        source=str(path),
    )


def detect_ml_environment() -> dict[str, Any]:
    """Real environment probe for the first-setup UI (Python / torch / CUDA
    / GPU). Official download (PATH A) never calls this; missing torch is a
    FACT, not an error, and is reported as one.

    SECURITY: the returned dict is surfaced by a web API, so it carries a
    SAFE CATEGORY only (exception TYPE NAME) — never the raw exception
    message (paths, library internals, environment details stay server-side
    in the log line)."""
    import platform
    import sys

    env: dict[str, Any] = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "torch": None,
        "cuda": False,
        "gpu_name": None,
    }
    try:
        import torch

        env["torch"] = torch.__version__
        try:
            env["cuda"] = bool(torch.cuda.is_available())
            if env["cuda"]:
                env["gpu_name"] = torch.cuda.get_device_name(0)
        except Exception:
            env["cuda"] = False
    except Exception as exc:
        # Safe category for the client; full detail for the local log only.
        env["torch_error"] = type(exc).__name__
        logger.info("[LOCAL-TRAIN] event=TORCH_PROBE_FAILED category=%s", type(exc).__name__)
    return env


def train_local_model(
    request: TrainingRequest, progress: ProgressCb | None = None
) -> dict[str, Any]:
    """Run the full PATH B pipeline with honest staged progress.

    Returns {"outcome": "INSTALLED"|"CANDIDATE"|"VALIDATION_FAILED"|
    "CANCELLED", "candidate_dir", "diagnostics", "report", ...}. Never
    raises on user-cancel (returns a CANCELLED outcome instead); raises on
    genuinely unusable input (import/validation failures surface as
    VALIDATION_FAILED with the reason).
    """
    from nexus_scalp.model_provisioning import service as prov

    cancel = request.cancel_event or threading.Event()

    def _cancelled() -> bool:
        return cancel.is_set()

    result: dict[str, Any] = {"outcome": "FAILED", "request": {"source": str(request.source_file)}}

    # -- import + diagnostics ------------------------------------------------
    _emit(
        progress,
        ProgressEvent(
            stage="import", status="start", message="reading broker export", fraction=0.02
        ),
    )
    try:
        imp = import_user_bars(Path(request.source_file), candles=request.candles)
    except Exception as exc:
        _emit(progress, ProgressEvent(stage="import", status="failed", message=str(exc)[:300]))
        result.update(outcome="VALIDATION_FAILED", reason=f"import failed: {exc}")
        return result
    result["diagnostics"] = imp.as_dict()
    _emit(
        progress,
        ProgressEvent(
            stage="validate",
            status="done",
            message=(
                f"{imp.selected_rows}/{imp.rows_total} bars selected · "
                f"{imp.dropped_duplicates} dupes · {imp.dropped_invalid} invalid · "
                f"{imp.time_range[0]} -> {imp.time_range[1]}"
            ),
            fraction=0.08,
            metrics=imp.as_dict(),
        ),
    )
    if _cancelled():
        _emit(progress, ProgressEvent(stage="train", status="cancelled", fraction=0.08))
        result["outcome"] = "CANCELLED"
        return result

    # -- canonical training (shared pipeline; features + sequences + WF) ----
    # TRAINING ENV GATE (BUG-301): training may only start once the resolved
    # environment is READY for THIS process. The manager only DISCOVERS here
    # (never installs — provisioning is a separate, explicit user action via
    # TrainingEnvironmentManager.install / the CLI "install" verb).
    from nexus_scalp.model_provisioning.training_env import TrainingEnvironmentManager

    manager = TrainingEnvironmentManager()
    backend = (request.backend or manager.chosen_backend()).lower()
    env_report = manager.status(backend=backend)
    result["environment_report"] = env_report.as_dict()
    if not env_report.training_ready:
        missing = [
            f"{c.stage}: {c.code or 'FAILED'}" + (f" — {c.remedy}" if c.remedy else "")
            for c in env_report.failing()
        ]
        _emit(
            progress,
            ProgressEvent(
                stage="env",
                status="blocked",
                message="Training environment NOT ready — training has not started. "
                + "; ".join(missing)[:400],
                metrics={"checks": [c.as_dict() for c in env_report.checks]},
            ),
        )
        result.update(
            outcome="TRAINING_ENV_BLOCKED",
            reason="; ".join(missing)[:500] or "environment not ready",
            missing=[c.stage for c in env_report.failing()],
            backend=backend,
        )
        return result
    if not env_report.in_process_ready:
        # The READY environment is a DIFFERENT interpreter (managed venv):
        # starting training HERE would silently run on a different stack.
        _emit(
            progress,
            ProgressEvent(
                stage="env",
                status="blocked",
                message="Environment READY but outside this process — run the printed "
                "command with that interpreter (TRAINING_ENV_MISMATCH).",
            ),
        )
        result.update(
            outcome="TRAINING_ENV_MISMATCH",
            reason=env_report.training_command,
            backend=backend,
        )
        return result
    _emit(
        progress,
        ProgressEvent(
            stage="prepare",
            status="start",
            message=f"70D canonical feature pipeline on {imp.selected_rows} bars "
            f"(backend={backend})",
            fraction=0.10,
        ),
    )

    def _fold_progress(payload: dict[str, Any]) -> None:
        # Real per-epoch metrics forwarded from the canonical trainer
        # (fold/epoch/loss/val_loss/elapsed) — nothing synthesized.
        done_fold = int(payload.get("fold", 0))
        tot_fold = max(1, int(payload.get("folds", request.folds)))
        epoch = int(payload.get("epoch", 0))
        epochs = max(1, int(payload.get("epochs", request.epochs)))
        frac = 0.10 + 0.80 * ((done_fold - 1 + epoch / epochs) / tot_fold)
        _emit(
            progress,
            ProgressEvent(
                stage="train",
                status="progress",
                message=f"fold {done_fold}/{tot_fold} epoch {epoch}/{epochs}",
                fraction=min(0.90, frac),
                metrics=payload,
            ),
        )

    try:
        from nexus_scalp.model_generation.three_model import train_variant
        from nexus_scalp.training.walk_forward_trainer import TrainingCancelledError

        candidate_dir = prov.candidate_dir()
        report = train_variant(
            "70d_liquidity",
            imp.frame,
            num_folds=request.folds,
            epochs=request.epochs,
            smoke=False,
            output_dir=candidate_dir,
            progress_cb=_fold_progress,
            cancel_event=cancel,
        )
    except TrainingCancelledError:
        _emit(
            progress,
            ProgressEvent(stage="train", status="cancelled", message="cancelled at epoch boundary"),
        )
        result.update(outcome="CANCELLED", reason="operator cancel (no artifact published)")
        return result
    except Exception as exc:
        if _cancelled():
            _emit(progress, ProgressEvent(stage="train", status="cancelled"))
            result.update(outcome="CANCELLED")
            return result
        _emit(progress, ProgressEvent(stage="train", status="failed", message=str(exc)[:300]))
        result.update(outcome="VALIDATION_FAILED", reason=f"training failed: {exc}")
        return result
    result["report"] = {
        k: report.get(k)
        for k in (
            "variant",
            "schema_id",
            "dimension",
            "output_dir",
            "artifact",
            "walk_forward",
            "gate",
            "elapsed_sec",
        )
    }
    _emit(
        progress,
        ProgressEvent(
            stage="validate_model",
            status="done",
            message=f"walk-forward complete (gate={report.get('gate')})",
            fraction=0.90,
            metrics=dict(report.get("walk_forward") or {}),
        ),
    )

    # -- integrity verification of the candidate ----------------------------
    from nexus_scalp.release import bootstrap as rb

    trained_model = Path(str(report.get("artifact", {}).get("model", "")))
    verdict = rb.bundle_status(trained_model)
    result["candidate_model"] = str(trained_model)
    if verdict["state"] != rb.STATE_OK:
        _emit(
            progress,
            ProgressEvent(
                stage="verify",
                status="failed",
                message=f"candidate failed integrity gates: {verdict['state']} {verdict['detail']}",
                fraction=0.92,
            ),
        )
        result.update(
            outcome="VALIDATION_FAILED", reason=verdict["detail"], serve_state=verdict["state"]
        )
        return result
    _emit(
        progress,
        ProgressEvent(
            stage="verify",
            status="done",
            message="candidate passes all engine gates",
            fraction=0.95,
        ),
    )

    # -- install (governed) --------------------------------------------------
    if not request.install:
        result["outcome"] = "CANDIDATE"
        _emit(
            progress,
            ProgressEvent(
                stage="install", status="done", message="--no-install: candidate kept", fraction=1.0
            ),
        )
        return result
    install = prov.install_candidate(
        trained_model,
        origin=prov.ORIGIN_USER_TRAINED,
        note="nexus first-setup train (local; walk-forward trained)",
    )
    result.update(install)
    if install.get("installed"):
        result["outcome"] = "INSTALLED"
        _emit(
            progress,
            ProgressEvent(
                stage="install",
                status="done",
                message=f"installed to {install.get('serving_path')}",
                fraction=1.0,
            ),
        )
    else:
        result["outcome"] = "CANDIDATE"
        _emit(
            progress,
            ProgressEvent(
                stage="install",
                status="done",
                message=f"not installed ({install.get('install_skipped') or install.get('install_error')}) — governed promotion applies",
                fraction=1.0,
            ),
        )
    return result


__all__ = [
    "CANDLE_PRESETS",
    "ProgressCb",
    "ProgressEvent",
    "TrainingCancelled",
    "TrainingRequest",
    "UserBarsImport",
    "detect_ml_environment",
    "import_user_bars",
    "train_local_model",
]
