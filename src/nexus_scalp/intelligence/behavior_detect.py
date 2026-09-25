"""Behavior DETECTION engine + the 13 trade-behavior detectors.

Extracted VERBATIM from intelligence/behavior.py (Agent-5 modularization,
behavior-preserving). Owns: detector thresholds (module constants, task §12
versioning), evidence/support-state helpers, the BehaviorDetectionEngine
(analyze / analyze_record / persist) and INSERT_DETECTION_SQL used by persist.

USED BY: intelligence/worker.py, application/live_engine (via the behavior
facade), intelligence/behavior_canonical.py (engine.analyze + engine.persist
only), hygiene/retention.py.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.experience.models import ExperienceRecord
from nexus_scalp.intelligence.models import (
    BehaviorDetection,
    BehaviorSeverity,
)
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.intelligence.behavior")

INSERT_DETECTION_SQL = """
    INSERT INTO behavior_detections
    (behavior_key, behavior_id, ticket, experience_id, ticket_ctx, pattern,
     severity, confidence, evidence, detected_at, autocorrected)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(behavior_key) DO NOTHING;
"""

INSERT_ANALYSIS_SQL = """
    INSERT INTO behavior_analysis
    (analysis_key, ticket, symbol, strategy_id, behavior_version, anomaly_version,
     analyzed_at, evidence_coverage, complete_context, partial_context,
     flags, anomalies)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(analysis_key) DO NOTHING;
"""

INSERT_ANOMALY_SQL = """
    INSERT INTO anomaly_events
    (anomaly_id, ticket, anomaly_type, category, severity, confidence,
     evidence, detected_at, algorithm_version)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(anomaly_id) DO NOTHING;
