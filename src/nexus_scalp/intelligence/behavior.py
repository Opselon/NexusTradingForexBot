"""
Behavior Detection Engine
=========================
PHASE 09 measurable behavioral-pattern detection (TASK-2 upgraded).

Every pattern is derived from RECORDED NUMBERS ONLY. There is no emotional,
psychological or intent-based attribution anywhere in this module - a "greed"
label is never asserted; instead the objectively computable
`PROFIT_GIVEBACK` is derived from a high profit giveback percentage. The system
observes what the data provably shows.

Detectable behavior classes (evidence-gated detectors):

    HOLD:
        OVERHOLD_LOSER           held a losing trade far beyond the expected
                                 horizon while evidence (MAE) invalidated it
        EXCESSIVE_HOLD_TIME      hold duration is a robust outlier vs the
                                 strategy baseline (median + MAD)
    EXIT:
        PROFIT_GIVEBACK          large MFE surrendered before exit
        MISSED_BREAKEVEN         reached meaningful positive R, returned to a
                                 loss, and no BE action occurred
        PREMATURE_BREAKEVEN      BE activated while MFE was still inside normal
                                 market noise
        EXIT_CLASSIFICATION_ANOMALY  recorded exit class contradicts the stored
                                 SL geometry (e.g. RISK_FREE_SL_HIT with
                                 was_sl_modified=false)
    MODEL / REGIME:
        MODEL_REVERSAL_IGNORED   model direction reversed materially and the
                                 position remained open to a loss
        REGIME_CHANGE_IGNORED    regime transitioned against the position and
                                 the system continued holding
        LIQUIDITY_REVERSAL_IGNORED  liquidity sweep opposite the position with
                                 the position still open
    RISK:
        RISK_DEVIATION           actual risk vs intended risk beyond tolerance
    CONTEXT:
        STRATEGY_CONTEXT_LOSS    entry has strategy context but the closed
                                 record does not
        DUPLICATE_ECONOMIC_OUTCOME  two ledger outcomes for one economic trade

Every detection carries: behavior_id, evidence (with threshold / actual /
expected / explanation), severity, confidence, timestamp.
Detections are append-only and persisted through the audit queue.

Algorithm versioning
--------------------
`behavior-v1` / `anomaly-v1` are the first supported detector sets. When a
threshold or semantic changes, bump the version — old analysis records remain
reproducible (task §12). Thresholds are centralized here (task §13), never
scattered magic numbers.

Idempotency
-----------
Derived analysis records key on (ticket, behavior_version, anomaly_version).
Re-running identical versions over identical source data MUST NOT duplicate
records.
"""

from __future__ import annotations

