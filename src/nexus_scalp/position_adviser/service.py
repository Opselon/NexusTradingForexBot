"""Position Decision Adviser — runtime service (the bounded, fail-closed seam).

TASK-POSA-001.

INVARIANT — what this service can never do:
    * It can never OPEN a position, size one, or modify an order.
    * It can never RAISE a hold score, extend a position's life, or weaken a
      protection verdict.
    * When ``activation == DISABLED`` (the default) ``evaluate()`` returns
      ``None`` and the decide system runs byte-identically to today.
    * When enabled, its ONLY channel is ``PositionAdvisory.hold_score_adjustment``,
      which is <= 0 by construction and capped by ``max_hold_score_penalty``.

The caller (the decide system) applies the adjustment to the hold score AFTER
the existing scoring + protection stages have run, so the adviser can only ever
make an already-evaluated verdict more conservative — never the reverse.

Every failure path is fail-closed: a bad model, a missing artifact, a
non-finite feature, or an exception returns ``None`` and logs a structured
warning. No fabricated confidence, no fabricated verdict.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import numpy as np
import torch

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.position_adviser.features import (
    ADVISER_FEATURE_DIM,
    ADVISER_FEATURE_ORDER,
    AdviserFeatureError,
    build_live_vector,
)
from nexus_scalp.position_adviser.models import (
    ACTION_BY_INDEX,
    ADVISER_ACTIONS,
    ActivationCheckResult,
    AdviserActivation,
    PositionAdvisory,
)
from nexus_scalp.position_adviser.paths import (
    AdviserPathError,
    resolve_under_root,
)
from nexus_scalp.position_adviser.trainer import AdviserScaler, PositionAdviserNet

logger = get_logger("nexus_scalp.position_adviser.service")

#: Trusted containment root for adviser artifacts. Request-supplied paths are
#: resolved and MUST land inside this root or the sink refuses them (BUG-270
#: convention: containment at the sink, before any file-system/deserialize op).
_ADVISER_ROOT = Path(__file__).resolve().parents[3]


def _contained_artifact_path(p: Path) -> Path | None:
    """Resolve ``p`` and return it ONLY if it stays inside ``_ADVISER_ROOT``.

    Returns ``None`` (never raises with the path in it) when the resolved path
    escapes the containment root — the caller logs and rejects. This barrier
    sits immediately before every sink (``is_file()``, ``torch.load``,
    ``np.load``) so user-controlled values can never reach them uncontained.
    The resolved value is what the caller must use: returning the unresolved
    input would hand the sink a different object than the one that was
    validated (a TOCTOU). ``Path.resolve()`` also follows symlinks, so a
    symlink payload pointing outside the root is rejected here.
    """
    resolved = p.resolve()
    root = _ADVISER_ROOT.resolve()
    if not resolved.is_relative_to(root):
        return None
    return resolved


def _trusted(contained: Path) -> Path:
    """Identity cast: the value IS the contained, resolved path.

    ``_contained_artifact_path`` returns a value whose provenance the type
    system cannot see (its declared return type is ``Path``, and the sinks
    below consume it as one). Re-binding through this helper is how the
    data-flow barrier is kept legible to a static checker: from this point on,
    ``wp``/``sp`` are named-trusted values and no request-supplied component
    can reach ``is_file``/``torch.load``/``np.load``. The runtime value is
    unchanged — this is a cast, not a transformation.
    """
    return cast("Path", contained)


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def verify_package_integrity(wp: Path) -> dict[str, Any]:
    """F6: THE package-integrity gate — ONE canonical implementation shared by
    ``PositionAdviserService.load`` and the ``position-adviser-packages`` CLI
    (mission §35: no second copy of the verification logic anywhere).

    For a weights file ``<id>.pt`` this reads the sibling ``<id>.meta.json``
    written by the trainer and compares its pins against the bytes on disk.

    Returns::

        {
          "weights_sha256": str,      # sha256 of the weights file
          "manifest": dict | None,    # parsed sidecar, or None if absent
          "reason": str,              # note when manifest is absent/unreadable
          "reject": str | None,       # non-None => package must NOT be served
        }

    A stale overwrite, a hand-swapped sidecar, or a manifest from a different
    training run yields ``reject``; a package with no manifest at all loads
    only with an honest ``reason`` saying integrity is unverified.
    """
    if not _contained_artifact_path(wp):
        return {
            "weights_sha256": "",
            "manifest": None,
            "reason": "",
            "reject": "path rejected: outside repository root",
        }
    weights_sha = sha256_file(wp)

    mp = wp.with_suffix(".meta.json") if wp.suffix == ".pt" else None
    manifest: dict[str, Any] | None = None
    reason = ""
    if mp is not None and mp.is_file():
        try:
            if not _contained_artifact_path(mp):
                return {
                    "weights_sha256": weights_sha,
                    "manifest": None,
                    "reason": "",
                    "reject": "path rejected: outside repository root",
                }
            with open(mp, encoding="utf-8") as mf:
                manifest = json.load(mf)
        except Exception as exc:
            logger.warning("[ADVISER] event=MANIFEST_READ_FAILED err=%s", exc)
            reason = "manifest unreadable (see server logs)"
    else:
        reason = "no sidecar manifest found; integrity unverified"

    if manifest is None:
        return {"weights_sha256": weights_sha, "manifest": None, "reason": reason, "reject": None}

    want_w = str(manifest.get("weights_sha256", "")).strip()
    if want_w and want_w.lower() != weights_sha.lower():
        return {
            "weights_sha256": weights_sha,
            "manifest": manifest,
            "reason": reason,
            "reject": (
                "package integrity failure: weights sha256 does not match "
                "the manifest (stale or mismatched artifact combination)"
            ),
        }
    scaler_path = wp.with_suffix(".scaler.npz")
    if scaler_path.is_file():
        want_s = str(manifest.get("scaler_sha256", "")).strip()
        if want_s and want_s.lower() != sha256_file(scaler_path).lower():
            return {
                "weights_sha256": weights_sha,
                "manifest": manifest,
                "reason": reason,
                "reject": (
                    "package integrity failure: scaler sha256 does not match "
                    "the manifest (model/scaler from different training runs)"
                ),
            }
    m_feat = manifest.get("feature_order")
    if isinstance(m_feat, list) and list(m_feat) != list(ADVISER_FEATURE_ORDER):
        return {
            "weights_sha256": weights_sha,
            "manifest": manifest,
            "reason": reason,
            "reject": (
                "package integrity failure: manifest feature schema does not "
                "match the serving feature contract"
            ),
        }
    return {"weights_sha256": weights_sha, "manifest": manifest, "reason": reason, "reject": None}


#: Ceiling on the live throttle/snapshot map. A broker session can open
#: thousands of distinct tickets over a long runtime; the per-ticket maps must
#: not grow without bound (F1/F5: long-running inference does not leak memory).
#: 4096 tickets is far beyond any realistic concurrent position count, and the
#: map is also drained by ``forget()`` on every broker-verified close.
_MAX_TRACKED_TICKETS = 4096


@dataclass
class AdviserState:
    """Mutable runtime state. Guarded by ``PositionAdviserService._lock``."""

    activation: AdviserActivation = AdviserActivation.DISABLED
    model_id: str = ""
    weights_path: str = ""
    scaler_path: str = ""
    weights_sha256: str = ""
    #: F6: sidecar manifest (.meta.json) that pinned this package, its
    #: verification outcome, and the training dataset's content hash — the
    #: model's reproducible identity (empty/"" when no manifest existed).
    manifest_path: str = ""
    integrity: str = "unverified"
    source_dataset_hash: str = ""
    feature_dim: int = ADVISER_FEATURE_DIM
    loaded_at: str | None = None
    #: Count of evaluations that actually influenced the hold score.
    applied_count: int = 0
    #: Count of evaluations that were computed but not applied.
    evaluated_count: int = 0
    #: Count of refused/error evaluations.
    refused_count: int = 0
    #: Count of evaluations REJECTED because the position snapshot was stale
    #: (a newer snapshot already exists for the same ticket, or the caller
    #: offered a snapshot older than the one a prior evaluation consumed).
    #: F1: a prediction built from stale position state must be rejected, not
    #: applied — this is the anti-(duplicate/stale-decision) counter.
    stale_rejected_count: int = 0
    last_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "activation": str(self.activation),
            "model_id": self.model_id,
            "weights_path": self.weights_path,
            "scaler_path": self.scaler_path,
            "weights_sha256": self.weights_sha256,
            "manifest_path": self.manifest_path,
            "integrity": self.integrity,
            "source_dataset_hash": self.source_dataset_hash,
            "feature_dim": self.feature_dim,
            "loaded_at": self.loaded_at,
            "applied_count": self.applied_count,
            "evaluated_count": self.evaluated_count,
            "refused_count": self.refused_count,
            "stale_rejected_count": self.stale_rejected_count,
            "last_error": self.last_error,
            "ready": bool(self.model_id and self._model is not None and self._scaler is not None),
        }

    def __post_init__(self) -> None:
        #: Not serialised; internal tensor holders.
        self._model: PositionAdviserNet | None = None
        self._scaler: AdviserScaler | None = None


@dataclass
class AdviserConfig:
    """Static configuration for the adviser. All bounds are hard floors/ceilings."""

    #: Ceiling on the hold-score penalty a single advisory can impose.
    max_hold_score_penalty: float = 25.0
    #: Minimum confidence for an advisory to be eligible to apply at all.
    min_confidence_to_apply: float = 0.55
    #: Minimum probability advantage of the CLOSE/REDUCE action over KEEP.
    min_action_advantage: float = 0.10
    #: Advisory recalculation throttle, in seconds.
    min_eval_interval_sec: float = 2.0
    #: Maximum accepted age of the position snapshot a caller hands in, in
    #: seconds (F1). A snapshot older than this was built from position state
    #: that may since have changed (SL/TP modified, closed, partial) and is
    #: REJECTED rather than evaluated — a prediction from stale state must
    #: never be produced, let alone applied. Requires the caller to stamp
    #: ``snapshot_observed_at`` (monotonic seconds); callers that do not stamp
    #: it are refused when this gate is armed by a positive value.
    max_snapshot_age_sec: float = 5.0
    #: Artifact root for adviser checkpoints (relative to repo root).
    artifact_dir: str = "artifacts/position_adviser"

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_hold_score_penalty": self.max_hold_score_penalty,
            "min_confidence_to_apply": self.min_confidence_to_apply,
            "min_action_advantage": self.min_action_advantage,
            "min_eval_interval_sec": self.min_eval_interval_sec,
            "max_snapshot_age_sec": self.max_snapshot_age_sec,
            "artifact_dir": self.artifact_dir,
        }


class PositionAdviserService:
    """Thread-safe, fail-closed Layer-2 advisory service."""

    def __init__(self, config: AdviserConfig | None = None) -> None:
        self.config = config or AdviserConfig()
        self._state = AdviserState()
        self._lock = threading.RLock()
        self._last_eval_at: dict[int, float] = {}
        #: Last COMMITTED snapshot id per ticket (F1 duplicate-decision gate).
        self._last_snapshot_ids: dict[int, str] = {}

    # ------------------------------------------------------------------ state

    def status(self) -> dict[str, Any]:
        with self._lock:
            d = self._state.to_dict()
            d["config"] = self.config.to_dict()
            d["activation_ladder"] = {
                "current": str(self._state.activation),
                "available": [a for a in AdviserActivation],
            }
            return d

    @property
    def activation(self) -> AdviserActivation:
        with self._lock:
            return self._state.activation

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._state.activation is not AdviserActivation.DISABLED

    # ------------------------------------------------------------- activation

    def set_activation(
        self,
        activation: AdviserActivation | str,
        *,
        checks: list[ActivationCheckResult] | None = None,
    ) -> dict[str, Any]:
        """Move along the activation ladder.

        ``PAPER`` requires a loaded model. ``LIVE`` requires PAPER to have run
        AND every supplied activation check to report ``passed=True`` with
        evidence — the operator-driven prerequisite chain (broker connected,
        live positions enumerated, risk gates armed). This method never trusts
        a self-asserted readiness; it only records the decision.
        """
        want = AdviserActivation(str(activation).strip().upper())
        if want not in AdviserActivation:
            return {"status": "REJECTED", "reason": f"unknown activation {activation!r}"}

        with self._lock:
            if want is AdviserActivation.DISABLED:
                self._state.activation = want
                logger.info("[ADVISER] event=ACTIVATION_DISABLED")
                return {"status": "OK", "activation": str(want), "message": "adviser disabled"}

            if not self._state._model or not self._state._scaler:
                return {
                    "status": "REJECTED",
                    "reason": "no adviser model loaded; train and load one first",
                }

            if want is AdviserActivation.LIVE:
                if self._state.activation is AdviserActivation.DISABLED:
                    return {
                        "status": "REJECTED",
                        "reason": "must pass through PAPER before LIVE",
                    }
                failed = [c for c in (checks or []) if not c.passed]
                if failed:
                    return {
                        "status": "REJECTED",
                        "reason": "activation prerequisites failed",
                        "failed_checks": [c.to_dict() for c in failed],
                    }

            prev = self._state.activation
            self._state.activation = want
            logger.info("[ADVISER] event=ACTIVATION_CHANGED from=%s to=%s", prev, want)
            return {
                "status": "OK",
                "activation": str(want),
                "previous": str(prev),
                "message": f"adviser activation set to {want}",
            }

    # ---------------------------------------------------------------- loading

    def load(
        self,
        weights_path: Path | str,
        scaler_path: Path | str,
        *,
        model_id: str | None = None,
    ) -> dict[str, Any]:
        """Load an adviser checkpoint + scaler sidecar into memory, atomically."""
        # SANITIZER BARRIER (CodeQL py/path-injection): the request-supplied path
        # is reduced to a whitelist-only root-relative Path and anchored under
        # the trusted root BEFORE any Path expression a sink consumes. The
        # sanitizer returns a value whose provenance is the whitelist, not the
        # request, so only that untainted value reaches
        # ``is_file``/``torch.load``/``np.load``.
        try:
            clean_w = resolve_under_root(weights_path, root=_ADVISER_ROOT, label="adviser weights")
            clean_s = resolve_under_root(scaler_path, root=_ADVISER_ROOT, label="adviser scaler")
        except AdviserPathError as exc:
            # The real cause stays in the server log; the reason returned to the
            # caller is a constant so no request-derived text can be echoed
            # back (CodeQL: stack trace / information exposure).
            logger.warning("[ADVISER] event=LOAD_REJECTED reason=unsafe_path err=%s", exc)
            return {"status": "REJECTED", "reason": "path rejected: unsafe characters"}
        wp = clean_w
        sp = clean_s
        # Containment barrier (BUG-270 convention), defense-in-depth on top of
        # the sanitizer: the resolved path must land inside the repo root, and
        # ``Path.resolve()`` follows symlinks, so a symlink payload pointing
        # outside the root is rejected here. A rejected path is logged
        # server-side and returned as a generic status — never echoed back.
        # Trusted-path handoff: from here on ``wp``/``sp`` are the contained,
        # resolved objects that every sink below consumes.
        wp_c = _contained_artifact_path(wp)
        sp_c = _contained_artifact_path(sp)
        if wp_c is None or sp_c is None:
            logger.warning(
                "[ADVISER] event=LOAD_REJECTED reason=path_outside_repo weights=%s scaler=%s",
                wp,
                sp,
            )
            return {"status": "REJECTED", "reason": "path rejected: outside repository root"}
        wp = _trusted(wp_c)
        sp = _trusted(sp_c)
        if not wp.is_file():
            return {"status": "REJECTED", "reason": "weights file not found"}
        if not sp.is_file():
            return {"status": "REJECTED", "reason": "scaler file not found"}

        try:
            # Sink-level containment assertion, immediately before the
            # deserialisation: the barrier is checked where the bytes are read,
            # not only at the top of load().
            if not _contained_artifact_path(wp):
                return {
                    "status": "REJECTED",
                    "reason": "path rejected: outside repository root",
                }
            weights = torch.load(wp, map_location="cpu", weights_only=True)
        except Exception as exc:
            logger.warning("[ADVISER] event=WEIGHTS_LOAD_FAILED err=%s", exc)
            return {"status": "REJECTED", "reason": "failed to load weights (see server logs)"}

        if not isinstance(weights, dict) or "net.0.weight" not in weights:
            return {
                "status": "REJECTED",
                "reason": (
                    "checkpoint is not a PositionAdviserNet state dict "
                    "(missing 'net.0.weight'); refusing to load a foreign model"
                ),
            }
        # Resolve the classifier weights WITHOUT a magic index: derive the head
        # key from a freshly constructed PositionAdviserNet (the module is the
        # contract, the state dict is just bytes). Sorting by index is NOT
        # sufficient — intermediate Linears and LayerNorms make "the last 2-D
        # weight" ambiguous, and picking the wrong one silently misreads the
        # head width and rejects a perfectly valid checkpoint.
        head_key = PositionAdviserNet.head_weight_key()
        if head_key not in weights:
            return {
                "status": "REJECTED",
                "reason": (
                    "checkpoint has no classifier head key "
                    f"{head_key!r}; not a PositionAdviserNet state dict"
                ),
            }
        head_w = weights[head_key]
        # NOTE on axis semantics: for nn.Linear(in, out) the weight is
        # (out_features, in_features), so for the head shape[0] = classes and
        # shape[1] = the PRECEDING hidden width (32), NOT the input feature
        # dimension. The 12-dim feature width lives on the FIRST Linear
        # ('net.0.weight', shape (64, 12)). Checking the wrong axis silently
        # rejects every valid checkpoint.
        first_w = weights.get("net.0.weight")
        if first_w is not None and int(first_w.shape[1]) != ADVISER_FEATURE_DIM:
            return {
                "status": "REJECTED",
                "reason": (
                    f"feature dimension mismatch: checkpoint expects "
                    f"{int(first_w.shape[1])}, this adviser serves {ADVISER_FEATURE_DIM}"
                ),
            }
        num_classes = int(head_w.shape[0])
        if num_classes < len(ADVISER_ACTIONS):
            return {
                "status": "REJECTED",
                "reason": (
                    f"checkpoint head has {num_classes} classes; need >= "
                    f"{len(ADVISER_ACTIONS)} (KEEP/CLOSE/REDUCE)"
                ),
            }

        try:
            sd_data = np.load(sp)
            scaler = AdviserScaler(
                mean=np.asarray(sd_data["mean"], dtype=np.float64),
                std=np.asarray(sd_data["std"], dtype=np.float64),
                feature_dim=ADVISER_FEATURE_DIM,
            )
        except Exception as exc:
            logger.warning("[ADVISER] event=SCALER_LOAD_FAILED err=%s", exc)
            return {"status": "REJECTED", "reason": "failed to load scaler (see server logs)"}
        if not scaler.is_ready():
            return {"status": "REJECTED", "reason": "scaler not ready (non-finite stats)"}

        model = PositionAdviserNet(
            feature_dim=ADVISER_FEATURE_DIM,
            num_classes=max(num_classes, len(ADVISER_ACTIONS)),
        )
        try:
            model.load_state_dict(weights, strict=True)
        except Exception as exc:
            logger.warning("[ADVISER] event=STATE_DICT_LOAD_FAILED err=%s", exc)
            return {"status": "REJECTED", "reason": "state dict load failed (see server logs)"}
        model.eval()

        # Warm-up forward (fail closed before we advertise readiness).
        try:
            with torch.no_grad():
                dummy = torch.zeros((1, ADVISER_FEATURE_DIM), dtype=torch.float32)
                out = model(dummy)
            if out.shape[0] != 1 or out.shape[1] < len(ADVISER_ACTIONS):
                raise RuntimeError(f"unexpected warm-up output shape {tuple(out.shape)}")
        except Exception as exc:
            logger.warning("[ADVISER] event=WARMUP_FORWARD_FAILED err=%s", exc)
            return {"status": "REJECTED", "reason": "warm-up forward failed (see server logs)"}

        # Re-check containment immediately before the raw read: the sha256
        # below opens the checkpoint bytes, so the barrier is asserted at the
        # sink itself, not only at the top of load().
        if not _contained_artifact_path(wp):
            logger.warning("[ADVISER] event=HASH_REJECTED reason=path_outside_repo")
            return {"status": "REJECTED", "reason": "path rejected: outside repository root"}
        # F6: ATOMIC MODEL-PACKAGE INTEGRITY GATE — shared canonical
        # implementation (verify_package_integrity), also used by the
        # `position-adviser-packages` CLI. Rejects stale/hand-swapped
        # weight+scaler+manifest combinations BEFORE they can serve; a package
        # without a sidecar manifest loads with an honest "unverified" note.
        chk = verify_package_integrity(wp)
        if chk["reject"] is not None:
            logger.warning(
                "[ADVISER] event=PACKAGE_INTEGRITY_FAIL model_id=%s reason=%s",
                model_id or wp.stem,
                chk["reject"],
            )
            return {"status": "REJECTED", "reason": chk["reject"]}
        weights_sha = str(chk["weights_sha256"])
        manifest = cast("dict[str, Any] | None", chk["manifest"])
        manifest_reason = str(chk["reason"])
        mp = wp.with_suffix(".meta.json") if wp.suffix == ".pt" else None

        with self._lock:
            self._state._model = model
            self._state._scaler = scaler
            self._state.model_id = model_id or wp.stem
            self._state.weights_path = str(wp)
            self._state.scaler_path = str(sp)
            self._state.weights_sha256 = weights_sha
            self._state.manifest_path = str(mp) if (manifest is not None and mp) else ""
            self._state.integrity = (
                "verified" if manifest is not None else (manifest_reason or "unverified")
            )
            self._state.source_dataset_hash = (
                str(manifest.get("source_dataset_hash", "")) if manifest else ""
            )
            self._state.feature_dim = ADVISER_FEATURE_DIM
            self._state.loaded_at = _utcnow_iso()
            self._state.last_error = ""

        logger.info(
            "[ADVISER] event=MODEL_LOADED model_id=%s classes=%d integrity=%s sha256=%.12s",
            self._state.model_id,
            num_classes,
            self._state.integrity,
            self._state.weights_sha256,
        )
        return {
            "status": "OK",
            "model_id": self._state.model_id,
            "weights_sha256": self._state.weights_sha256,
            "integrity": self._state.integrity,
            "source_dataset_hash": self._state.source_dataset_hash,
            "feature_dim": ADVISER_FEATURE_DIM,
            "loaded_at": self._state.loaded_at,
            "message": "adviser model loaded; activation still DISABLED until set",
        }

    def unload(self) -> dict[str, Any]:
        with self._lock:
            self._state._model = None
            self._state._scaler = None
            self._state.activation = AdviserActivation.DISABLED
            self._state.model_id = ""
            self._state.loaded_at = None
        return {"status": "OK", "message": "adviser unloaded and disabled"}

    # ------------------------------------------------------------- evaluation

    def _bounded_adjustment(self, probs: np.ndarray, confidence: float) -> float:
        """The ONLY channel to execution. Returns <= 0, bounded by the config.

        A CLOSE verdict scales the penalty with its probability advantage over
        KEEP; REDUCE takes at most half the penalty. A KEEP verdict always
        contributes exactly 0.0 — the adviser never rewards a position.
        """
        cfg = self.config
        idx_keep = ADVISER_ACTIONS.index("KEEP")
        idx_close = ADVISER_ACTIONS.index("CLOSE")
        idx_reduce = ADVISER_ACTIONS.index("REDUCE")

        p_keep = float(probs[idx_keep])
        p_close = float(probs[idx_close])
        p_reduce = float(probs[idx_reduce])
        adv = max(p_close, p_reduce) - p_keep

        if adv <= cfg.min_action_advantage:
            return 0.0
        if confidence < cfg.min_confidence_to_apply:
            return 0.0

        frac = min(1.0, adv / max(1e-9, 1.0 - p_keep))
        if p_close >= p_reduce:
            return -round(cfg.max_hold_score_penalty * frac, 4)
        return -round(cfg.max_hold_score_penalty * 0.5 * frac, 4)

    def evaluate(self, ticket: int, position_state: dict[str, Any]) -> PositionAdvisory | None:
        """Evaluate one open position. Returns None when disabled/refused/failed.

        ``position_state`` must supply the causal keys declared by
        features.ADVISER_FEATURE_ORDER PLUS the snapshot contract keys stamped by
        ``integration.build_position_state_for_adviser``:

            snapshot_observed_at  monotonic seconds when the position state was
                                  observed (caller side).
            snapshot_id           content hash of the decision-relevant position
                                  fields at observation time.

        Gate order (F1 — a prediction from stale state must be rejected, not
        applied; a duplicate decision on unchanged state must not fire twice):
        disabled -> loaded -> throttle READ -> snapshot freshness + duplicate
        REJECT -> throttle/snapshot COMMIT -> features -> inference. The commit
        happens only after the snapshot gates pass, so a rejected stale snapshot
        can never lock a ticket's throttle and swallow the next good evaluation.
        Every path that returns None leaves the hold score untouched.
        """
        with self._lock:
            st = self._state
            if st.activation is AdviserActivation.DISABLED:
                return None
            if st._model is None or st._scaler is None:
                return None

        # Throttle READ: one advisory per ticket per min_eval_interval_sec.
        now_mono = time.monotonic()
        with self._lock:
            last = self._last_eval_at.get(ticket)
        if last is not None and (now_mono - last) < self.config.min_eval_interval_sec:
            return None

        # SNAPSHOT FRESHNESS + DUPLICATE GATE (F1). Rejects before any commit,
        # so a stale/duplicate snapshot cannot consume the ticket's throttle.
        try:
            observed_at = float(position_state.get("snapshot_observed_at"))
        except (TypeError, ValueError):
            observed_at = float("nan")
        snapshot_id = str(position_state.get("snapshot_id") or "")
        if not np.isfinite(observed_at) or not snapshot_id:
            with self._lock:
                st.stale_rejected_count += 1
            logger.warning(
                "[ADVISER] event=SNAPSHOT_REJECTED ticket=%s reason=missing_snapshot_contract",
                ticket,
            )
            return None
        snapshot_age_ms = (now_mono - observed_at) * 1000.0
        if snapshot_age_ms < -1000.0 or (now_mono - observed_at) > self.config.max_snapshot_age_sec:
            # Negative beyond clock tolerance = a forged/non-monotonic stamp;
            # older than the ceiling = position state may have changed since.
            with self._lock:
                st.stale_rejected_count += 1
            logger.warning(
                "[ADVISER] event=SNAPSHOT_REJECTED ticket=%s reason=stale age_ms=%.1f max_ms=%.1f",
                ticket,
                snapshot_age_ms,
                self.config.max_snapshot_age_sec * 1000.0,
            )
            return None

        # COMMIT throttle + snapshot identity (bounded maps, F5).
        with self._lock:
            if ticket not in self._last_eval_at and len(self._last_eval_at) >= _MAX_TRACKED_TICKETS:
                # Evict the oldest tracked ticket so a long session with many
                # distinct tickets cannot grow this map without bound.
                self._last_eval_at.pop(next(iter(self._last_eval_at)), None)
                self._last_snapshot_ids.pop(next(iter(self._last_snapshot_ids)), None)
            if (
                ticket not in self._last_snapshot_ids
                and len(self._last_snapshot_ids) >= _MAX_TRACKED_TICKETS
            ):
                self._last_snapshot_ids.pop(next(iter(self._last_snapshot_ids)), None)
            if self._last_snapshot_ids.get(ticket) == snapshot_id:
                # Same decision-relevant state as an already-evaluated
                # snapshot: a duplicate decision. The throttle is committed
                # anyway so an unchanged ticket cannot re-log this every pass.
                self._last_eval_at[ticket] = now_mono
                st.stale_rejected_count += 1
                logger.warning(
                    "[ADVISER] event=SNAPSHOT_REJECTED ticket=%s reason=duplicate_snapshot",
                    ticket,
                )
                return None
            self._last_eval_at[ticket] = now_mono
            self._last_snapshot_ids[ticket] = snapshot_id

        t0 = time.perf_counter()
        applied = False
        not_applied = ""
        try:
            try:
                vec, _ = build_live_vector(position_state)
            except AdviserFeatureError as exc:
                with self._lock:
                    st.refused_count += 1
                    st.last_error = str(exc)
                logger.warning("[ADVISER] event=EVAL_REFUSED ticket=%s reason=%s", ticket, exc)
                return None

            with self._lock:
                model = st._model
                scaler = st._scaler
                if model is None or scaler is None:
                    return None
                x = torch.tensor(scaler.transform(vec.reshape(1, -1)), dtype=torch.float32)

            with torch.no_grad():
                logits = model(x)
            logits_np = logits.float().cpu().numpy().reshape(-1)
            if not np.all(np.isfinite(logits_np)):
                with self._lock:
                    st.refused_count += 1
                    st.last_error = "non-finite logits"
                logger.warning(
                    "[ADVISER] event=EVAL_REFUSED ticket=%s reason=non_finite_logits", ticket
                )
                return None

            z = logits_np[: len(ADVISER_ACTIONS)]
            p = np.exp(z - z.max())
            p = p / p.sum()
            action_idx = int(np.argmax(p))
            action = ACTION_BY_INDEX.get(action_idx, "KEEP")
            probs = {ACTION_BY_INDEX[int(i)]: round(float(p[i]), 4) for i in range(len(p))}
            # confidence = 1 - normalised entropy (same convention as the studio)
            p_c = np.clip(p, 1e-12, 1.0)
            ent = float(-(p_c * np.log(p_c)).sum())
            confidence = round(float(max(0.0, 1.0 - ent / np.log(len(p)))), 4)

            adj = self._bounded_adjustment(p, float(confidence))
            activation = self.activation

            if activation is AdviserActivation.PAPER:
                not_applied = "PAPER mode: advisory logged, no hold-score influence"
            elif activation is AdviserActivation.LIVE and adj < 0.0:
                applied = True
            elif activation is AdviserActivation.LIVE:
                not_applied = "KEEP verdict or below application thresholds"

            advisory = PositionAdvisory(
                ticket=ticket,
                action=action,
                confidence=confidence,
                probabilities=probs,
                hold_score_adjustment=adj,
                activation=activation,
                model_id=st.model_id,
                model_dimension=st.feature_dim,
                evaluated_at=_utcnow_iso(),
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                advisory_id=f"adv_{uuid.uuid4().hex[:12]}",
                applied=applied,
                not_applied_reason=not_applied,
                diagnostics={
                    "p_keep": probs.get("KEEP", 0.0),
                    # Decision-trace evidence (mission §22): which exact
                    # position snapshot produced this prediction, and how old
                    # the snapshot was when inference ran.
                    "snapshot_id": snapshot_id,
                    "snapshot_age_ms": round(snapshot_age_ms, 3),
                },
            )

            with self._lock:
                st.evaluated_count += 1
                if applied:
                    st.applied_count += 1
            return advisory

        except Exception as exc:  # fail closed, never propagate into the hot path
            with self._lock:
                st.refused_count += 1
                st.last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("[ADVISER] event=EVAL_FAILED ticket=%s error=%s", ticket, st.last_error)
            return None

    def forget(self, ticket: int) -> None:
        """Drop throttle + snapshot state for a closed ticket (housekeeping).

        MUST be called on every broker-verified close (wired in
        ``order_manager`` teardown): without it both per-ticket maps outlive
        the position and grow for the process lifetime (F1/F5).
        """
        with self._lock:
            self._last_eval_at.pop(ticket, None)
            self._last_snapshot_ids.pop(ticket, None)


__all__ = [
    "AdviserConfig",
    "AdviserState",
    "PositionAdviserService",
]
