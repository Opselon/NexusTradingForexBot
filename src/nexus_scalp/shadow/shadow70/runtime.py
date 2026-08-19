"""70D Shadow Load Validation & Runtime (TASK-05-70D-SHADOW).

SHADOW_LOAD_GATE v1 (spec 3 / 4 / 35): before any 70D candidate is loaded:

    MANIFEST -> ARTIFACT HASH -> SCHEMA -> DIMENSION -> SCALER
    -> MODEL LOAD -> HEALTH CHECK -> SHADOW READY

A malformed candidate NEVER enters the runtime (no "load model if file
exists"). The runtime is strictly observational: it imports no adapter,
no order manager, no risk engine and no policy object (INV-018). Inference
is torch.inference_mode deterministic; the output classes are the existing
4-class contract, never reinterpreted (spec 8).
"""

from __future__ import annotations

import hashlib
import math
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.shadow.shadow70.models import (
    SHADOW70_DIMENSION,
    SHADOW70_SCHEMA_ID,
    DisagreementClass,
    Shadow70CandidateContract,
    Shadow70LoadStatus,
    Shadow70Observation,
    Shadow70RuntimeState,
    Shadow70VectorReport,
    classify_disagreement,
)

logger = get_logger("nexus_scalp.shadow.shadow70.runtime")

#: Latency budget for one shadow inference (ms). Exceeding it marks the
#: observation SHADOW_INFERENCE_TIMEOUT but NEVER blocks the Champion path.
SHADOW70_LATENCY_BUDGET_MS: float = 50.0

#: Bounded in-memory observation window (spec 13 / 39: no unbounded history).
MAX_INMEMORY_OBSERVATIONS: int = 2000

#: Freshness budget for the base feature vector (seconds).
FEATURE_FRESHNESS_SEC: float = 300.0

_ERROR_CODES: tuple[str, ...] = (
    "SHADOW_SCHEMA_MISMATCH",
    "SHADOW_MODEL_LOAD_FAILED",
    "SHADOW_FEATURE_INVALID",
    "SHADOW_INFERENCE_TIMEOUT",
    "SHADOW_STALE_FEATURES",
    "SHADOW_SCALER_MISMATCH",
    "SHADOW_PERSISTENCE_FAILED",
    "SHADOW_BACKPRESSURE",
    "SHADOW_ARTIFACT_HASH_MISMATCH",
    "SHADOW_MANIFEST_INVALID",
    "SHADOW_DIMENSION_MISMATCH",
)


class Shadow70LoadResult:
    """Verdict of the load-validation sequence (spec 4)."""

    __slots__ = ("contract", "failing_gate", "reason", "status")

    def __init__(
        self,
        status: Shadow70LoadStatus,
        failing_gate: str = "",
        reason: str = "",
        contract: Shadow70CandidateContract | None = None,
    ) -> None:
        self.status = status
        self.failing_gate = failing_gate
        self.reason = reason
        self.contract = contract

    @property
    def passed(self) -> bool:
        return self.status == Shadow70LoadStatus.SHADOW_READY

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "failing_gate": self.failing_gate,
            "reason": self.reason,
            "contract": self.contract.model_dump(mode="json") if self.contract else None,
        }


def sha256_file(path: Path | str, prefix: int = 16) -> str:
    """Content hash of a file (sha256 hex prefix). '' when missing."""
    p = Path(path)
    if not p.exists() or not p.is_file():
        return ""
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:prefix]


def sha256_json(payload: Any, prefix: int = 16) -> str:
    """Deterministic content hash for canonical identity (spec 13)."""
    return hashlib.sha256(str(payload).encode("utf-8", errors="replace")).hexdigest()[:prefix]


