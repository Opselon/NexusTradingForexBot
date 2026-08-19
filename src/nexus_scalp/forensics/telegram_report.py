"""Periodic Telegram forensic report (TASK-12 §30-32).

Bounded summarized report of the FORENSIC_HEALTH_SNAPSHOT — never every
individual check. Config-driven (enabled/interval/minimum_severity/
aggregation_window), deduplicated + cooldown-gated so identical recurring
conditions never spam the operator.

The report builder is pure (snapshot -> text); delivery goes through the
existing settings-service notifier (INV-010 — telegram is a read-only
consumer of canonical state).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.forensics.engine import ForensicHealthEngine
from nexus_scalp.forensics.models import HealthStatus
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.forensics.telegram_report")

#: Defaults (overridable through config).
DEFAULT_ENABLED: bool = False
DEFAULT_INTERVAL_SEC: int = 6 * 3600  # every 6h
DEFAULT_MIN_SEVERITY: str = "WARNING"  # only report at/above this
DEFAULT_AGGREGATION_WINDOW_SEC: int = 3600  # cooldown window

#: Cooldown per (check_id, status) so identical alerts aggregate, not spam.
COOLDOWN_PER_CHECK_SEC: int = DEFAULT_AGGREGATION_WINDOW_SEC


@dataclass
class ForensicReportConfig:
    enabled: bool = DEFAULT_ENABLED
    interval_sec: int = DEFAULT_INTERVAL_SEC
    minimum_severity: str = DEFAULT_MIN_SEVERITY
    aggregation_window_sec: int = DEFAULT_AGGREGATION_WINDOW_SEC

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "interval_sec": self.interval_sec,
            "minimum_severity": self.minimum_severity,
            "aggregation_window_sec": self.aggregation_window_sec,
        }


def load_report_config(config_path: Path | None = None) -> ForensicReportConfig:
    """Loads the report config from the repo config file if present.

    Never raises: defaults on missing/unparseable config (the report is a
    safety net — a config error must not crash the engine).
    """
    path = config_path or Path("configs") / "base.yaml"
    try:
        if not path.exists():
            return ForensicReportConfig()
        import yaml

        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        section = raw.get("forensic_report", {}) or {}
        return ForensicReportConfig(
            enabled=bool(section.get("enabled", DEFAULT_ENABLED)),
            interval_sec=int(section.get("interval_sec", DEFAULT_INTERVAL_SEC)),
            minimum_severity=str(section.get("minimum_severity", DEFAULT_MIN_SEVERITY)).upper(),
            aggregation_window_sec=int(
                section.get("aggregation_window_sec", DEFAULT_AGGREGATION_WINDOW_SEC)
            ),
        )
    except Exception:
        return ForensicReportConfig()


def build_report_text(rec: Any, *, mode: str = "", symbol: str = "") -> str:
    """Bounded summarized report text (TASK-11 §53 format, TASK-12 §30).

    Only aggregate counts + top-level group statuses + top incidents —
    never every check row.
    """
    severity_rank = {"PASS": 0, "WARNING": 1, "DEGRADED": 2, "CRITICAL": 3, "UNKNOWN": 2}
    groups = rec.groups or {}
    lines = [
        "NSE FORENSIC HEALTH",
        f"Overall: {rec.overall}",
        f"Critical: {rec.critical_count} | Warning: {rec.warning_count} | "
        f"Degraded: {rec.degraded_count} | Unknown: {rec.unknown_count}",
    ]
    if mode:
        lines.append(f"Mode: {mode}")
    if symbol:
        lines.append(f"Symbol: {symbol}")
    for group in (
        "FeatureContract",
        "Model",
        "Parity",
        "Dataset",
        "Accounting",
        "Database",
        "Liquidity",
        "News",
        "Shadow",
        "Governance",
        "UI",
        "API",
        "Telegram",
        "Workers",
        "Runtime",
        "Performance",
    ):
        if group in groups:
            lines.append(f"{group}: {groups[group]}")
    # top active incidents (bounded to 5, CRITICAL/DEGRADED only)
    incidents = [
        c
        for c in rec.checks
        if c["status"] in (HealthStatus.CRITICAL.value, HealthStatus.DEGRADED.value)
    ]
    incidents.sort(key=lambda c: severity_rank.get(c["status"], 0), reverse=True)
    for c in incidents[:5]:
        lines.append(f"• {c['check_id']} [{c['status']}] {c['evidence'][:100]}")
    return "\n".join(lines)


class TelegramReportScheduler:
    """Bounded periodic forensic report scheduler (TASK-12 §31/§35).

    - bounded interval (config-driven)
    - dedup via per-check cooldown fingerprints (check_id + status)
    - never blocks the tick path (invoked from a background task/worker)
    """

    def __init__(
        self,
        config: ForensicReportConfig | None = None,
        *,
        history_dir: Path | None = None,
        state_path: Path | None = None,
    ) -> None:
        self.config = config or load_report_config()
        self.history_dir = history_dir or Path("artifacts") / "forensics"
        self.state_path = state_path or self.history_dir / "telegram_report_state.json"
        self._last_sent_at: float = 0.0
        self._cooldowns: dict[str, float] = {}
        self._load_state()

    def _load_state(self) -> None:
        try:
            if self.state_path.exists():
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                self._last_sent_at = float(data.get("last_sent_at", 0.0))
                self._cooldowns = {k: float(v) for k, v in data.get("cooldowns", {}).items()}
        except (OSError, ValueError, TypeError):
            pass

    def _save_state(self) -> None:
        try:
            self.history_dir.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(
                json.dumps(
                    {
                        "last_sent_at": self._last_sent_at,
                        "cooldowns": self._cooldowns,
                    },
                    indent=2,
                    default=str,
                ),
                encoding="utf-8",
            )
        except OSError:
            pass

    def should_send(self, now: float | None = None) -> bool:
        """Interval gate: at most one report per configured interval."""
        now = now if now is not None else time.monotonic()
        if not self.config.enabled:
            return False
        return (now - self._last_sent_at) >= self.config.interval_sec

    def _fingerprint(self, check: dict[str, Any]) -> str:
        return f"{check['check_id']}|{check['status']}"

    def dedup(self, rec: Any, now: float | None = None) -> list[dict[str, Any]]:
        """Returns checks that pass the per-check cooldown (dedup §32).

        A check that fired within the aggregation window is skipped.
        """
        now = now if now is not None else time.monotonic()
        fresh: list[dict[str, Any]] = []
        for c in rec.checks:
            if c["status"] not in (
                HealthStatus.WARNING.value,
                HealthStatus.DEGRADED.value,
                HealthStatus.CRITICAL.value,
                HealthStatus.UNKNOWN.value,
            ):
                continue
            fp = self._fingerprint(c)
            last = self._cooldowns.get(fp, 0.0)
            if (now - last) >= self.config.aggregation_window_sec:
                fresh.append(c)
        return fresh

    def mark_sent(self, rec: Any, now: float | None = None) -> None:
        """Records the send + per-check cooldowns (post-delivery)."""
        now = now if now is not None else time.monotonic()
        self._last_sent_at = now
        for c in rec.checks:
            self._cooldowns[self._fingerprint(c)] = now
        self._save_state()

    def run_once(
        self,
        engine: ForensicHealthEngine | None = None,
        *,
        deliver: bool = True,
        mode: str = "",
        symbol: str = "",
    ) -> dict[str, Any]:
        """One bounded report cycle. Returns the report outcome dict.

        deliver=True attempts actual Telegram delivery through the settings
        service notifier; delivery failure is logged, never raised.
        """
        engine = engine or ForensicHealthEngine(history_dir=self.history_dir)
        rec = engine.snapshot(persist=True)
        fresh = self.dedup(rec)
        report_text = build_report_text(rec, mode=mode, symbol=symbol)
        outcome: dict[str, Any] = {
            "sent": False,
            "overall": rec.overall,
            "fresh_checks": [c["check_id"] for c in fresh],
            "timestamp": datetime.now(UTC).isoformat(),
        }
        if not self.config.enabled:
            outcome["reason"] = "report disabled by config"
            return outcome
        if not self.should_send():
            outcome["reason"] = "inside interval window"
            return outcome
        if not fresh:
            outcome["reason"] = "no fresh conditions beyond cooldown"
            # still mark interval so we don't rescan every tick
            self._last_sent_at = time.monotonic()
            self._save_state()
            return outcome
        if deliver:
            try:
                from nexus_scalp.settings import load_settings_service

                svc = load_settings_service()
                notifier = getattr(svc, "notifier", None) or getattr(svc, "_notifier", None)
                if notifier is not None and hasattr(notifier, "send"):
                    ok = notifier.send(report_text)
                    outcome["sent"] = bool(ok)
                    outcome["reason"] = "delivered" if ok else "delivery returned falsy"
                else:
                    outcome["reason"] = "notifier unavailable (configured? enabled?)"
            except Exception as exc:
                logger.warning("[FORENSIC_REPORT] delivery failed", error=str(exc))
                outcome["reason"] = f"delivery failed: {exc!r}"
        else:
            outcome["reason"] = "deliver=False (dry run)"
        self.mark_sent(rec)
        return outcome
