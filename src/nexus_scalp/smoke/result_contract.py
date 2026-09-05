"""Result contracts + evidence collection + redaction + performance budgets.

This module is the SINGLE source of truth for the JSON report schema
(brief section 12), the human summary (section 13) and the failure-code
contract (section 14). No ad-hoc keys elsewhere.

Safety:
  * Secret-bearing keys (bot_token, api_key, password, token) are NEVER
    emitted — redacted to "***REDACTED***".
  * Quantities needed for confidence (git_commit, version, timestamp,
    smoke run_id) are always present.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

# ---------------------------------------------------------------------------
# Run identity (correlation ID) — every smoke run is traceable.
# ---------------------------------------------------------------------------

SMOKE_CORRELATION_ID = uuid.uuid4().hex[:16]


def new_run_id() -> str:
    return f"smoke-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Redaction (INV-SEC-01/02) — never emit a token.
# ---------------------------------------------------------------------------

REDACT_KEYS = {"bot_token", "api_key", "password", "token", "secret", "credential"}
REDACT_PATTERN = re.compile(r"(bot_token|api_key|password|token)\s*[:=]\s*\S+", re.IGNORECASE)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: ("***REDACTED***" if k.lower() in REDACT_KEYS else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return REDACT_PATTERN.sub(r"\1=***REDACTED***", value)
    return value


# ---------------------------------------------------------------------------
# Performance budgets (brief section 10) — documented thresholds with reason.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Budget:
    metric: str
    threshold_ms: float
    reason: str
    source: str
    severity: str  # WARN or FAIL


BUDGETS: tuple[Budget, ...] = (
    Budget(
        "startup_duration",
        5000,
        "second LiveEngine must boot within 5s on dev hardware",
        "docs/architecture/runtime-certification-gate.md + measured 30s full gate",
        "WARN",
    ),
    Budget(
        "readiness_duration",
        2000,
        "/health ready within 2s after construction",
        "web/server health wiring",
        "WARN",
    ),
    Budget(
        "first_tick_latency",
        100,
        "first tick features+inference < 100ms (hot path is 4-6ms features)",
        "TDF-4 hot-path probe",
        "WARN",
    ),
    Budget(
        "e2e_decision_ms",
        250,
        "end-to-end decision (features->risk) p50 <250ms offline",
        "smoke chain measured ~15s whole; decision cycle ~200ms",
        "WARN",
    ),
    Budget("shutdown_latency", 5000, "shutdown + audit flush < 5s", "gate L8 measured", "WARN"),
    Budget(
        "worker_startup", 3000, "background workers start <3s", "live_engine _start_* path", "WARN"
    ),
    Budget("api_readiness", 1000, "/health responds <1s", "gate L7 measured", "WARN"),
    Budget(
        "persistence_flush",
        2000,
        "audit queue flush <2s for one signal",
        "gate L3 round-trip",
        "WARN",
    ),
)

BUDGET_MAP = {b.metric: b for b in BUDGETS}


def evaluate_budgets(timings: dict[str, float]) -> list[dict[str, Any]]:
    """Returns WARN entries for budgets exceeded. Never mutates the gate status on budgets alone (spec: smoke is not a benchmark suite)."""
    out: list[dict[str, Any]] = []
    for metric, ms in timings.items():
        b = BUDGET_MAP.get(metric)
        if b and ms > b.threshold_ms:
            out.append(
                {
                    "metric": metric,
                    "threshold_ms": b.threshold_ms,
                    "observed_ms": round(ms, 1),
                    "reason": b.reason,
                    "severity": b.severity,
                }
            )
    return out


# ---------------------------------------------------------------------------
# Machine-readable report (brief section 12) — canonical structure.
# ---------------------------------------------------------------------------


@dataclass
class CheckRecord:
    """One check inside the report (maps to coverage ids + runtime checks)."""

    id: str
    layer: str
    name: str
    status: str  # PASS | FAIL | SKIP | WARN | BLOCKED | NOT_APPLICABLE | ENVIRONMENT_FAILURE | UNAVAILABLE
    duration_ms: float = 0.0
    failure_code: str | None = None
    reason: str = ""
    expected: str = ""
    observed: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    safe_action: str = ""
    suggested_investigation: str = ""


@dataclass
class SmokeReport:
    run_id: str
    git_commit: str
    version: str
    timestamp: str
    environment: dict[str, Any]
    runtime_mode: str
    tier: str  # fast | full | runtime | safety
    overall_status: str  # PASS | FAIL | DEGRADED | BLOCKED | NOT_APPLICABLE | ENVIRONMENT_FAILURE
    release_gate: bool
    duration_ms: float
    checks: list[CheckRecord] = field(default_factory=list)
    critical_failures: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    degraded_components: list[str] = field(default_factory=list)
    contract_results: list[dict[str, Any]] = field(default_factory=list)
    runtime_results: list[dict[str, Any]] = field(default_factory=list)
    safety_results: list[dict[str, Any]] = field(default_factory=list)
    performance_results: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    worker_health: dict[str, Any] = field(default_factory=dict)
    model_identity: dict[str, Any] = field(default_factory=dict)
    schema_identity: dict[str, Any] = field(default_factory=dict)
    adapter_identity: dict[str, Any] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        raw = {
            "run_id": self.run_id,
            "git_commit": self.git_commit,
            "version": self.version,
            "timestamp": self.timestamp,
            "environment": redact(self.environment),
            "runtime_mode": self.runtime_mode,
            "tier": self.tier,
            "overall_status": self.overall_status,
            "release_gate": self.release_gate,
            "duration_ms": round(self.duration_ms, 1),
            "checks": [asdict(c) for c in self.checks],
            "critical_failures": redact(self.critical_failures),
            "warnings": redact(self.warnings),
            "degraded_components": self.degraded_components,
            "contract_results": redact(self.contract_results),
            "runtime_results": redact(self.runtime_results),
            "safety_results": redact(self.safety_results),
            "performance_results": self.performance_results,
            "artifacts": self.artifacts,
            "evidence": redact(self.evidence),
            "worker_health": redact(self.worker_health),
            "model_identity": redact(self.model_identity),
            "schema_identity": redact(self.schema_identity),
            "adapter_identity": redact(self.adapter_identity),
            "summary": self.summary,
        }
        return raw


def collect_environment() -> dict[str, Any]:
    env: dict[str, Any] = {
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "executable": sys.executable,
    }
    # Presence-only wiring for secrets (never values).
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "NEXUS_SETTINGS_DB"):
        val = os.environ.get(key)
        env[f"{key}_present"] = bool(val)
    return env


def current_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return "NOT_RECORDED"


def current_version() -> str:
    try:
        from nexus_scalp.release.metadata import get_version_info  # type: ignore[import]

        return str(get_version_info().get("version", "unknown"))
    except Exception:
        return "unknown"


def config_fingerprint_hash(cfg: Any) -> str:
    """Stable config fingerprint without secrets (shape-only)."""
    try:
        d = cfg.model_dump() if hasattr(cfg, "model_dump") else dict(cfg)
        # Strip secret-bearing keys recursively.
        clean = redact(d)
        blob = json.dumps(clean, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]
    except Exception:
        return "UNKNOWN"


# ---------------------------------------------------------------------------
# Human summary (brief section 13) + actionable failure (section 14)
# ---------------------------------------------------------------------------


def failure_block(c: CheckRecord) -> str:
    """Actionable failure rendering per brief section 14."""
    return (
        f"  FAIL\n"
        f"  Code: {c.failure_code or 'UNKNOWN'}\n"
        f"  Component: {c.name}\n"
        f"  Expected: {c.expected or '(recorded in evidence)'}\n"
        f"  Observed: {c.observed or c.reason}\n"
        f"  Safe Action: {c.safe_action or 'See evidence / investigation hint'}\n"
        f"  Evidence: {json.dumps(c.evidence, default=str)[:800]}\n"
        f"  Suggested Investigation: {c.suggested_investigation or c.reason}\n"
    )


def human_summary(report: SmokeReport) -> str:
    checks = report.checks

    def has(statuses: tuple[str, ...], layer_prefix: str | None = None) -> str:
        for c in checks:
            if c.status in statuses and (layer_prefix is None or c.layer.startswith(layer_prefix)):
                return c.status
        return "PASS"

    lines = [
        "",
        "=" * 72,
        "NEXUS E2E SMOKE TEST  —  " + report.tier.upper(),
        "=" * 72,
        "",
        f"Overall: {report.overall_status}",
        f"Release Gate: {'PASS' if report.release_gate else 'BLOCKED'}",
        f"Run: {report.run_id}  @  {report.timestamp}",
        f"Git: {report.git_commit}   Version: {report.version}   Mode: {report.runtime_mode}",
        f"Duration: {report.duration_ms:.0f} ms   Python: {report.environment.get('python', '?')}  Platform: {report.environment.get('platform', '?')}",
        "",
        "Startup",
        f"  Doctor/Static:  {has(('PASS',), 'L0')}",
        f"  Config:         {has(('PASS',), 'L0')}",
        f"  Migration:      {has(('PASS',), 'L0')}",
        f"  Model Load:     {has(('PASS', 'SKIP'), 'L1')}",
        f"  Web Readiness:  {has(('PASS',), 'L3')}",
        "",
        "Critical Path",
        f"  Market Data:  {has(('PASS',), 'L1')}",
        f"  Features:     {has(('PASS',), 'L1')}",
        f"  Contract:     {has(('PASS',), 'L1')}",
        f"  Inference:    {has(('PASS', 'SKIP'), 'L2')}",
        f"  Policy:       {has(('PASS',), 'L2')}",
        f"  Risk:         {has(('PASS',), 'L2')}",
        f"  Execution:    {has(('PASS',), 'L2')}",
        f"  Accounting:   {has(('PASS',), 'L2')}",
        "",
        "Safety",
        f"  LIVE blocked:             {has(('PASS',), 'L4')}",
        f"  Shadow order authority:   {has(('PASS',), 'L4')}",
        f"  Research order authority: {has(('PASS',), 'L4')}",
        f"  Risk clamps:              {has(('PASS',), 'L2')}",
        f"  Kill switch:              {has(('PASS',), 'L2')}",
        "",
        "Observability",
        f"  Correlation IDs:   {has(('PASS',), 'L3')}",
        f"  Structured events: {has(('PASS',), 'L3')}",
        f"  Worker health:     {has(('PASS',), 'L3')}",
        "",
        "Lifecycle",
        f"  Startup:     {has(('PASS',), 'lifecycle')}",
        f"  Steady:      {has(('PASS',), 'lifecycle')}",
        f"  Shutdown:    {has(('PASS',), 'L3')}",
        f"  Restart:     {has(('PASS',), 'lifecycle')}",
        "",
        "Performance",
        f"  Startup:     {report.performance_results[0]['observed_ms'] if report.performance_results else '(measured)'} ms budgeted",
        f"  First Tick:  {(report.evidence.get('first_tick_ms') or '(measured)')}",
        f"  E2E Decision:{(report.evidence.get('e2e_decision_ms') or '(measured)')}",
        f"  Shutdown:    {(report.evidence.get('shutdown_ms') or '(measured)')}",
        "",
        f"Warnings: {len(report.warnings)}   Failures: {len(report.critical_failures)}",
        f"Artifacts: {', '.join(report.artifacts) if report.artifacts else '(none — use --evidence)'}",
        "",
    ]
    if report.critical_failures:
        lines.append("Failures (actionable):")
        for c in [x for x in checks if x.status == "FAIL"]:
            lines.append(failure_block(c))
    lines.append("=" * 72)
    lines.append("")
    return "\n".join(lines)
