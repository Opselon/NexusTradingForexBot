"""
Behavior Detection Engine
=========================
PHASE 09 measurable behavioral-pattern detection.

Every pattern is derived from RECORDED NUMBERS ONLY. There is no emotional,
psychological or intent-based attribution anywhere in this module - a "greed"
label is never asserted; instead the objectively computable
`GREED_PATTERN` is derived from a high profit giveback percentage. The system
observes what the data provably shows.

Detectable patterns (spec section):
    GREED_PATTERN           large MFE, tiny captured profit -> high giveback
    PANIC_EXIT_PATTERN      closed quickly while continuation evidence was strong
    EARLY_EXIT_PATTERN      exited before the statistical target zone
    LATE_EXIT_PATTERN       ignored degradation signals and held too long
    BAD_RECOVERY_PATTERN    position invalidated yet still held/recovered
    OVERTRADING_PATTERN     repeated low-quality/context entries

Every detection carries: behavior_id, evidence, severity, confidence, timestamp.
Detections are append-only and persisted through the audit queue.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.experience.models import ExperienceRecord
from nexus_scalp.intelligence.models import BehaviorDetection, BehaviorSeverity
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.intelligence.behavior")

INSERT_DETECTION_SQL = """
    INSERT INTO behavior_detections
    (behavior_key, behavior_id, ticket, experience_id, ticket_ctx, pattern,
     severity, confidence, evidence, detected_at, autocorrected)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(behavior_key) DO NOTHING;