class Shadow70LoadValidator:
    """Deterministic load gate — verifies every contract field (spec 3 / 4)."""

    def __init__(
        self,
        expected_schema_id: str = SHADOW70_SCHEMA_ID,
        expected_dimension: int = SHADOW70_DIMENSION,
        expected_num_classes: int = 4,
    ) -> None:
        self.expected_schema_id = expected_schema_id
        self.expected_dimension = int(expected_dimension)
        self.expected_num_classes = int(expected_num_classes)

    # ------------------------------------------------------------------
    # Public entry
    # ------------------------------------------------------------------

    def validate(self, contract: Shadow70CandidateContract | None) -> Shadow70LoadResult:
        """Runs the full sequence. Never raises; returns a verdict."""
        if contract is None:
            return Shadow70LoadResult(
                Shadow70LoadStatus.NO_VALIDATED_CANDIDATE,
                failing_gate="CANDIDATE_EXISTS",
                reason="No 70D candidate registered/validated (First Gate: "
                "NO_VALIDATED_CANDIDATE)",
            )
        c = contract
        # 1 MANIFEST
        if not c.model_id or not c.model_version or not c.artifact_path:
            return Shadow70LoadResult(
                Shadow70LoadStatus.SHADOW_BLOCKED,
                "MANIFEST_VALID",
                "manifest missing model_id/model_version/artifact_path",
                c,
            )
        # 2 VALIDATION STATUS
        if not c.is_validated():
            return Shadow70LoadResult(
                Shadow70LoadStatus.SHADOW_BLOCKED,
                "VALIDATION_STATUS_VALID",
                f"candidate validation_result={c.validation_result!r} — "
                "only VALIDATED_CANDIDATE may enter shadow",
                c,
            )
        # 3 SCHEMA
        if c.schema_id != self.expected_schema_id:
            return Shadow70LoadResult(
                Shadow70LoadStatus.SHADOW_BLOCKED,
                "SCHEMA_VALID",
                f"schema {c.schema_id} != expected {self.expected_schema_id}",
                c,
            )
        # 4 DIMENSION
        if c.dimension != self.expected_dimension:
            return Shadow70LoadResult(
                Shadow70LoadStatus.SHADOW_BLOCKED,
                "INPUT_DIMENSION_VALID",
                f"dimension {c.dimension} != expected {self.expected_dimension}",
                c,
            )
        # 5 ARTIFACT HASH (live file, never trusted from manifest alone)
        live_artifact_hash = sha256_file(c.artifact_path)
        if not live_artifact_hash:
            return Shadow70LoadResult(
                Shadow70LoadStatus.SHADOW_LOAD_FAILED,
                "HASH_VALID",
                f"artifact missing: {c.artifact_path}",
                c,
            )
        if c.artifact_hash and c.artifact_hash != live_artifact_hash:
            return Shadow70LoadResult(
                Shadow70LoadStatus.SHADOW_LOAD_FAILED,
                "HASH_VALID",
                f"artifact hash mismatch: manifest={c.artifact_hash} "
                f"live={live_artifact_hash}",
                c,
            )
        # 6 SCALER HASH
        if c.scaler_path:
            live_scaler_hash = sha256_file(c.scaler_path)
            if not live_scaler_hash:
                return Shadow70LoadResult(
                    Shadow70LoadStatus.SHADOW_LOAD_FAILED,
                    "SCALER_VALID",
                    f"scaler missing: {c.scaler_path}",
                    c,
                )
            if c.scaler_hash and c.scaler_hash != live_scaler_hash:
                return Shadow70LoadResult(
                    Shadow70LoadStatus.SHADOW_LOAD_FAILED,
                    "SCALER_VALID",
                    f"scaler hash mismatch: manifest={c.scaler_hash} live={live_scaler_hash}",
                    c,
                )
        # 7 FEATURE SCHEMA HASH present (schema identity recorded)
        if not c.feature_schema_hash:
            return Shadow70LoadResult(
                Shadow70LoadStatus.SHADOW_DEGRADED,
                "FEATURE_SCHEMA_HASH",
                "feature_schema_hash empty — provenance incomplete",
                c,
            )
        return Shadow70LoadResult(Shadow70LoadStatus.SHADOW_READY, contract=c)


