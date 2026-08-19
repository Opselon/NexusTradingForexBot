"""Forensic health check results — shared payload contract (TASK-11).

Every check in the ForensicHealthEngine produces a CheckResult with the
five-level status vocabulary and the full evidence envelope required by the
TASK-11 spec §4:

    check_id | timestamp | status | duration_ms | evidence | observed |
    expected | correlation_id

Status is NEVER only PASS/FAIL: PASS / WARNING / DEGRADED / CRITICAL /
UNKNOWN are all first-class. UNKNOWN is returned when the checker cannot
determine health (database unavailable, telemetry failed, feature disabled)
— it is never converted to PASS and never converted to zero/"no errors".
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class HealthStatus(StrEnum):
    """Five-level status vocabulary (TASK-11 §3/§4)."""

    PASS = "PASS"
    WARNING = "WARNING"
    DEGRADED = "DEGRADED"
    CRITICAL = "CRITICAL"
    UNKNOWN = "UNKNOWN"

    @property
    def severity(self) -> int:
        return {
            HealthStatus.PASS: 0,
            HealthStatus.WARNING: 1,
            HealthStatus.DEGRADED: 2,
            HealthStatus.CRITICAL: 3,
            HealthStatus.UNKNOWN: 2,  # unknown is never ignorable (§5)
        }[self]

    def is_healthy(self) -> bool:
        return self is HealthStatus.PASS

    def is_blocking(self) -> bool:
        """CRITICAL blocks unsafe deployment; UNKNOWN/DEGRADED never silently."""
        return self is HealthStatus.CRITICAL


class ForensicCheckError(RuntimeError):
    """A check that RAISES is surfaced as UNKNOWN/CRITICAL evidence, never a PASS."""


def new_correlation_id(prefix: str = "fh") -> str:
    """Short deterministic-ish correlation id for a check run."""
    raw = f"{prefix}:{time.time_ns()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class CheckResult:
    """Immutable result envelope for one forensic check (TASK-11 §4)."""

    check_id: str
    status: HealthStatus
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    duration_ms: float = 0.0
    evidence: str = ""
    observed: dict[str, Any] = field(default_factory=dict)
    expected: str = ""
    correlation_id: str = field(default_factory=new_correlation_id)
    detail: str = ""  # human-oriented detail line (goes to the dashboard)

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "status": self.status.value,
            "timestamp": self.timestamp.isoformat(),
            "duration_ms": round(self.duration_ms, 3),
            "evidence": self.evidence,
            "observed": self.observed,
            "expected": self.expected,
            "correlation_id": self.correlation_id,
            "detail": self.detail,
        }


def worst_status(results: list[CheckResult]) -> HealthStatus:
    """Aggregate status that NEVER averages criticals away (TASK-11 §50)."""
    if not results:
        return HealthStatus.UNKNOWN
    return max((r.status for r in results), key=lambda s: s.severity)


def severity_label(status: HealthStatus) -> str:
    return {
        HealthStatus.PASS: "✅ PASS",
        HealthStatus.WARNING: "⚠️ WARNING",
        HealthStatus.DEGRADED: "🟠 DEGRADED",
        HealthStatus.CRITICAL: "🚨 CRITICAL",
        HealthStatus.UNKNOWN: "❓ UNKNOWN",
    }[status]
