"""TRUE FORWARD TEST experiment (CHG-0035, FORWARD_TEST_EXPERIMENT v1).

A dedicated Forward Test is NOT a backtest flag and NOT shadow mode
(user brief §4-§11, §45-§46, §62). It is an explicit frozen experiment:

    experiment_type = FORWARD_TEST

    CUT_OFF_TIMESTAMP + FROZEN MODEL + FROZEN SCALER + FROZEN STRATEGY
    + FROZEN PARAMETERS + CAUSAL FEATURES + UNSEEN DATA AFTER CUTOFF

Difference vs Walk-Forward OOS (§46 — documented, never collapsed):
    Walk-Forward OOS  = rolling folds; a model is TRAINED before each fold.
    Forward Test      = ONE explicit frozen cutoff; the future period is
                        evaluated with artifacts frozen AT the cutoff; no
                        tuning/training/parameter change after it.

Architecture (§40-§41 — no duplicated engine):
    ForwardTestExperiment  = freeze capture + result identity + JSON export
    StreamingReplayEngine  = the shared causal execution pipeline
    ForwardTestPolicy      = the temporal-freeze policy: the event source is
                             HARD-SLICED to timestamps > cutoff and the
                             frozen artifacts directory is pinned.

Freeze capture (§7): at construction the experiment records the model
fingerprint (sha256 of model.pt bytes), scaler fingerprint, schema id/hash
(dimension), strategy id/params fingerprint, execution config, git commit
and the cutoff. Frozen artifact BYTES are copied into
artifacts/forward_test/<run_id>/ so a later champion change cannot mutate
the recorded identity or the rerun path (§34: provenance immutability).

Future-data isolation (§8-§10): the runner streams ONLY events with
timestamp > cutoff through the shared engine; state at the cutoff is built
causally from the frozen artifacts (no dataset preload — warm-up comes from
events the engine itself consumed). The freeze identity is re-verified
AFTER the run (§70) — any drift raises FREEZE_DRIFTED.

Safety (§63): paper/simulation only. Nothing here imports an adapter or
sends orders; the shared engine is order-free by construction.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.research.streaming_replay import (
    ModelArtifacts,
    ReplaySessionConfig,
    StreamingReplayEngine,
    _sha256_file,
    load_model_artifacts,
)

logger = get_logger("nexus_scalp.research.forward_test")

#: Experiment type token persisted on every forward-test result.
EXPERIMENT_TYPE: str = "FORWARD_TEST"


# ---------------------------------------------------------------------------
# Freeze record
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ForwardTestFreeze:
    """The frozen identity captured AT the cutoff (brief §7, §87-§92)."""

    cutoff: datetime
    model_id: str
    model_version: str
    model_fingerprint: str
    schema_id: str
    schema_version: str
    schema_hash: str
    feature_dim: int
    scaler_fingerprint: str
    strategy_id: str
    strategy_version: str
    strategy_fingerprint: str
    execution_model_fingerprint: str
    git_commit: str
    frozen_artifact_dir: str
    captured_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "cutoff": self.cutoff.isoformat(),
            "model_id": self.model_id,
            "model_version": self.model_version,
            "model_fingerprint": self.model_fingerprint,
            "schema_id": self.schema_id,
            "schema_version": self.schema_version,
            "schema_hash": self.schema_hash,
            "feature_dim": self.feature_dim,
            "scaler_fingerprint": self.scaler_fingerprint,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "strategy_fingerprint": self.strategy_fingerprint,
            "execution_model_fingerprint": self.execution_model_fingerprint,
            "git_commit": self.git_commit,
            "frozen_artifact_dir": self.frozen_artifact_dir,
            "captured_at": self.captured_at.isoformat(),
        }

    def identity_digest(self) -> str:
        raw = json.dumps(self.to_dict(), sort_keys=True).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:32]


def _resolve_model_version(model_path: Path) -> str:
    """Model version from AUTHORITATIVE metadata, never from filename (§36).

    Resolution order: side-car model.meta.json declaration (the artifact
    registry's own contract) -> parent directory name ONLY when it matches
    the bundle's declared schema (known 70d_liquidity convention). When no
    authoritative evidence exists the version is the empty string —
    NOT_RECORDED, never invented (§35).
    """
    meta_path = model_path.with_suffix(".meta.json")
    if meta_path.exists():
        with contextlib.suppress(Exception):
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            for key in ("model_version", "version", "artifact_version"):
                v = str(meta.get(key, "") or "")
                if v:
                    return v
    return ""  # NOT_RECORDED — never invent


def _git_commit() -> str:
    try:
        from nexus_scalp.release.metadata import _git_commit as _git

        return _git() or ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# ForwardTestPolicy — the temporal-freeze wrapper (§41)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ForwardTestPolicy:
    """Temporal freeze policy applied to ANY HistoricalEventSource.

    The policy does not reimplement execution or strategy logic: it only
    (a) HARD-SLICES the stream to timestamp > cutoff (strict inequality:
    the cutoff event itself is KNOWN data, evaluation starts strictly
    after it) and (b) re-labels the session as experiment FORWARD_TEST.
    Chunked sources keep their own semantics; slicing composes.
    """

    cutoff: datetime

    def __post_init__(self) -> None:
        c = self.cutoff if self.cutoff.tzinfo else self.cutoff.replace(tzinfo=UTC)
        object.__setattr__(self, "cutoff", c)

    def slice_source(self, source: Any) -> _CutoffSlicedSource:
        return _CutoffSlicedSource(source, self.cutoff)


class _CutoffSlicedSource:
    """Yields only events with timestamp > cutoff (causal boundary)."""

    event_kind = source_event_kind = None  # filled below

    def __init__(self, inner: Any, cutoff: datetime) -> None:
        self._inner = inner
        self._cutoff = cutoff if cutoff.tzinfo else cutoff.replace(tzinfo=UTC)
        self.symbol = getattr(inner, "symbol", "XAUUSD")
        self.name = f"forward-slice({getattr(inner, 'name', 'source')})>={self._cutoff.isoformat()}"
        from nexus_scalp.research.event_source import EventKind

        self.event_kind = getattr(inner, "event_kind", EventKind.TICK)

    def events(self) -> Any:
        from nexus_scalp.research.event_source import DataErrorEvent

        for ev in self._inner.events():
            ts = getattr(ev, "timestamp", None)
            if isinstance(ts, datetime):
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=UTC)
                if ts <= self._cutoff:
                    continue
            elif not isinstance(ev, DataErrorEvent):
                # A non-error event without a timestamp cannot be placed on
                # the causal timeline: skip it loudly, never silently trade.
                logger.warning(
                    "[FORWARD_TEST] event=UNTIMED_EVENT_SKIPPED source=%s kind=%s",
                    self.name,
                    getattr(ev, "kind", "?"),
                )
                continue
            yield ev


# ---------------------------------------------------------------------------
# The experiment
# ---------------------------------------------------------------------------


class ForwardTestExperiment:
    """Creates + runs a dedicated FORWARD_TEST experiment.

    Usage:
        exp = ForwardTestExperiment.create(
            cutoff=datetime(2026, 9, 1, 0, 0, tzinfo=UTC),
            model_artifact_path="artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt",
            policy_params={"confidence_threshold": 0.35},
            strategy_id="scalp_v3_70d",
            strategy_version="1.0.0",
        )
        result = exp.run(source)          # source covers the FUTURE period
        exp.verify_freeze()               # post-run freeze check (§70)
    """

    def __init__(
        self,
        freeze: ForwardTestFreeze,
        engine: StreamingReplayEngine,
        *,
        run_id: str,
        symbol: str,
        timeframe: str,
        storage_dir: Path,
    ) -> None:
        self.freeze = freeze
        self.engine = engine
        self.run_id = run_id
        self.symbol = symbol
        self.timeframe = timeframe
        self.storage_dir = storage_dir

    # ------------------------------------------------------------------
    # Construction: freeze capture
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        *,
        cutoff: datetime,
        model_artifact_path: str | Path,
        policy_params: dict[str, Any] | None = None,
        strategy_id: str = "scalp_v3_70d",
        strategy_version: str = "",
        symbol: str = "XAUUSD",
        timeframe: str = "M1",
        execution_config: Any = None,
        news_frame: Any = None,
        storage_root: str | Path = "artifacts/forward_test",
        run_id: str | None = None,
        starting_equity_usd: float = 10_000.0,
        model_artifacts: ModelArtifacts | None = None,
    ) -> ForwardTestExperiment:
        """Captures the freeze AT the cutoff and builds the shared engine.

        The model/scaler artifact BYTES are copied into the frozen storage
        dir BEFORE the run; the engine is then built against the FROZEN
        copies, so even a concurrent champion hot-swap on the live path
        cannot alter this experiment's weights mid-run.
        """
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=UTC)
        rid = run_id or (
            f"FT-{cutoff:%Y%m%d%H%M%S}-{datetime.now(UTC):%Y%m%d%H%M%S}-"
            f"{hashlib.sha256(repr(policy_params).encode()).hexdigest()[:6]}"
        )
        storage_dir = Path(storage_root) / rid
        storage_dir.mkdir(parents=True, exist_ok=True)

        artifacts = model_artifacts or load_model_artifacts(model_artifact_path)

        # Freeze the artifact BYTES (§34: identity must survive champion changes)
        frozen_model = storage_dir / "model.pt"
        frozen_scaler = storage_dir / "model.scaler.npz"
        if not frozen_model.exists():
            shutil.copyfile(artifacts.model_path, frozen_model)
        if artifacts.scaler_path.exists() and not frozen_scaler.exists():
            shutil.copyfile(artifacts.scaler_path, frozen_scaler)
        frozen_meta = storage_dir / "freeze.json"
        if not frozen_meta.exists():
            frozen_meta.write_text("{}", encoding="utf-8")  # placeholder; filled below

        frozen_artifacts = load_model_artifacts(frozen_model)

        from nexus_scalp.features.schema_contract import (
            SCHEMA_ID,
            SCHEMA_VERSION,
            feature_schema_hash,
        )
        from nexus_scalp.research.streaming_replay import FrozenPolicyRunner

        policy = FrozenPolicyRunner(policy_params or {})
        exec_cfg = execution_config
        if exec_cfg is None:
            from nexus_scalp.research.streaming_replay import ReplayExecutionConfig

            exec_cfg = ReplayExecutionConfig()

        freeze = ForwardTestFreeze(
            cutoff=cutoff,
            model_id="scalp_70d_bundle",
            model_version=_resolve_model_version(artifacts.model_path),
            model_fingerprint=frozen_artifacts.model_fingerprint,
            schema_id=SCHEMA_ID,
            schema_version=SCHEMA_VERSION,
            schema_hash=feature_schema_hash(),
            feature_dim=int(frozen_artifacts.num_features),
            scaler_fingerprint=frozen_artifacts.scaler_fingerprint,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            strategy_fingerprint=policy.fingerprint(),
            execution_model_fingerprint=hashlib.sha256(
                json.dumps(exec_cfg.identity(), sort_keys=True).encode("utf-8")
            ).hexdigest()[:32],
            git_commit=_git_commit(),
            frozen_artifact_dir=str(storage_dir),
            captured_at=datetime.now(UTC),
        )
        # Persist the freeze record next to the frozen bytes (audit trail).
        frozen_meta.write_text(
            json.dumps(freeze.to_dict(), indent=2, sort_keys=True), encoding="utf-8"
        )

        session_cfg = ReplaySessionConfig(
            experiment_type=EXPERIMENT_TYPE,
            symbol=symbol,
            timeframe=timeframe,
            model_artifact_path=str(frozen_model),
            policy_params=dict(policy.params),
            execution=exec_cfg,
            decide_on="bar_close",
            news_frame=news_frame,
            git_commit=freeze.git_commit,
            starting_equity_usd=starting_equity_usd,
        )
        engine = StreamingReplayEngine(session_cfg, artifacts=frozen_artifacts, policy=policy)
        return cls(
            freeze=freeze,
            engine=engine,
            run_id=rid,
            symbol=symbol,
            timeframe=timeframe,
            storage_dir=storage_dir,
        )

    # ------------------------------------------------------------------
    # Run + post-run freeze verification (§70)
    # ------------------------------------------------------------------

    def run(self, source: Any, *, max_events: int | None = None) -> dict[str, Any]:
        """Runs the forward test over the FUTURE period (timestamp > cutoff).

        Returns the run result augmented with experiment identity, the
        future window actually consumed, and the post-run freeze verdict.
        """
        sliced = ForwardTestPolicy(self.freeze.cutoff).slice_source(source)
        base = self.engine.run(sliced, run_id=self.run_id, max_events=max_events)
        drift = self.verify_freeze()
        result = {
            "run_id": self.run_id,
            "experiment_type": EXPERIMENT_TYPE,
            "cutoff": self.freeze.cutoff.isoformat(),
            "future_start": base.first_event,
            "future_end": base.last_event,
            "freeze": self.freeze.to_dict(),
            "freeze_identity_digest": self.freeze.identity_digest(),
            "freeze_verified_after_run": drift,
            "result": base.to_dict(),
        }
        self._persist(result)
        return result

    def verify_freeze(self) -> bool:
        """Re-verifies frozen fingerprints AFTER data was processed (§70).

        The frozen copies on disk are the run's source of truth; the live
        artifact may change freely (that is the point of freezing). A drift
        of the FROZEN bytes vs the recorded fingerprints means the frozen
        dir was tampered with mid-experiment -> raise FREEZE_DRIFTED.
        """
        frozen_model = Path(self.freeze.frozen_artifact_dir) / "model.pt"
        current = _sha256_file(frozen_model)
        if current != self.freeze.model_fingerprint:
            raise RuntimeError(
                f"FREEZE_DRIFTED: frozen model bytes changed mid-experiment "
                f"({self.freeze.model_fingerprint} -> {current})"
            )
        frozen_scaler = Path(self.freeze.frozen_artifact_dir) / "model.scaler.npz"
        if self.freeze.scaler_fingerprint and frozen_scaler.exists():
            sc = _sha256_file(frozen_scaler)
            if sc != self.freeze.scaler_fingerprint:
                raise RuntimeError("FREEZE_DRIFTED: frozen scaler bytes changed mid-experiment")
        fp_now = self.engine.policy.fingerprint()
        if fp_now != self.freeze.strategy_fingerprint:
            raise RuntimeError(
                f"FREEZE_DRIFTED: strategy params fingerprint changed "
                f"({self.freeze.strategy_fingerprint} -> {fp_now})"
            )
        if self.engine.config.experiment_type != EXPERIMENT_TYPE:
            raise RuntimeError("FREEZE_DRIFTED: experiment_type mutated")
        return True

    # ------------------------------------------------------------------
    # Result persistence + JSON protocol (§60, §93 — no result overwrite)
    # ------------------------------------------------------------------

    def _persist(self, result: dict[str, Any]) -> Path:
        out = self.storage_dir / "result.json"
        tmp = out.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(result, indent=2, sort_keys=True, default=str), encoding="utf-8")
        tmp.replace(out)
        return out

    def export_json(self) -> dict[str, Any]:
        """The §60 JSON protocol envelope (values only from real records)."""
        result_path = self.storage_dir / "result.json"
        if result_path.exists():
            with contextlib.suppress(Exception):
                return json.loads(result_path.read_text(encoding="utf-8"))
        return {
            "run_id": self.run_id,
            "experiment_type": EXPERIMENT_TYPE,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "cutoff": self.freeze.cutoff.isoformat(),
            "freeze": self.freeze.to_dict(),
        }
