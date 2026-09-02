"""Canonical Deploy Gate (TASK-12 §5-§11, §39).

One health engine + one canonical gate contract. The gate interprets the
FORENSIC_HEALTH_SNAPSHOT statuses with the repository governance policy:

    PASS       -> ALLOW
    WARNING    -> ALLOW_WITH_WARNING
    DEGRADED   -> REVIEW_REQUIRED        (policy-dependent, §6)
    CRITICAL   -> BLOCK
    UNKNOWN    -> REVIEW_REQUIRED        (NEVER silently PASS, §7)

FAIL-SAFE (§39): if the health engine itself fails, the gate returns
FORENSIC_ENGINE_UNAVAILABLE and BLOCKs (never silently passes).

Every decision carries the §8 evidence envelope and is persisted to
artifacts/forensics/deploy_gate_result.json.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.forensics.engine import ForensicHealthEngine
from nexus_scalp.forensics.models import HealthStatus, new_correlation_id
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.forensics.deploy_gate")

#: Governance deployment policy (TASK-12 §6). Degraded/UNKNOWN -> review
#: (operator decides); CRITICAL -> hard block; engine failure -> block.
DEPLOY_POLICY: dict[str, str] = {
    HealthStatus.PASS.value: "ALLOW",
    HealthStatus.WARNING.value: "ALLOW_WITH_WARNING",
    HealthStatus.DEGRADED.value: "REVIEW_REQUIRED",
    HealthStatus.CRITICAL.value: "BLOCK",
    HealthStatus.UNKNOWN.value: "REVIEW_REQUIRED",
}

#: Exit codes for the CLI gate contract (§10):
#: 0 = allowed, 1 = blocked, 2 = review required, 3 = engine unavailable.
EXIT_ALLOW = 0
EXIT_BLOCK = 1
EXIT_REVIEW = 2
EXIT_ENGINE_UNAVAILABLE = 3

#: Checks that are mandatory regardless of policy (a CRITICAL here hard-blocks).
MANDATORY_CRITICAL_PREFIXES: tuple[str, ...] = (
    "CHECK-FCS-",  # feature schema contract
    "CHECK-MDL-",  # model/scaler contract
    "CHECK-INT-",  # database integrity
    "CHECK-MIG-",  # migration drift
    "CHECK-ACC-",  # accounting/outcome integrity
    "CHECK-RTP-",  # parity/causality
    "CHECK-GOV-",  # governance/champion
)


def current_git_commit() -> str:
    """Best-effort current commit SHA ('' when not a git repo / unavailable)."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5.0,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return ""


@dataclass
class DeployGateResult:
    """Machine-readable gate decision (§8)."""

    decision: str
    overall_status: str
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    correlation_id: str = field(default_factory=new_correlation_id)
    commit_sha: str = ""
    check_count: int = 0
    critical_count: int = 0
    warning_count: int = 0
    degraded_count: int = 0
    unknown_count: int = 0
    blocking_checks: list[str] = field(default_factory=list)
    health_snapshot_id: str = ""
    engine_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "overall_status": self.overall_status,
            "timestamp": self.timestamp,
            "correlation_id": self.correlation_id,
            "commit_sha": self.commit_sha,
            "check_count": self.check_count,
            "critical_count": self.critical_count,
            "warning_count": self.warning_count,
            "degraded_count": self.degraded_count,
            "unknown_count": self.unknown_count,
            "blocking_checks": self.blocking_checks,
            "health_snapshot_id": self.health_snapshot_id,
            "engine_error": self.engine_error,
        }

    @property
    def exit_code(self) -> int:
        if self.decision == "ALLOW":
            return EXIT_ALLOW
        if self.decision == "ALLOW_WITH_WARNING":
            return EXIT_ALLOW
        if self.decision == "BLOCK":
            return EXIT_BLOCK
        if self.decision == "FORENSIC_ENGINE_UNAVAILABLE":
            return EXIT_ENGINE_UNAVAILABLE
        return EXIT_REVIEW  # REVIEW_REQUIRED


def run_deploy_gate(
    engine: ForensicHealthEngine | None = None,
    *,
    persist: bool = True,
    result_dir: Path | None = None,
    commit_sha: str = "",
) -> DeployGateResult:
    """Runs the canonical deploy gate over the health engine snapshot.

    Never raises: an engine failure produces FORENSIC_ENGINE_UNAVAILABLE
    (block, §39) instead of crashing the pipeline.
    """
    result_dir = result_dir or Path("artifacts") / "forensics"
    engine = engine or ForensicHealthEngine(history_dir=result_dir)
    try:
        rec = engine.snapshot(persist=persist)
    except Exception as exc:
        logger.error("[DEPLOY_GATE] engine failure -> FORENSIC_ENGINE_UNAVAILABLE", error=str(exc))
        result = DeployGateResult(
            decision="FORENSIC_ENGINE_UNAVAILABLE",
            overall_status="UNKNOWN",
            commit_sha=commit_sha or current_git_commit(),
            engine_error=str(exc),
        )
        if persist:
            _persist_result(result, result_dir)
        return result

    blockers: list[str] = [
        c["check_id"]
        for c in rec.checks
        if c["status"] == HealthStatus.CRITICAL.value
        and c["check_id"].startswith(MANDATORY_CRITICAL_PREFIXES)
    ]
    # Any CRITICAL blocks regardless of prefix if the policy is not
    # explicitly overridden — safety first (§6: CRITICAL = BLOCK).
    all_critical = [c["check_id"] for c in rec.checks if c["status"] == HealthStatus.CRITICAL.value]
    if all_critical:
        blockers = sorted(set(blockers + all_critical))

    if blockers:
        decision = "BLOCK"
    elif rec.overall == HealthStatus.CRITICAL.value:
        decision = "BLOCK"
    elif rec.overall in (HealthStatus.DEGRADED.value, HealthStatus.UNKNOWN.value):
        decision = "REVIEW_REQUIRED"
    elif rec.overall == HealthStatus.WARNING.value:
        decision = "ALLOW_WITH_WARNING"
    else:
        decision = "ALLOW"

    result = DeployGateResult(
        decision=decision,
        overall_status=rec.overall,
        commit_sha=commit_sha or current_git_commit(),
        check_count=len(rec.checks),
        critical_count=rec.critical_count,
        warning_count=rec.warning_count,
        degraded_count=rec.degraded_count,
        unknown_count=rec.unknown_count,
        blocking_checks=blockers,
        health_snapshot_id=rec.correlation_id,
    )
    if persist:
        _persist_result(result, result_dir)
    return result


def _persist_result(result: DeployGateResult, result_dir: Path) -> None:
    try:
        result_dir.mkdir(parents=True, exist_ok=True)
        path = result_dir / "deploy_gate_result.json"
        path.write_text(json.dumps(result.to_dict(), indent=2, default=str), encoding="utf-8")
    except OSError as exc:
        logger.warning("[DEPLOY_GATE] result persistence failed", error=str(exc))


def load_last_gate_result(result_dir: Path | None = None) -> dict[str, Any] | None:
    """Reads the last persisted gate result (dashboard/UI consumption)."""
    result_dir = result_dir or Path("artifacts") / "forensics"
    path = result_dir / "deploy_gate_result.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