"""

#: Giveback fraction of peak profit that constitutes greedy profit surrender.
GREED_GIVEBACK_PCT: float = 0.65
#: Realised R captured relative to MFE that flags an early exit.
EARLY_EXIT_CAPTURE_CEIL: float = 0.35
#: MFE (R) required before an early-exit can be attributed.
EARLY_EXIT_MFE_FLOOR: float = 1.0
#: Hold-duration multiple of expected horizon that signals a late exit.
LATE_EXIT_HOLD_FACTOR: float = 3.0
#: MAE (R) beyond which a position is deemed invalidated while still held.
RECOVERY_MAE_FLOOR: float = 0.9
#: Entries in the re-entry window that constitute overtrading.
OVERTRADE_REENTRY_THRESHOLD: int = 3


class BehaviorDetectionEngine:
    """
    Derives measurable behavioral patterns from a closed position's evidence.

    The engine receives the richest available evidence (the decomposition, the
    realized R, the MFE/MAE/giveback, the hold duration, and any pre-existing
    Phase 08 flags) and emits zero-or-more `BehaviorDetection` objects.
    """

    def __init__(
        self,
        audit_repo: AuditRepository,
        greed_giveback_pct: float = GREED_GIVEBACK_PCT,
        early_exit_capture_ceil: float = EARLY_EXIT_CAPTURE_CEIL,
        early_exit_mfe_floor: float = EARLY_EXIT_MFE_FLOOR,
        late_exit_hold_factor: float = LATE_EXIT_HOLD_FACTOR,
        recovery_mae_floor: float = RECOVERY_MAE_FLOOR,
        overtrade_reentry_threshold: int = OVERTRADE_REENTRY_THRESHOLD,
    ) -> None:
        self.audit_repo = audit_repo
        self.greed_giveback_pct = greed_giveback_pct
        self.early_exit_capture_ceil = early_exit_capture_ceil
        self.early_exit_mfe_floor = early_exit_mfe_floor
        self.late_exit_hold_factor = late_exit_hold_factor
        self.recovery_mae_floor = recovery_mae_floor
        self.overtrade_reentry_threshold = overtrade_reentry_threshold
        self.detection_count: int = 0

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
    ) -> list[BehaviorDetection]:
        """
        Derives every measurable behavior pattern this position evidences.

        Returns a list of `BehaviorDetection` objects (possibly empty). Never
        raises; a failure in detection is isolated and logged.
        """
        flags = set(existing_flags or [])
        detections: list[BehaviorDetection] = []
        ticket_ctx = f"{getattr(record, 'symbol', '')}/{getattr(record, 'timeframe', '')}"
        exp_id = getattr(record, "experience_id", "") if record else ""

        # -- GREED_PATTERN -------------------------------------------------
        if giveback_pct >= self.greed_giveback_pct and mfe_r > 0.0:
            conf = min(1.0, 0.5 + (giveback_pct - self.greed_giveback_pct))
            detections.append(
                self._detection(
                    "GREED_PATTERN",
                    ticket,
                    exp_id,
                    ticket_ctx,
                    BehaviorSeverity.MEDIUM,
                    conf,
                    {
                        "giveback_pct": round(giveback_pct, 3),
                        "mfe_r": round(mfe_r, 3),
                        "realized_r": round(realized_r, 3),
                        "note": "large favourable excursion surrendered before exit",
                    },
                )
            )

        # -- EARLY_EXIT_PATTERN --------------------------------------------
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
                        "mfe_r": round(mfe_r, 3),
                        "realized_r": round(realized_r, 3),
                        "capture_ratio": round(realized_r / mfe_r, 3),
                        "note": "exited before the statistical target zone",
                    },
                )
            )

        # -- LATE_EXIT_PATTERN ---------------------------------------------
        if (
            expected_duration_sec > 0.0
            and holding_duration_sec > expected_duration_sec * self.late_exit_hold_factor
            and realized_r <= 0.0
        ):
            detections.append(
                self._detection(
                    "LATE_EXIT_PATTERN",
                    ticket,
                    exp_id,
                    ticket_ctx,
                    BehaviorSeverity.HIGH,
                    0.8,
                    {
                        "holding_duration_sec": round(holding_duration_sec, 1),
                        "expected_duration_sec": round(expected_duration_sec, 1),
                        "note": "held far beyond expected horizon while edge decayed",
                    },
                )
            )

        # -- BAD_RECOVERY_PATTERN ------------------------------------------
        if mae_r >= self.recovery_mae_floor and realized_r <= 0.0:
            detections.append(
                self._detection(
                    "BAD_RECOVERY_PATTERN",
                    ticket,
                    exp_id,
                    ticket_ctx,
                    BehaviorSeverity.HIGH,
                    0.75,
                    {
                        "mae_r": round(mae_r, 3),
                        "note": "invalidation breached yet position was carried to a losing exit",
                    },
                )
            )

        # -- PANIC_EXIT_PATTERN --------------------------------------------
        # Objective proxy: exited quickly (below expected horizon) despite a
        # healthy favourable excursion (continuation evidence) and positive edge.
        if (
            expected_duration_sec > 0.0
            and holding_duration_sec < expected_duration_sec * 0.5
            and mfe_r >= self.early_exit_mfe_floor
            and realized_r > 0.0
        ):
            detections.append(
                self._detection(
                    "PANIC_EXIT_PATTERN",
                    ticket,
                    exp_id,
                    ticket_ctx,
                    BehaviorSeverity.MEDIUM,
                    0.6,
                    {
                        "holding_duration_sec": round(holding_duration_sec, 1),
                        "expected_duration_sec": round(expected_duration_sec, 1),
                        "mfe_r": round(mfe_r, 3),
                        "note": "closed early while continuation evidence remained strong",
                    },
                )
            )

        # -- OVERTRADING_PATTERN -------------------------------------------
        if recent_context_entries >= self.overtrade_reentry_threshold:
            detections.append(
                self._detection(
                    "OVERTRADING_PATTERN",
                    ticket,
                    exp_id,
                    ticket_ctx,
                    BehaviorSeverity.MEDIUM,
                    0.7,
                    {
                        "recent_context_entries": int(recent_context_entries),
                        "note": "repeated entries in the same low-quality context",
                    },
                )
            )

        # -- WEAK_SETUP / EXECUTION chained from existing flags -------------
        if "WEAK_SETUP_ACCEPTED" in flags:
            detections.append(
                self._detection(
                    "WEAK_SETUP_PATTERN",
                    ticket,
                    exp_id,
                    ticket_ctx,
                    BehaviorSeverity.MEDIUM,
                    0.6,
                    {"flag": "WEAK_SETUP_ACCEPTED"},
                )
            )
        if "EXECUTION_SLIPPAGE_ANOMALY" in flags:
            detections.append(
                self._detection(
                    "EXECUTION_PATTERN",
                    ticket,
                    exp_id,
                    ticket_ctx,
                    BehaviorSeverity.MEDIUM,
                    0.7,
                    {"flag": "EXECUTION_SLIPPAGE_ANOMALY"},
                )
            )

        # Persist all detections.
        for d in detections:
            self.persist(d)
        return detections

    # ------------------------------------------------------------------
    # Helpers
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
            json.dumps(detection.evidence),
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
        raw = f"{ticket}|{pattern}|{json.dumps(evidence, sort_keys=True)}"
        return f"beh_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"