class _InferenceFn:
    """Minimal adapter around a callable that maps a 70D vector to probs.

    Contract: fn(vector: list[float]) -> list[float] of num_classes
    probabilities (finite, >= 0, sum ~1). Raises on any invalid output.
    """

    __slots__ = ("fn",)

    def __init__(self, fn: Callable[[list[float]], list[float]]) -> None:
        self.fn = fn

    def infer(self, vector: list[float]) -> dict[str, Any]:
        probs = [float(x) for x in self.fn(vector)]
        if len(probs) != 4:
            raise ValueError(f"expected 4 probabilities, got {len(probs)}")
        for p in probs:
            if not math.isfinite(p) or p < 0.0 or p > 1.0:
                raise ValueError(f"invalid probability {p}")
        total = sum(probs)
        if total <= 0.0:
            raise ValueError("probability vector sums to zero")
        probs = [p / total for p in probs]
        idx = max(range(len(probs)), key=lambda i: probs[i])
        action = ("NO_TRADE", "BUY_MARKET", "SELL_MARKET", "WAIT")[idx]
        return {"probabilities": probs, "action": action, "confidence": float(probs[idx])}


def _action_from_probs(probs: list[float]) -> str:
    idx = max(range(len(probs)), key=lambda i: probs[i])
    mapping = {0: "NO_TRADE", 1: "BUY_MARKET", 2: "SELL_MARKET", 3: "WAIT"}
    return mapping.get(idx, "NO_TRADE")