import hashlib
import json
import statistics
import uuid
from datetime import datetime
from typing import Any

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.experience.models import ExperienceRecord
from nexus_scalp.intelligence.models import (
    AnomalyEvent,
    BehaviorAnalysis,
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

        # -- PROFIT_GIVEBACK (formerly GREED_PATTERN) -------------------
        if giveback_pct >= self.greed_giveback_pct and mfe_r > 0.0:
            conf = min(1.0, 0.5 + max(0.0, giveback_pct - self.greed_giveback_pct))
            detections.append(
                self._detection(
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
            )

        # -- EARLY_EXIT_PATTERN -----------------------------------------
        if (
            mfe_r >= self.early_exit_mfe_floor
            and realized_r > 0.0
            and (realized_r / mfe_r) < self.early_exit_capture_ceil
        ):
            detections.append(
                self._detection(
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
            )

        # -- LATE_EXIT_PATTERN / OVERHOLD_LOSER -------------------------
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
            # OVERHOLD_LOSER = the same evidence + invalidation depth.
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

        # -- EXCESSIVE_HOLD_TIME (robust strategy baseline outlier) ------
        if (
            strategy_baseline_median_sec is not None
            and strategy_baseline_median_sec > 0.0
            and strategy_baseline_mad_sec is not None
        ):
            baseline = float(strategy_baseline_median_sec)
            mad = float(strategy_baseline_mad_sec)
            if mad <= 1e-9:
                mad = max(baseline * 0.25, 1.0)  # degenerate MAD fallback
            z = (holding_duration_sec - baseline) / mad
            if holding_duration_sec > baseline and z >= EXCESSIVE_HOLD_MAD_MULT:
                detections.append(
                    self._detection(
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
                )

        # -- MISSED_BREAKEVEN -------------------------------------------
        if (
            mfe_r >= MISSED_BE_MIN_MFE_R
            and mae_r <= -MISSED_BE_REVERSAL_MAE_R
            and realized_r < 0.0
            and not sl_moved
        ):
            detections.append(
                self._detection(
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
            )

        # -- PREMATURE_BREAKEVEN -----------------------------------------
        if (
            sl_moved
            and exit_mechanism.upper() in ("BREAK_EVEN_SL_HIT", "RISK_FREE_SL_HIT")
            and mfe_r <= PREMATURE_BE_MAX_MFE_R
            and holding_duration_sec >= PREMATURE_BE_MIN_HOLD_SEC
        ):
            detections.append(
                self._detection(
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
            )

        # -- MODEL_REVERSAL_IGNORED --------------------------------------
        if (
            model_flip >= 1.0
            and model_conf_at_exit is not None
            and model_conf_at_exit <= MODEL_REVERSAL_CONF_FLOOR
            and holding_duration_sec >= MODEL_REVERSAL_MIN_HOLD_SEC
            and realized_r < 0.0
        ):
            detections.append(
                self._detection(
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
            )

        # -- REGIME_CHANGE_IGNORED ----------------------------------------
        if (
            regime_flip >= 1.0
            and regime_at_exit
            and holding_duration_sec >= REGIME_CHANGE_MIN_HOLD_SEC
            and realized_r < 0.0
        ):
            detections.append(
                self._detection(
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
            )

        # -- LIQUIDITY_REVERSAL_IGNORED ----------------------------------
        if (
            liquidity_sweep_opposite
            and holding_duration_sec >= LIQUIDITY_REVERSAL_MIN_HOLD_SEC
            and realized_r < 0.0
        ):
            detections.append(
                self._detection(
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
            )

        # -- RISK_DEVIATION (canonical vs intended) ----------------------
        if (
            actual_risk_usd is not None
            and intended_risk_usd is not None
            and intended_risk_usd > 0.0
        ):
            deviation = abs(actual_risk_usd - intended_risk_usd) / intended_risk_usd
            if deviation > RISK_DEVIATION_TOLERANCE:
                detections.append(
                    self._detection(
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
                )

        # -- EXIT_CLASSIFICATION_ANOMALY ---------------------------------
        mech = exit_mechanism.upper()
        if mech in ("RISK_FREE_SL_HIT", "BREAK_EVEN_SL_HIT") and not sl_moved:
            detections.append(
                self._detection(
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
            )

        # -- STRATEGY_CONTEXT_LOSS ----------------------------------------
        if record is not None and not getattr(record, "strategy_id", ""):
            detections.append(
                self._detection(
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
            )

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


def _build_analysis_key(ticket: str, behavior_version: str, anomaly_version: str) -> str:
    raw = f"{ticket}|{behavior_version}|{anomaly_version}"
    return f"ana_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def _coverage_fields(record: Any, trade: Any) -> tuple[float, int, int]:
    """
    Evidence-coverage estimate for one trade.

    `complete_context` counts the fields a behavioral analysis actually needs;
    `partial_context` counts fields that exist but are sparse/zero. Coverage is
    complete/(complete+partial) — a zero-flag result with 100% coverage means
    something very different from zero flags at 20% coverage (task §16).
    """
    complete = 0
    partial = 0
    checks: list[bool] = [
        bool(getattr(trade, "mae_points", 0.0) or getattr(trade, "mae_usd", 0.0)),
        bool(getattr(trade, "mfe_points", 0.0) or getattr(trade, "mfe_usd", 0.0)),
        getattr(trade, "realized_r", None) is not None,
        getattr(trade, "risk_usd", None) is not None,
        getattr(trade, "duration_sec", 0.0) > 0.0,
        bool(getattr(trade, "exit_mechanism_raw", "")),
        getattr(trade, "closed_at", None) is not None,
    ]
    complete = sum(1 for c in checks if c)
    partial = len(checks) - complete
    coverage = complete / len(checks) if checks else 0.0
    return round(coverage, 4), complete, partial


def analyze_canonical_trades(
    audit_repo: AuditRepository,
    engine: BehaviorDetectionEngine,
    behavior_version: str = "behavior-v1",
    anomaly_version: str = "anomaly-v1",
    max_trades: int = 200,
) -> dict[str, Any]:
    """
    Runs the detector set over canonical closed trades and persists derived
    records. This is the offline/background path (NEVER on the tick hot path).

    Idempotency: records key on (ticket, behavior_version, anomaly_version);
    a ticket already analyzed under these versions is skipped.

    Returns a summary dict: analyzed / skipped / flags / anomalies / coverage.
    """
    from nexus_scalp.accounting.normalize import normalize_trade_row

    if not audit_repo._is_sqlite:
        return {"analyzed": 0, "skipped": 0, "flags": 0, "anomalies": 0, "coverage": 0.0}

    import sqlite3

    conn = None
    try:
        conn = sqlite3.connect(audit_repo._db_path, timeout=5.0)
        conn.row_factory = None

        # Existing analysis keys under these versions (idempotency set).
        done_rows = conn.execute(
            "SELECT analysis_key, ticket FROM behavior_analysis "
            "WHERE behavior_version = ? AND anomaly_version = ?",
            (behavior_version, anomaly_version),
        ).fetchall()
        done_tickets = {str(r[1]) for r in done_rows}

        rows = conn.execute(
            "SELECT * FROM audit_ledger WHERE status != 'OPENED' "
            "AND close_time != '' ORDER BY close_time DESC LIMIT ?",
            (max_trades,),
        ).fetchall()
        cols = [d[0] for d in conn.execute("SELECT * FROM audit_ledger LIMIT 0").description]
    finally:
        if conn is not None:
            conn.close()

    analyzed = 0
    skipped = 0
    flags_total = 0
    anomalies_total = 0
    coverage_sum = 0.0

    for raw in rows:
        row = dict(zip(cols, raw, strict=False))
        ticket = str(row.get("ticket", ""))
        if not ticket or ticket in done_tickets:
            skipped += 1
            continue
        trade = normalize_trade_row(row)

        # Robust strategy baseline for EXCESSIVE_HOLD_TIME.
        baseline = _strategy_hold_baseline(audit_repo, trade.strategy_id, ticket)

        coverage, complete_n, partial_n = _coverage_fields(None, trade)
        detections: list[BehaviorDetection] = []
        anomalies: list[AnomalyEvent] = []

        mfe_r = float(trade.mfe_r or 0.0)
        mae_r = float(trade.mae_r or 0.0)
        realized_r = float(trade.realized_r or 0.0)
        giveback = _giveback_fraction(trade)
        detections = engine.analyze(
            ticket=ticket,
            realized_r=realized_r,
            mfe_r=mfe_r,
            mae_r=mae_r,
            giveback_pct=giveback,
            holding_duration_sec=float(trade.duration_sec or 0.0),
            expected_duration_sec=baseline["median"] if baseline["median"] else 600.0,
            exit_mechanism=trade.exit_mechanism_raw or "UNKNOWN",
            sl_moved=bool(trade.was_sl_modified),
            actual_risk_usd=float(trade.risk_usd or 0.0) if trade.risk_usd else None,
            intended_risk_usd=None,
            strategy_baseline_median_sec=baseline["median"],
            strategy_baseline_mad_sec=baseline["mad"],
            strategy_baseline_hold_sec=baseline["mean"],
        )

        # -- data/context anomalies for this trade ------------------------
        anomalies.extend(_trade_data_anomalies(trade, ticket, anomaly_version))

        for det in detections:
            engine.persist(det)
        flags_total += len(detections)

        for anomaly in anomalies:
            _persist_anomaly(audit_repo, anomaly, anomaly_version)
        anomalies_total += len(anomalies)

        analysis = BehaviorAnalysis(
            ticket=ticket,
            symbol=trade.symbol,
            strategy_id=trade.strategy_id,
            behavior_version=behavior_version,
            anomaly_version=anomaly_version,
            evidence_coverage=coverage,
            complete_context=complete_n,
            partial_context=partial_n,
            flags=[_jsonable(d) for d in detections],
            anomalies=[_jsonable(a) for a in anomalies],
        )
        _persist_analysis(audit_repo, analysis)
        analyzed += 1
        coverage_sum += coverage

    # -- batch-level anomalies: duplicate economic outcomes ----------------
    dup_anomalies = _duplicate_outcome_anomalies(audit_repo, anomaly_version)
    for anomaly in dup_anomalies:
        _persist_anomaly(audit_repo, anomaly, anomaly_version)
    anomalies_total += len(dup_anomalies)

    # Deterministic batch semantics: drain the async audit queue so the
    # caller can observe persisted records immediately after this returns.
    # This is the OFFLINE path (never the tick hot path) — a bounded join is
    # safe and keeps idempotency checks truthful.
    try:
        audit_repo._queue.join()
    except Exception:
        pass

    return {
        "analyzed": analyzed,
        "skipped": skipped,
        "flags": flags_total,
        "anomalies": anomalies_total,
        "coverage": round(coverage_sum / analyzed, 4) if analyzed else 0.0,
    }


def _giveback_fraction(trade: Any) -> float:
    """MFE -> realized surrender fraction, 0..1 (0 when unknown)."""
    mfe_usd = abs(float(getattr(trade, "mfe_usd", 0.0) or 0.0))
    net = float(getattr(trade, "net_pnl", 0.0) or 0.0)
    if mfe_usd <= 1e-9:
        return 0.0
    if net >= mfe_usd:
        return 0.0
    return max(0.0, min(1.0, (mfe_usd - net) / mfe_usd))


def _strategy_hold_baseline(
    audit_repo: AuditRepository, strategy_id: str, exclude_ticket: str
) -> dict[str, float | None]:
    """Robust per-strategy hold-duration baseline (median + MAD)."""
    if not strategy_id:
        return {"median": None, "mad": None, "mean": None}
    try:
        from nexus_scalp.experience import ExperienceLedger

        ledger = ExperienceLedger(audit_repo=audit_repo)
        records = ledger.get_experiences_for_strategy(strategy_id=strategy_id, limit=500)
        durations = [
            float(getattr(r, "holding_duration_seconds", 0.0) or 0.0)
            for r in records
            if float(getattr(r, "holding_duration_seconds", 0.0) or 0.0) > 0.0
        ]
    except Exception:
        durations = []
    if len(durations) < EXCESSIVE_HOLD_MIN_SAMPLE:
        return {"median": None, "mad": None, "mean": None}
    median = float(statistics.median(durations))
    mad = float(statistics.median([abs(d - median) for d in durations]))
    return {
        "median": median,
        "mad": mad,
        "mean": float(statistics.fmean(durations)),
    }


def _trade_data_anomalies(trade: Any, ticket: str, anomaly_version: str) -> list[AnomalyEvent]:
    """Objective data-inconsistency anomalies for one canonical trade."""
    out: list[AnomalyEvent] = []

    # STRATEGY_CONTEXT_LOSS — closed trade without strategy attribution.
    if not (trade.strategy_id or trade.entry_reason):
        out.append(
            AnomalyEvent(
                anomaly_id=f"ano_{uuid.uuid4().hex[:12]}",
                ticket=ticket,
                anomaly_type="STRATEGY_CONTEXT_LOSS",
                category="DATA",
                severity="MEDIUM",
                confidence=0.7,
                evidence={
                    "explanation": "closed trade carries no strategy attribution",
                    "threshold": {"strategy_id": "present"},
                    "actual": {"strategy_id": ""},
                    "expected": {"strategy_id": "present"},
                    "algorithm_version": anomaly_version,
                },
            )
        )

    # EXIT_CLASSIFICATION_ANOMALY — risk-free claim without SL modification.
    mech = (trade.exit_mechanism_raw or "").upper()
    if mech in ("RISK_FREE_SL_HIT", "BREAK_EVEN_SL_HIT") and not trade.was_sl_modified:
        out.append(
            AnomalyEvent(
                anomaly_id=f"ano_{uuid.uuid4().hex[:12]}",
                ticket=ticket,
                anomaly_type="EXIT_CLASSIFICATION_ANOMALY",
                category="EXECUTION",
                severity="MEDIUM",
                confidence=0.9,
                evidence={
                    "explanation": "exit recorded as risk-free/breakeven while "
                    "was_sl_modified=false",
                    "threshold": {"sl_modified": True},
                    "actual": {"sl_modified": False, "exit_mechanism": mech},
                    "expected": {"sl_modified": True},
                    "algorithm_version": anomaly_version,
                },
            )
        )

    # IMPOSSIBLE_EXCURSION — MAE/MFE signs contradict the direction.
    direction = (trade.direction or "").upper()
    mae = float(getattr(trade, "mae_points", 0.0) or 0.0)
    mfe = float(getattr(trade, "mfe_points", 0.0) or 0.0)
    if direction == "BUY" and mae > 0.0:
        out.append(
            AnomalyEvent(
                anomaly_id=f"ano_{uuid.uuid4().hex[:12]}",
                ticket=ticket,
                anomaly_type="IMPOSSIBLE_EXCURSION",
                category="DATA",
                severity="LOW",
                confidence=0.8,
                evidence={
                    "explanation": "BUY trade records positive MAE (adverse excursion "
                    "must be <= 0)",
                    "actual": {"mae_points": mae},
                    "expected": {"mae_points": "<= 0"},
                    "algorithm_version": anomaly_version,
                },
            )
        )
    if direction == "SELL" and mfe < 0.0:
        out.append(
            AnomalyEvent(
                anomaly_id=f"ano_{uuid.uuid4().hex[:12]}",
                ticket=ticket,
                anomaly_type="IMPOSSIBLE_EXCURSION",
                category="DATA",
                severity="LOW",
                confidence=0.8,
                evidence={
                    "explanation": "SELL trade records negative MFE (favourable "
                    "excursion must be >= 0)",
                    "actual": {"mfe_points": mfe},
                    "expected": {"mfe_points": ">= 0"},
                    "algorithm_version": anomaly_version,
                },
            )
        )

    # IMPOSSIBLE_TIMESTAMP — closed before opened.
    if (
        trade.opened_at is not None
        and trade.closed_at is not None
        and trade.closed_at < trade.opened_at
    ):
        out.append(
            AnomalyEvent(
                anomaly_id=f"ano_{uuid.uuid4().hex[:12]}",
                ticket=ticket,
                anomaly_type="IMPOSSIBLE_TIMESTAMP",
                category="DATA",
                severity="LOW",
                confidence=0.9,
                evidence={
                    "explanation": "close timestamp precedes open timestamp",
                    "actual": {"closed_at": trade.closed_at.isoformat()},
                    "expected": {"closed_at": ">= opened_at"},
                    "algorithm_version": anomaly_version,
                },
            )
        )
    return out


def _duplicate_outcome_anomalies(
    audit_repo: AuditRepository, anomaly_version: str
) -> list[AnomalyEvent]:
    """Batch-level DATA anomaly: two closed outcomes for one execution_id.

    Idempotent: the anomaly_id is deterministic for (execution_id, type,
    version), and executions already flagged under this version are skipped.
    """
    import sqlite3

    out: list[AnomalyEvent] = []
    try:
        conn = sqlite3.connect(audit_repo._db_path, timeout=5.0)
        try:
            rows = conn.execute(
                "SELECT execution_id, COUNT(*) c, "
                "MIN(realized_pnl_usd) min_pnl, MAX(realized_pnl_usd) max_pnl "
                "FROM audit_experience_outcomes WHERE is_closed = 1 "
                "GROUP BY execution_id HAVING c > 1"
            ).fetchall()
        finally:
            conn.close()
    except Exception as e:
        logger.error("[BEHAVIOR] duplicate-outcome scan failed (isolated)", error=str(e))
        return out

    # Skip executions already flagged under THIS anomaly version (idempotency).
    try:
        conn = sqlite3.connect(audit_repo._db_path, timeout=5.0)
        try:
            existing = {
                str(r[0])
                for r in conn.execute(
                    "SELECT anomaly_id FROM anomaly_events WHERE algorithm_version = ?",
                    (anomaly_version,),
                ).fetchall()
            }
        finally:
            conn.close()
    except Exception:
        existing = set()

    for execution_id, count, min_pnl, max_pnl in rows:
        delta = abs(float(max_pnl or 0.0) - float(min_pnl or 0.0))
        if delta > 1e-9:
            anomaly_id = _duplicate_anomaly_id(
                str(execution_id), "DUPLICATE_ECONOMIC_OUTCOME", anomaly_version
            )
            if anomaly_id in existing:
                continue
            out.append(
                AnomalyEvent(
                    anomaly_id=anomaly_id,
                    ticket=str(execution_id),
                    anomaly_type="DUPLICATE_ECONOMIC_OUTCOME",
                    category="DATA",
                    severity="CRITICAL",
                    confidence=0.95,
                    evidence={
                        "explanation": "multiple closed ledger outcomes exist for one "
                        "economic trade with different realized PnL",
                        "threshold": {"outcomes_per_execution": 1},
                        "actual": {"outcome_count": int(count), "pnl_delta": round(delta, 2)},
                        "expected": {"outcome_count": 1},
                        "algorithm_version": anomaly_version,
                    },
                )
            )
    return out


def _duplicate_anomaly_id(ticket: str, anomaly_type: str, version: str) -> str:
    """Deterministic anomaly id: (ticket, type, version) -> stable key."""
    raw = f"{ticket}|{anomaly_type}|{version}"
    return f"ano_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def _persist_analysis(audit_repo: AuditRepository, analysis: BehaviorAnalysis) -> bool:
    """Idempotent analysis-record persistence (ON CONFLICT DO NOTHING)."""
    if not audit_repo._is_sqlite:
        return False
    key = _build_analysis_key(analysis.ticket, analysis.behavior_version, analysis.anomaly_version)
    args = (
        key,
        analysis.ticket,
        analysis.symbol,
        analysis.strategy_id,
        analysis.behavior_version,
        analysis.anomaly_version,
        analysis.analyzed_at.isoformat(),
        analysis.evidence_coverage,
        analysis.complete_context,
        analysis.partial_context,
        json.dumps(analysis.flags, default=_json_default),
        json.dumps(analysis.anomalies, default=_json_default),
    )
    try:
        audit_repo._queue.put_nowait((INSERT_ANALYSIS_SQL, args))
        return True
    except Exception as e:
        logger.error("[BEHAVIOR] analysis persist failed (isolated)", error=str(e))
        return False


def _persist_anomaly(audit_repo: AuditRepository, anomaly: AnomalyEvent, version: str) -> bool:
    """Idempotent anomaly-event persistence."""
    if not audit_repo._is_sqlite:
        return False
    args = (
        anomaly.anomaly_id,
        anomaly.ticket,
        anomaly.anomaly_type,
        anomaly.category,
        anomaly.severity,
        anomaly.confidence,
        json.dumps(anomaly.evidence, default=_json_default),
        anomaly.detected_at.isoformat(),
        version,
    )
    try:
        audit_repo._queue.put_nowait((INSERT_ANOMALY_SQL, args))
        return True
    except Exception as e:
        logger.error("[BEHAVIOR] anomaly persist failed (isolated)", error=str(e))
        return False


class BehaviorAnalysisBackfiller:
    """
    Bounded historical behavioral-analysis backfill driver.

    Runs offline (never on the tick path) and is fully idempotent: tickets
    already analyzed under the same (behavior_version, anomaly_version) are
    skipped. `max_trades_per_run` bounds one pass so the engine never scans
    unbounded history in a single tick.
    """

    def __init__(
        self,
        audit_repo: AuditRepository,
        max_trades_per_run: int = 200,
        behavior_version: str = "behavior-v1",
        anomaly_version: str = "anomaly-v1",
    ) -> None:
        self.audit_repo = audit_repo
        self.max_trades_per_run = max(1, int(max_trades_per_run))
        self.behavior_version = behavior_version
        self.anomaly_version = anomaly_version
        self.engine = BehaviorDetectionEngine(audit_repo=audit_repo)

    def run(
        self, behavior_version: str = "behavior-v1", anomaly_version: str = "anomaly-v1"
    ) -> dict[str, Any]:
        """Runs one bounded pass; returns the summary dict."""
        return analyze_canonical_trades(
            audit_repo=self.audit_repo,
            engine=self.engine,
            behavior_version=behavior_version,
            anomaly_version=anomaly_version,
            max_trades=self.max_trades_per_run,
        )