"""

# ---------------------------------------------------------------------------
# Centralized thresholds (task §13 — no hardcoded magic behavior)
# ---------------------------------------------------------------------------

#: Current detector-set versions (task §12). Bump when thresholds/semantics
#: change; old analysis records remain reproducible under their own version.
BEHAVIOR_ALGORITHM_VERSION: str = "behavior-v1"
ANOMALY_ALGORITHM_VERSION: str = "anomaly-v1"

# HOLD
OVERHOLD_MIN_SECONDS: float = 900.0  # baseline floor (15 min)
OVERHOLD_FACTOR: float = 3.0  # vs expected duration
OVERHOLD_MAE_R_FLOOR: float = 0.5  # invalidation depth
EXCESSIVE_HOLD_MAD_MULT: float = 3.0  # robust outlier vs strategy median
EXCESSIVE_HOLD_MIN_SAMPLE: int = 8  # strategy baseline sample floor

# EXIT / money
GIVEBACK_PCT_MIN: float = 0.60  # share of MFE surrendered
GIVEBACK_MIN_MFE_R: float = 0.5  # meaningful excursion first
MISSED_BE_MIN_MFE_R: float = 0.30  # meaningful positive R reached
MISSED_BE_REVERSAL_MAE_R: float = 0.30  # ...then the trade reversed to a loss
PREMATURE_BE_MAX_MFE_R: float = 0.20  # BE inside normal market noise
PREMATURE_BE_MIN_HOLD_SEC: float = 120.0  # BE before the thesis matured

# MODEL / REGIME
MODEL_REVERSAL_CONF_DROP: float = 0.30  # confidence collapse magnitude
MODEL_REVERSAL_CONF_FLOOR: float = 0.30  # absolute floor at exit
MODEL_REVERSAL_MIN_HOLD_SEC: float = 60.0
REGIME_CHANGE_MIN_HOLD_SEC: float = 300.0
LIQUIDITY_REVERSAL_MIN_HOLD_SEC: float = 300.0

# RISK
RISK_DEVIATION_TOLERANCE: float = 0.15  # relative tolerance vs intended risk

# CONTEXT
STRATEGY_CONTEXT_ANOMALY_SEVERITY = "MEDIUM"

# ---------------------------------------------------------------------------
# Evidence / support states
# ---------------------------------------------------------------------------


def _support_state(confidence: float, evidence_count: int, required: int) -> str:
    """Maps (confidence, evidence_count, required_evidence) to a support state.

    States: OBSERVED / PROBABLE / CONFIRMED / INSUFFICIENT_EVIDENCE.
    """
    if evidence_count < required:
        return "INSUFFICIENT_EVIDENCE"
    if confidence >= 0.85 and evidence_count >= required + 1:
        return "CONFIRMED"
    if confidence >= 0.6:
        return "PROBABLE"
    return "OBSERVED"


def _safe_ratio(numerator: float, denominator: float) -> float:
    if denominator is None or abs(float(denominator)) <= 1e-12:
        return 0.0
    return float(numerator) / float(denominator)


def _json_default(o: Any) -> str:
    """JSON fallback for datetimes in derived records."""
    if isinstance(o, datetime):
        return o.isoformat()
    if hasattr(o, "value"):
        return o.value
    return str(o)


def _jsonable(model: Any) -> dict[str, Any]:
    """Pydantic model -> JSON-safe dict (datetimes serialized)."""
    return json.loads(model.model_dump_json())


class BehaviorDetectionEngine:
    """
    Derives measurable behavioral patterns from canonical evidence.

    The engine receives the richest available evidence (the decomposition, the
    realized R, the MFE/MAE/giveback, the hold duration, and any pre-existing
    Phase 08 flags) and emits zero-or-more `BehaviorDetection` objects.
    """

    def __init__(
        self,
        audit_repo: AuditRepository,
        greed_giveback_pct: float = GIVEBACK_PCT_MIN,
        early_exit_capture_ceil: float = 0.35,
        early_exit_mfe_floor: float = 1.0,
        late_exit_hold_factor: float = OVERHOLD_FACTOR,
        recovery_mae_floor: float = OVERHOLD_MAE_R_FLOOR,
        overtrade_reentry_threshold: int = 3,
    ) -> None:
        self.audit_repo = audit_repo
        self.greed_giveback_pct = greed_giveback_pct
        self.early_exit_capture_ceil = early_exit_capture_ceil
        self.early_exit_mfe_floor = early_exit_mfe_floor
        self.late_exit_hold_factor = late_exit_hold_factor
        self.recovery_mae_floor = recovery_mae_floor
        self.overtrade_reentry_threshold = overtrade_reentry_threshold
        # Bounded telemetry counters.
        self.detection_count = 0

    # ------------------------------------------------------------------
    # Evidence-gated analysis entrypoint
    # ------------------------------------------------------------------

    def _check_profit_giveback(
        self,
        ticket: str,
        exp_id: str,
        ticket_ctx: str,
        giveback_pct: float,
        mfe_r: float,
        realized_r: float,
    ) -> BehaviorDetection | None:
        if giveback_pct >= self.greed_giveback_pct and mfe_r > 0.0:
            conf = min(1.0, 0.5 + max(0.0, giveback_pct - self.greed_giveback_pct))
            return self._detection(
                "PROFIT_GIVEBACK",
                ticket,
                exp_id,
                ticket_ctx,
                BehaviorSeverity.MEDIUM,
                conf,
                {
                    "explanation": "large favourable excursion surrendered before exit",
                    "threshold": self.greed_giveback_pct,
                    "actual": round(giveback_pct, 3),
                    "expected": 0.0,
                    "giveback_pct": round(giveback_pct, 3),
                    "mfe_r": round(mfe_r, 3),
                    "realized_r": round(realized_r, 3),
                },
            )
        return None

    def _check_early_exit(
        self, ticket: str, exp_id: str, ticket_ctx: str, mfe_r: float, realized_r: float
    ) -> BehaviorDetection | None:
        if (
            mfe_r >= self.early_exit_mfe_floor
            and realized_r > 0.0
            and (realized_r / mfe_r) < self.early_exit_capture_ceil
        ):
            return self._detection(
                "EARLY_EXIT_PATTERN",
                ticket,
                exp_id,
                ticket_ctx,
                BehaviorSeverity.MEDIUM,
                0.7,
                {
                    "explanation": "exited before the statistical target zone",
                    "mfe_r": round(mfe_r, 3),
                    "realized_r": round(realized_r, 3),
                    "capture_ratio": round(realized_r / mfe_r, 3),
                },
            )
        return None

    def _check_late_exit(
        self,
        ticket: str,
        exp_id: str,
        ticket_ctx: str,
        holding_duration_sec: float,
        expected_duration_sec: float,
        realized_r: float,
        mae_r: float,
    ) -> list[BehaviorDetection]:
        detections = []
        overhold_time = (
            holding_duration_sec > expected_duration_sec * self.late_exit_hold_factor
            and expected_duration_sec > 0.0
        )
        if overhold_time and realized_r <= 0.0:
            detections.append(
                self._detection(
                    "LATE_EXIT_PATTERN",
                    ticket,
                    exp_id,
                    ticket_ctx,
                    BehaviorSeverity.HIGH,
                    0.8,
                    {
                        "explanation": "held far beyond expected horizon while edge decayed",
                        "holding_duration_sec": round(holding_duration_sec, 1),
                        "expected_duration_sec": round(expected_duration_sec, 1),
                    },
                )
            )
            if (
                abs(mae_r) >= self.recovery_mae_floor
                and holding_duration_sec >= OVERHOLD_MIN_SECONDS
            ):
                detections.append(
                    self._detection(
                        "OVERHOLD_LOSER",
                        ticket,
                        exp_id,
                        ticket_ctx,
                        BehaviorSeverity.HIGH,
                        0.85,
                        {
                            "explanation": (
                                "position spent significant time below zero with the "
                                "model/strategy invalidated and remained open well "
                                "beyond the baseline"
                            ),
                            "threshold": {
                                "min_hold": OVERHOLD_MIN_SECONDS,
                                "hold_factor": self.late_exit_hold_factor,
                                "mae_r_floor": self.recovery_mae_floor,
                            },
                            "actual": {
                                "hold_seconds": round(holding_duration_sec, 1),
                                "mae_r": round(mae_r, 3),
                            },
                            "expected": {
                                "hold_seconds": round(expected_duration_sec, 1),
                                "mae_r": 0.0,
                            },
                            "holding_duration_sec": round(holding_duration_sec, 1),
                            "expected_duration_sec": round(expected_duration_sec, 1),
                            "mae_r": round(mae_r, 3),
                        },
                    )
                )
        return detections

    def _check_excessive_hold(
        self,
        ticket: str,
        exp_id: str,
        ticket_ctx: str,
        holding_duration_sec: float,
        strategy_baseline_median_sec: float | None,
        strategy_baseline_mad_sec: float | None,
    ) -> BehaviorDetection | None:
        if (
            strategy_baseline_median_sec is not None
            and strategy_baseline_median_sec > 0.0
            and strategy_baseline_mad_sec is not None
        ):
            baseline = float(strategy_baseline_median_sec)
            mad = float(strategy_baseline_mad_sec)
            if mad <= 1e-9:
                mad = max(baseline * 0.25, 1.0)
            z = (holding_duration_sec - baseline) / mad
            if holding_duration_sec > baseline and z >= EXCESSIVE_HOLD_MAD_MULT:
                return self._detection(
                    "EXCESSIVE_HOLD_TIME",
                    ticket,
                    exp_id,
                    ticket_ctx,
                    BehaviorSeverity.LOW,
                    min(0.9, 0.5 + z / 10.0),
                    {
                        "explanation": "hold duration is a robust outlier vs the "
                        "strategy baseline (median + MAD)",
                        "threshold": EXCESSIVE_HOLD_MAD_MULT,
                        "actual": round(holding_duration_sec, 1),
                        "expected": round(baseline, 1),
                        "mad": round(mad, 1),
                        "z_score": round(z, 2),
                    },
                )
        return None

    def _check_missed_breakeven(
        self,
        ticket: str,
        exp_id: str,
        ticket_ctx: str,
        mfe_r: float,
        mae_r: float,
        realized_r: float,
        sl_moved: bool,
    ) -> BehaviorDetection | None:
        if (
            mfe_r >= MISSED_BE_MIN_MFE_R
            and mae_r <= -MISSED_BE_REVERSAL_MAE_R
            and realized_r < 0.0
            and not sl_moved
        ):
            return self._detection(
                "MISSED_BREAKEVEN",
                ticket,
                exp_id,
                ticket_ctx,
                BehaviorSeverity.HIGH,
                0.8,
                {
                    "explanation": "trade reached a meaningful positive R then "
                    "returned to a loss with no BE protection",
                    "threshold": {
                        "min_mfe_r": MISSED_BE_MIN_MFE_R,
                        "reversal_mae_r": MISSED_BE_REVERSAL_MAE_R,
                        "be_required": True,
                    },
                    "actual": {"mfe_r": round(mfe_r, 3), "mae_r": round(mae_r, 3)},
                    "expected": {"stop_at_break_even": True},
                    "mfe_r": round(mfe_r, 3),
                    "mae_r": round(mae_r, 3),
                },
            )
        return None

    def _check_premature_breakeven(
        self,
        ticket: str,
        exp_id: str,
        ticket_ctx: str,
        sl_moved: bool,
        exit_mechanism: str,
        mfe_r: float,
        holding_duration_sec: float,
    ) -> BehaviorDetection | None:
        if (
            sl_moved
            and exit_mechanism.upper() in ("BREAK_EVEN_SL_HIT", "RISK_FREE_SL_HIT")
            and mfe_r <= PREMATURE_BE_MAX_MFE_R
            and holding_duration_sec >= PREMATURE_BE_MIN_HOLD_SEC
        ):
            return self._detection(
                "PREMATURE_BREAKEVEN",
                ticket,
                exp_id,
                ticket_ctx,
                BehaviorSeverity.MEDIUM,
                0.7,
                {
                    "explanation": "BE activated while MFE was still inside normal "
                    "market noise, so normal fluctuation hit the BE stop",
                    "threshold": {"max_mfe_r": PREMATURE_BE_MAX_MFE_R},
                    "actual": {"mfe_r": round(mfe_r, 3)},
                    "expected": {"mfe_r_floor": 0.0},
                },
            )
        return None

    def _check_model_reversal_ignored(
        self,
        ticket: str,
        exp_id: str,
        ticket_ctx: str,
        model_flip: float,
        model_conf_at_exit: float | None,
        holding_duration_sec: float,
        realized_r: float,
    ) -> BehaviorDetection | None:
        if (
            model_flip >= 1.0
            and model_conf_at_exit is not None
            and model_conf_at_exit <= MODEL_REVERSAL_CONF_FLOOR
            and holding_duration_sec >= MODEL_REVERSAL_MIN_HOLD_SEC
            and realized_r < 0.0
        ):
            return self._detection(
                "MODEL_REVERSAL_IGNORED",
                ticket,
                exp_id,
                ticket_ctx,
                BehaviorSeverity.HIGH,
                0.8,
                {
                    "explanation": "model direction reversed materially and "
                    "confidence collapsed while the position remained open",
                    "threshold": {
                        "conf_drop": MODEL_REVERSAL_CONF_DROP,
                        "conf_floor": MODEL_REVERSAL_CONF_FLOOR,
                    },
                    "actual": {"conf_at_exit": round(model_conf_at_exit, 3)},
                    "expected": {"conf_floor": 0.0},
                    "model_flip": round(float(model_flip), 3),
                },
            )
        return None

    def _check_regime_change_ignored(
        self,
        ticket: str,
        exp_id: str,
        ticket_ctx: str,
        regime_flip: float,
        regime_at_exit: str,
        holding_duration_sec: float,
        realized_r: float,
    ) -> BehaviorDetection | None:
        if (
            regime_flip >= 1.0
            and regime_at_exit
            and holding_duration_sec >= REGIME_CHANGE_MIN_HOLD_SEC
            and realized_r < 0.0
        ):
            return self._detection(
                "REGIME_CHANGE_IGNORED",
                ticket,
                exp_id,
                ticket_ctx,
                BehaviorSeverity.HIGH,
                0.75,
                {
                    "explanation": "regime transitioned against the position and "
                    "the system continued holding to a loss",
                    "threshold": {"min_hold": REGIME_CHANGE_MIN_HOLD_SEC},
                    "actual": {"regime_at_exit": regime_at_exit},
                    "expected": {"regime": "compatible with entry"},
                    "regime_flip": round(float(regime_flip), 3),
                },
            )
        return None

    def _check_liquidity_reversal_ignored(
        self,
        ticket: str,
        exp_id: str,
        ticket_ctx: str,
        liquidity_sweep_opposite: bool,
        holding_duration_sec: float,
        realized_r: float,
    ) -> BehaviorDetection | None:
        if (
            liquidity_sweep_opposite
            and holding_duration_sec >= LIQUIDITY_REVERSAL_MIN_HOLD_SEC
            and realized_r < 0.0
        ):
            return self._detection(
                "LIQUIDITY_REVERSAL_IGNORED",
                ticket,
                exp_id,
                ticket_ctx,
                BehaviorSeverity.HIGH,
                0.7,
                {
                    "explanation": "liquidity swept opposite the position and the "
                    "system continued holding to a loss",
                    "threshold": {"min_hold": LIQUIDITY_REVERSAL_MIN_HOLD_SEC},
                    "actual": {"sweep_opposite": True},
                    "expected": {"exit_on_sweep": True},
                },
            )
        return None

    def _check_risk_deviation(
        self,
        ticket: str,
        exp_id: str,
        ticket_ctx: str,
        actual_risk_usd: float | None,
        intended_risk_usd: float | None,
    ) -> BehaviorDetection | None:
        if (
            actual_risk_usd is not None
            and intended_risk_usd is not None
            and intended_risk_usd > 0.0
        ):
            deviation = abs(actual_risk_usd - intended_risk_usd) / intended_risk_usd
            if deviation > RISK_DEVIATION_TOLERANCE:
                return self._detection(
                    "RISK_DEVIATION",
                    ticket,
                    exp_id,
                    ticket_ctx,
                    BehaviorSeverity.MEDIUM,
                    min(0.95, 0.5 + deviation),
                    {
                        "explanation": "actual risk deviates from the RiskEngine "
                        "intended risk beyond tolerance",
                        "threshold": RISK_DEVIATION_TOLERANCE,
                        "actual": round(float(actual_risk_usd), 2),
                        "expected": round(float(intended_risk_usd), 2),
                        "deviation": round(deviation, 3),
                    },
                )
        return None

    def _check_exit_classification_anomaly(
        self, ticket: str, exp_id: str, ticket_ctx: str, exit_mechanism: str, sl_moved: bool
    ) -> BehaviorDetection | None:
        mech = exit_mechanism.upper()
        if mech in ("RISK_FREE_SL_HIT", "BREAK_EVEN_SL_HIT") and not sl_moved:
            return self._detection(
                "EXIT_CLASSIFICATION_ANOMALY",
                ticket,
                exp_id,
                ticket_ctx,
                BehaviorSeverity.MEDIUM,
                0.9,
                {
                    "explanation": "exit recorded as risk-free/breakeven while "
                    "was_sl_modified=false — the SL geometry contradicts the "
                    "stored classification",
                    "threshold": {"sl_modified": True},
                    "actual": {"sl_modified": False, "exit_mechanism": mech},
                    "expected": {"sl_modified": True},
                },
            )
        return None

    def _check_strategy_context_loss(
        self, ticket: str, exp_id: str, ticket_ctx: str, record: ExperienceRecord | None
    ) -> BehaviorDetection | None:
        if record is not None and not getattr(record, "strategy_id", ""):
            return self._detection(
                "STRATEGY_CONTEXT_LOSS",
                ticket,
                exp_id,
                ticket_ctx,
                BehaviorSeverity.MEDIUM,
                0.7,
                {
                    "explanation": "closed trade carries no strategy attribution — "
                    "learning/attribution context was lost between entry and exit",
                    "threshold": {"strategy_id": "present"},
                    "actual": {"strategy_id": ""},
                    "expected": {"strategy_id": "present"},
                },
            )
        return None

    def analyze(
        self,
        ticket: str,
        realized_r: float,
        mfe_r: float,
        mae_r: float,
        giveback_pct: float,
        holding_duration_sec: float,
        expected_duration_sec: float,
        exit_mechanism: str,
        risk_reward_ratio: float = 0.0,
        recent_context_entries: int = 0,
        existing_flags: list[str] | None = None,
        record: ExperienceRecord | None = None,
        *,
        sl_moved: bool = False,
        model_flip: float = 0.0,
        model_conf_at_exit: float | None = None,
        regime_flip: float = 0.0,
        regime_at_exit: str = "",
        actual_risk_usd: float | None = None,
        intended_risk_usd: float | None = None,
        liquidity_sweep_opposite: bool = False,
        strategy_baseline_hold_sec: float | None = None,
        strategy_baseline_median_sec: float | None = None,
        strategy_baseline_mad_sec: float | None = None,
    ) -> list[BehaviorDetection]:
        """
        Derives every measurable behavior pattern this position evidences.

        Returns a list of `BehaviorDetection` objects (possibly empty). Never
        raises; a failure in detection is isolated and logged. Every flag
        carries evidence with threshold / actual / expected / explanation.
        """
        _ = set(existing_flags or [])  # legacy Phase-08 flags reference
        detections: list[BehaviorDetection] = []
        ticket_ctx = f"{getattr(record, 'symbol', '')}/{getattr(record, 'timeframe', '')}"
        exp_id = getattr(record, "experience_id", "") if record else ""

        if det := self._check_profit_giveback(
            ticket, exp_id, ticket_ctx, giveback_pct, mfe_r, realized_r
        ):
            detections.append(det)

        if det := self._check_early_exit(ticket, exp_id, ticket_ctx, mfe_r, realized_r):
            detections.append(det)

        detections.extend(
            self._check_late_exit(
                ticket,
                exp_id,
                ticket_ctx,
                holding_duration_sec,
                expected_duration_sec,
                realized_r,
                mae_r,
            )
        )

        if det := self._check_excessive_hold(
            ticket,
            exp_id,
            ticket_ctx,
            holding_duration_sec,
            strategy_baseline_median_sec,
            strategy_baseline_mad_sec,
        ):
            detections.append(det)

        if det := self._check_missed_breakeven(
            ticket, exp_id, ticket_ctx, mfe_r, mae_r, realized_r, sl_moved
        ):
            detections.append(det)

        if det := self._check_premature_breakeven(
            ticket, exp_id, ticket_ctx, sl_moved, exit_mechanism, mfe_r, holding_duration_sec
        ):
            detections.append(det)

        if det := self._check_model_reversal_ignored(
            ticket,
            exp_id,
            ticket_ctx,
            model_flip,
            model_conf_at_exit,
            holding_duration_sec,
            realized_r,
        ):
            detections.append(det)

        if det := self._check_regime_change_ignored(
            ticket,
            exp_id,
            ticket_ctx,
            regime_flip,
            regime_at_exit,
            holding_duration_sec,
            realized_r,
        ):
            detections.append(det)

        if det := self._check_liquidity_reversal_ignored(
            ticket, exp_id, ticket_ctx, liquidity_sweep_opposite, holding_duration_sec, realized_r
        ):
            detections.append(det)

        if det := self._check_risk_deviation(
            ticket, exp_id, ticket_ctx, actual_risk_usd, intended_risk_usd
        ):
            detections.append(det)

        if det := self._check_exit_classification_anomaly(
            ticket, exp_id, ticket_ctx, exit_mechanism, sl_moved
        ):
            detections.append(det)

        if det := self._check_strategy_context_loss(ticket, exp_id, ticket_ctx, record):
            detections.append(det)

        return detections

    def analyze_record(self, record: ExperienceRecord, **kwargs: Any) -> list[BehaviorDetection]:
        """Convenience wrapper: drives `analyze` from an ExperienceRecord."""
        dec = getattr(record, "decomposition", None)
        behavior = getattr(record, "behavior", None)
        mfe_r = float(getattr(dec, "mfe_r", 0.0) or 0.0)
        mae_r = float(getattr(dec, "mae_r", 0.0) or 0.0)
        realized_r = float(getattr(record, "realized_r_multiple", 0.0) or 0.0)
        giveback = float(getattr(behavior, "giveback_pct", 0.0) or 0.0)
        duration = float(getattr(behavior, "duration_sec", 0.0) or 0.0)
        expected = float(getattr(behavior, "expected_duration_sec", 0.0) or 0.0)
        exit_mech = str(getattr(record, "exit_reason", "") or "")
        return self.analyze(
            ticket=str(getattr(record, "execution_id", "") or ""),
            realized_r=realized_r,
            mfe_r=mfe_r,
            mae_r=mae_r,
            giveback_pct=giveback,
            holding_duration_sec=duration,
            expected_duration_sec=expected,
            exit_mechanism=exit_mech,
            existing_flags=[
                f.value if hasattr(f, "value") else str(f)
                for f in getattr(record, "behavioral_flags", [])
            ],
            record=record,
            **kwargs,
        )

    # ------------------------------------------------------------------
    # Detection construction / persistence
    # ------------------------------------------------------------------

    def _detection(
        self,
        pattern: str,
        ticket: str,
        exp_id: str,
        ticket_ctx: str,
        severity: BehaviorSeverity,
        confidence: float,
        evidence: dict[str, Any],
    ) -> BehaviorDetection:
        return BehaviorDetection(
            behavior_id=f"beh_{uuid.uuid4().hex[:12]}",
            ticket=str(ticket),
            experience_id=exp_id,
            pattern=pattern,
            severity=severity,
            confidence=round(min(1.0, max(0.0, confidence)), 4),
            evidence=evidence,
        )

    def persist(self, detection: BehaviorDetection) -> bool:
        """Persists one behavior detection (dedup by content key)."""
        if not self.audit_repo._is_sqlite:
            return False
        behavior_key = self._build_key(
            ticket=detection.ticket, pattern=detection.pattern, evidence=detection.evidence
        )
        args = (
            behavior_key,
            detection.behavior_id,
            detection.ticket,
            detection.experience_id,
            f"{detection.ticket}",
            detection.pattern,
            detection.severity.value,
            detection.confidence,
            json.dumps(detection.evidence, default=_json_default),
            detection.detected_at.isoformat(),
            0,
        )
        try:
            self.audit_repo._queue.put_nowait((INSERT_DETECTION_SQL, args))
            self.detection_count += 1
            return True
        except Exception as e:
            logger.error(
                "[BEHAVIOR] persist failed (isolated)", pattern=detection.pattern, error=str(e)
            )
            return False

    @staticmethod
    def _build_key(ticket: str, pattern: str, evidence: dict[str, Any]) -> str:
        raw = f"{ticket}|{pattern}|{json.dumps(evidence, sort_keys=True, default=_json_default)}"
        return f"beh_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"


# ---------------------------------------------------------------------------
# Canonical-analysis batch driver (task §14/§19/§22)
# ---------------------------------------------------------------------------