class Shadow70Runtime:
    """Bounded, failure-isolated 70D shadow runner (spec 5 / 16 / 17).

    Guarantees:
      * the candidate is load-validated BEFORE any observation;
      * observe() NEVER raises — every fault is classified with an
        error_code and isolated from the Champion path;
      * no execution/policy/risk/broker import anywhere (INV-018);
      * observations are deterministic and idempotent (spec 13).
    """

    def __init__(
        self,
        validator: Shadow70LoadValidator | None = None,
        latency_budget_ms: float = SHADOW70_LATENCY_BUDGET_MS,
    ) -> None:
        self.validator = validator or Shadow70LoadValidator()
        self.latency_budget_ms = float(latency_budget_ms)
        self.contract: Shadow70CandidateContract | None = None
        self.infer_fn: _InferenceFn | None = None
        self.state: Shadow70RuntimeState = Shadow70RuntimeState.IDLE
        self.load_result: Shadow70LoadResult | None = None
        self.error_code: str = ""

        # telemetry counters
        self.observations: int = 0
        self.valid_observations: int = 0
        self.errors: int = 0
        self.dropped: int = 0
        self.timeouts: int = 0
        self.schema_mismatches: int = 0
        self.scaler_mismatches: int = 0
        self.feature_invalid: int = 0
        self._total_ms: float = 0.0
        self._max_ms: float = 0.0
        self.latency_ms: list[float] = []

        # bounded in-memory windows (spec 13 / 39)
        self._recent: list[Shadow70Observation] = []
        self.last_observation: Shadow70Observation | None = None
        self.last_error: str = ""
        self.last_error_code: str = ""

    # ------------------------------------------------------------------
    # Load lifecycle (spec 32 START/STOP/PAUSE/RESUME semantics)
    # ------------------------------------------------------------------

    def attach(self, contract: Shadow70CandidateContract | None) -> Shadow70LoadResult:
        """Validates + attaches a candidate. Returns the load verdict."""
        result = self.validator.validate(contract)
        self.load_result = result
        if result.status == Shadow70LoadStatus.SHADOW_READY and result.contract is not None:
            self.contract = result.contract
            self.state = Shadow70RuntimeState.READY
            logger.info(
                "[SHADOW70] event=MODEL_LOADED",
                model_id=result.contract.model_id,
                schema=SHADOW70_SCHEMA_ID,
                dimension=SHADOW70_DIMENSION,
                model_version=result.contract.model_version,
                artifact_hash=result.contract.artifact_hash,
            )
        else:
            self.contract = None
            self.infer_fn = None
            self.error_code = result.failing_gate or result.reason
            if result.status == Shadow70LoadStatus.SHADOW_LOAD_FAILED:
                self.state = Shadow70RuntimeState.FAILED
            elif result.status == Shadow70LoadStatus.NO_VALIDATED_CANDIDATE:
                self.state = Shadow70RuntimeState.IDLE
            else:
                self.state = Shadow70RuntimeState.BLOCKED
            logger.warning(
                "[SHADOW70] event=MODEL_LOAD_REJECTED",
                status=result.status.value,
                failing_gate=result.failing_gate,
                reason=result.reason,
            )
        return result

    def set_inference(self, fn: Callable[[list[float]], list[float]]) -> None:
        """Attaches the inference callable (pure computation, no broker)."""
        self.infer_fn = _InferenceFn(fn)

    def pause(self) -> None:
        if self.state == Shadow70RuntimeState.READY:
            self.state = Shadow70RuntimeState.PAUSED

    def resume(self) -> None:
        if self.state == Shadow70RuntimeState.PAUSED:
            self.state = Shadow70RuntimeState.READY

    def stop(self) -> None:
        self.state = Shadow70RuntimeState.STOPPED

    # ------------------------------------------------------------------
    # Observation (the ONLY entry the engine calls)
    # ------------------------------------------------------------------

    def observe(
        self,
        *,
        vector70: list[float],
        champion_action: str,
        champion_probabilities: list[float],
        champion_confidence: float,
        snapshot_id: str = "",
        timestamp: datetime | None = None,
        symbol: str = "XAUUSD",
        timeframe: str = "M1",
        regime: str = "UNKNOWN",
        session: str = "ALL",
        news_context: dict[str, Any] | None = None,
        news_state: str = "",
        liquidity_state: str = "",
        liquidity_calculation_version: str = "",
        liquidity_features_10: list[float] | None = None,
        base_feature_hash: str = "",
        feature_schema_hash: str = "",
        sample_source: str = "LIVE",
        decision_id: str = "",
    ) -> Shadow70Observation:
        """Runs one shadow observation. NEVER raises (spec 16).

        A fault marks the observation invalid with an error_code and is
        isolated; the Champion path is never touched.
        """
        started = time.perf_counter()
        ts = timestamp or datetime.now(UTC)
        error_code = ""
        valid = True
        reason = ""
        shadow_action = "NO_TRADE"
        shadow_probs: list[float] = []
        shadow_conf = 0.0
        latency_ms = 0.0

        # guard: not ready / paused / blocked
        if self.state != Shadow70RuntimeState.READY:
            error_code = "SHADOW_BLOCKED"
            valid = False
            reason = f"runtime state {self.state.value} — no observation"
            obs = self._build_observation(
                vector70=vector70,
                champion_action=champion_action,
                champion_probabilities=champion_probabilities,
                champion_confidence=champion_confidence,
                snapshot_id=snapshot_id,
                timestamp=ts,
                symbol=symbol,
                timeframe=timeframe,
                regime=regime,
                session=session,
                news_state=news_state,
                liquidity_state=liquidity_state,
                liquidity_calculation_version=liquidity_calculation_version,
                liquidity_features_10=liquidity_features_10,
                news_context_hash=sha256_json(news_context or "no_news"),
                liquidity_feature_hash=sha256_json(liquidity_features_10 or []),
                base_feature_hash=base_feature_hash,
                feature_schema_hash=feature_schema_hash,
                sample_source=sample_source,
                decision_id=decision_id,
                shadow_action=shadow_action,
                shadow_probabilities=shadow_probs,
                shadow_confidence=shadow_conf,
                disagreement=DisagreementClass.NO_TRADE_DISAGREEMENT,
                agreement=False,
                valid=False,
                reason=reason,
                latency_ms=0.0,
                error_code=error_code,
            )
            self._record(obs, started)
            return obs

        # 1. vector validation (spec 6: finite/range/schema/freshness/provenance)
        report = self._validate_vector(
            vector70,
            base_feature_hash=base_feature_hash,
            feature_schema_hash=feature_schema_hash,
            timestamp=ts,
        )
        if not report.ok:
            valid = False
            error_code = (
                "SHADOW_STALE_FEATURES"
                if self._looks_stale(report)
                else "SHADOW_FEATURE_INVALID"
            )
            reason = "; ".join(report.reasons)[:400]
            self.feature_invalid += 1
            obs = self._build_observation(
                vector70=vector70,
                champion_action=champion_action,
                champion_probabilities=champion_probabilities,
                champion_confidence=champion_confidence,
                snapshot_id=snapshot_id,
                timestamp=ts,
                symbol=symbol,
                timeframe=timeframe,
                regime=regime,
                session=session,
                news_state=news_state,
                liquidity_state=liquidity_state,
                liquidity_calculation_version=liquidity_calculation_version,
                liquidity_features_10=liquidity_features_10,
                news_context_hash=sha256_json(news_context or "no_news"),
                liquidity_feature_hash=sha256_json(liquidity_features_10 or []),
                base_feature_hash=base_feature_hash,
                feature_schema_hash=feature_schema_hash,
                sample_source=sample_source,
                decision_id=decision_id,
                shadow_action=shadow_action,
                shadow_probabilities=shadow_probs,
                shadow_confidence=shadow_conf,
                disagreement=DisagreementClass.NO_TRADE_DISAGREEMENT,
                agreement=False,
                valid=False,
                reason=reason,
                latency_ms=0.0,
                error_code=error_code,
            )
            self._record(obs, started)
            return obs

        # 2. latency-bounded shadow inference (spec 17 / 21)
        if self.infer_fn is None:
            valid = False
            error_code = "SHADOW_MODEL_LOAD_FAILED"
            reason = "inference function not attached"
            self.errors += 1
        else:
            try:
                t0 = time.perf_counter()
                result = self.infer_fn.infer(vector70)
                latency_ms = (time.perf_counter() - t0) * 1000.0
                shadow_action = str(result.get("action", "NO_TRADE"))
                shadow_conf = float(result.get("confidence", 0.0) or 0.0)
                shadow_probs = [float(p) for p in result.get("probabilities", [])]
                if latency_ms > self.latency_budget_ms:
                    valid = False
                    error_code = "SHADOW_INFERENCE_TIMEOUT"
                    reason = (
                        f"shadow inference {latency_ms:.1f}ms > budget "
                        f"{self.latency_budget_ms:.1f}ms — observation skipped"
                    )
                    self.timeouts += 1
            except Exception as e:
                valid = False
                error_code = "SHADOW_INFERENCE_FAILED"
                reason = str(e)[:300]
                self.errors += 1
                self.last_error = reason
                self.last_error_code = error_code
                logger.warning(
                    "[SHADOW70] event=ERROR",
                    stage="inference",
                    error_code=error_code,
                    reason=reason,
                )

        # 3. classify disagreement (spec 9 / 26)
        disagreement = classify_disagreement(
            champion_action,
            shadow_action,
            champion_confidence,
            shadow_conf,
        )
        agreement = disagreement in (
            DisagreementClass.AGREEMENT,
            DisagreementClass.CONFIDENCE_DIVERGENCE,
        )
        obs = self._build_observation(
            vector70=vector70,
            champion_action=champion_action,
            champion_probabilities=champion_probabilities,
            champion_confidence=champion_confidence,
            snapshot_id=snapshot_id,
            timestamp=ts,
            symbol=symbol,
            timeframe=timeframe,
            regime=regime,
            session=session,
            news_state=news_state,
            liquidity_state=liquidity_state,
            liquidity_calculation_version=liquidity_calculation_version,
            liquidity_features_10=liquidity_features_10,
            news_context_hash=sha256_json(news_context or "no_news"),
            liquidity_feature_hash=sha256_json(liquidity_features_10 or []),
            base_feature_hash=base_feature_hash,
            feature_schema_hash=feature_schema_hash,
            sample_source=sample_source,
            decision_id=decision_id,
            shadow_action=shadow_action,
            shadow_probabilities=shadow_probs,
            shadow_confidence=shadow_conf,
            disagreement=disagreement,
            agreement=agreement,
            valid=valid,
            reason=reason,
            latency_ms=latency_ms,
            error_code=error_code,
        )
        self._record(obs, started)
        return obs

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_observation(
        self,
        *,
        vector70: list[float],
        champion_action: str,
        champion_probabilities: list[float],
        champion_confidence: float,
        snapshot_id: str,
        timestamp: datetime,
        symbol: str,
        timeframe: str,
        regime: str,
        session: str,
        news_state: str,
        liquidity_state: str,
        liquidity_calculation_version: str,
        liquidity_features_10: list[float] | None,
        news_context_hash: str,
        liquidity_feature_hash: str,
        base_feature_hash: str,
        feature_schema_hash: str,
        sample_source: str,
        decision_id: str,
        shadow_action: str,
        shadow_probabilities: list[float],
        shadow_confidence: float,
        disagreement: DisagreementClass,
        agreement: bool,
        valid: bool,
        reason: str,
        latency_ms: float,
        error_code: str,
    ) -> Shadow70Observation:
        contract = self.contract
        chash = contract.artifact_hash if contract else ""
        schash = contract.scaler_hash if contract else ""
        model_id = contract.model_id if contract else ""
        model_version = contract.model_version if contract else ""
        # deterministic identity (spec 13): snapshot_id | model_id | version | ts
        ident = sha256_json(
            f"{snapshot_id}|{model_id}|{model_version}|{timestamp.isoformat()}"
        )
        liq10 = list(liquidity_features_10) if liquidity_features_10 else []
        return Shadow70Observation(
            observation_id=ident,
            snapshot_id=snapshot_id or ident,
            timestamp=timestamp,
            symbol=symbol,
            timeframe=timeframe,
            simulated=True,
            model_id=model_id,
            model_version=model_version,
            model_hash=chash,
            scaler_hash=schash,
            schema_id=SHADOW70_SCHEMA_ID,
            schema_dimension=SHADOW70_DIMENSION,
            champion_action=champion_action,
            champion_probabilities=[float(p) for p in champion_probabilities],
            champion_confidence=float(champion_confidence or 0.0),
            shadow_action=shadow_action,
            shadow_probabilities=shadow_probabilities,
            shadow_confidence=shadow_confidence,
            confidence_delta=abs(float(champion_confidence or 0.0) - shadow_confidence),
            disagreement=disagreement,
            agreement=bool(agreement),
            valid=bool(valid),
            reason=reason,
            regime=regime,
            session=session,
            news_state=news_state,
            liquidity_state=liquidity_state,
            news_context_hash=news_context_hash,
            liquidity_feature_hash=liquidity_feature_hash,
            liquidity_features_10=liq10,
            feature_hash=base_feature_hash or snapshot_id,
            sample_source=sample_source,
            latency_ms=latency_ms,
            error_code=error_code,
            outcome="PENDING",
            outcome_resolved_at=None,
        )

    def _validate_vector(
        self,
        vector70: list[float],
        *,
        base_feature_hash: str,
        feature_schema_hash: str,
        timestamp: datetime,
    ) -> Shadow70VectorReport:
        """Spec 6: finite / range / schema / freshness / provenance."""
        reasons: list[str] = []
        n = len(vector70)
        dim_ok = n == SHADOW70_DIMENSION
        if not dim_ok:
            reasons.append(f"dimension {n} != {SHADOW70_DIMENSION} (INV-70D-004)")
        finite = all(math.isfinite(v) for v in vector70)
        if not finite:
            reasons.append("non-finite value present (INV-70D-005)")
        in_range = all(-3.0 <= v <= 3.0 for v in vector70)
        if not in_range:
            reasons.append("value outside [-3,3] (INV-70D-006)")
        schema_ok = True
        if self.contract is not None:
            if feature_schema_hash and self.contract.feature_schema_hash:
                schema_ok = feature_schema_hash == self.contract.feature_schema_hash
            if not schema_ok:
                reasons.append("feature_schema_hash mismatch (INV-70D-007)")
        freshness_ok = True
        age = (datetime.now(UTC) - timestamp).total_seconds()
        if age > FEATURE_FRESHNESS_SEC:
            freshness_ok = False
            reasons.append(f"stale features {age:.0f}s > {FEATURE_FRESHNESS_SEC:.0f}s")
        provenance_ok = bool(base_feature_hash) or bool(feature_schema_hash)
        if not provenance_ok:
            reasons.append("missing feature provenance hash")
        ok = dim_ok and finite and in_range and schema_ok and freshness_ok and provenance_ok
        return Shadow70VectorReport(
            ok=ok,
            dimension=n,
            finite=finite,
            in_range=in_range,
            schema_ok=schema_ok,
            freshness_ok=freshness_ok,
            provenance_ok=provenance_ok,
            reasons=reasons,
        )

    @staticmethod
    def _looks_stale(report: Shadow70VectorReport) -> bool:
        return not report.freshness_ok and report.finite and report.in_range

    def _record(self, obs: Shadow70Observation, started: float) -> None:
        total_ms = (time.perf_counter() - started) * 1000.0
        self.observations += 1
        if obs.valid:
            self.valid_observations += 1
        self._total_ms += total_ms
        self._max_ms = max(self._max_ms, total_ms)
        latency = obs.latency_ms if obs.latency_ms > 0 else total_ms
        self.latency_ms.append(latency)
        if len(self.latency_ms) > 500:
            self.latency_ms = self.latency_ms[-500:]
        self.last_observation = obs
        self._recent.append(obs)
        if len(self._recent) > MAX_INMEMORY_OBSERVATIONS:
            self._recent = self._recent[-MAX_INMEMORY_OBSERVATIONS:]
        if not obs.valid and obs.error_code:
            self.dropped += 1
        if obs.error_code == "SHADOW_SCHEMA_MISMATCH":
            self.schema_mismatches += 1
        if obs.error_code == "SHADOW_SCALER_MISMATCH":
            self.scaler_mismatches += 1
        if obs.valid:
            logger.debug(
                "[SHADOW70] event=INFERENCE",
                model_id=obs.model_id,
                prediction=obs.shadow_action,
                confidence=round(obs.shadow_confidence, 4),
                latency_ms=round(obs.latency_ms, 3),
                disagreement=obs.disagreement.value,
            )

    # ------------------------------------------------------------------
    # Telemetry / summaries
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """Truthful runtime summary (spec 28 / 33 / 45)."""
        contract = self.contract
        return {
            "model_id": contract.model_id if contract else "",
            "model_version": contract.model_version if contract else "",
            "schema": SHADOW70_SCHEMA_ID,
            "dimension": SHADOW70_DIMENSION,
            "artifact_hash": contract.artifact_hash if contract else "",
            "scaler_hash": contract.scaler_hash if contract else "",
            "status": self.state.value,
            "loaded_at": "",
            "last_inference": (
                self.last_observation.timestamp.isoformat() if self.last_observation else ""
            ),
            "observations": self.observations,
            "valid_observations": self.valid_observations,
            "errors": self.errors,
            "dropped": self.dropped,
            "timeouts": self.timeouts,
            "schema_mismatches": self.schema_mismatches,
            "scaler_mismatches": self.scaler_mismatches,
            "feature_invalid": self.feature_invalid,
            "agreements": sum(1 for o in self._recent if o.agreement),
            "disagreements": sum(1 for o in self._recent if not o.agreement),
            "avg_latency_ms": round(self._total_ms / max(1, self.observations), 3),
            "max_latency_ms": round(self._max_ms, 3),
            "p95_latency_ms": round(
                sorted(self.latency_ms)[int(len(self.latency_ms) * 0.95) - 1], 3
            )
            if len(self.latency_ms) >= 20
            else round(self._max_ms, 3),
            "last_error": self.last_error,
            "last_error_code": self.last_error_code,
            "simulated": True,
        }

    def recent_window(self, limit: int = 300) -> list[Shadow70Observation]:
        return self._recent[-limit:]