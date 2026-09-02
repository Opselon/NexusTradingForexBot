"""Incident Telegram alerts (TASK-12 spec 48/49).

CRITICAL / HIGH incidents are notified through the existing Telegram
infrastructure (TelegramNotifier). Message includes: incident_id, severity,
component, symptom, root-cause status, impact, correlation_id. No huge
stack traces.

THROTTLING (spec 49): one root incident must not spam Telegram 500 times.
The notifier keeps a per-incident dedup/cooldown ring: the first occurrence
alerts immediately; repeats within the cooldown window are summarized with a
repeat counter (repeat_count=N).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from nexus_scalp.incidents.models import Incident
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.incidents.telegram")

#: Only these severities are eligible for Telegram (spec 48).
ALERT_SEVERITIES: tuple[str, ...] = ("CRITICAL", "HIGH")

DEFAULT_COOLDOWN_SEC: float = 900.0
DEFAULT_REPEAT_COOLDOWN_SEC: float = 3600.0


@dataclass
class IncidentAlertState:
    """Per-incident throttling state (spec 49)."""

    first_alerted_at: float
    last_alerted_at: float
    repeat_count: int = 0
    repeat_alerts_sent: int = 0


class IncidentTelegramNotifier:
    """Deduplicated, throttled Telegram alerter for CRITICAL/HIGH incidents.

    Uses the canonical TelegramNotifier (never re-implements bot plumbing).
    """

    def __init__(
        self,
        notifier: Any | None = None,
        *,
        cooldown_sec: float = DEFAULT_COOLDOWN_SEC,
        repeat_cooldown_sec: float = DEFAULT_REPEAT_COOLDOWN_SEC,
        enabled: bool = True,
    ) -> None:
        self.notifier = notifier
        self.cooldown_sec = float(cooldown_sec)
        self.repeat_cooldown_sec = float(repeat_cooldown_sec)
        self.enabled = enabled
        #: incident_id -> throttle state (bounded ring)
        self._state: dict[str, IncidentAlertState] = {}
        self.alerts_sent = 0
        self.alerts_suppressed = 0

    # ------------------------------------------------------------------

    def _trim(self) -> None:
        if len(self._state) <= 2000:
            return
        cutoff = time.time() - 86400.0
        self._state = {k: v for k, v in self._state.items() if v.last_alerted_at >= cutoff}

    def should_alert(self, incident: Incident, now: float | None = None) -> bool:
        """Throttle decision: first occurrence alerts; repeats summarized."""
        if not self.enabled:
            return False
        if incident.severity.value not in ALERT_SEVERITIES:
            return False
        now = now or time.time()
        st = self._state.get(incident.incident_id)
        if st is None:
            return True  # first occurrence
        if now - st.last_alerted_at >= self.repeat_cooldown_sec:
            return True  # summary repeat allowed after the repeat cooldown
        return False

    def maybe_alert(self, incident: Incident, now: float | None = None) -> bool:
        """Sends an alert when throttling allows. Returns True if sent."""
        now = now or time.time()
        if not self.should_alert(incident, now):
            self.alerts_suppressed += 1
            return False
        st = self._state.get(incident.incident_id)
        if st is None:
            st = IncidentAlertState(first_alerted_at=now, last_alerted_at=now)
            self._state[incident.incident_id] = st
        else:
            st.repeat_count += 1
            st.last_alerted_at = now
            st.repeat_alerts_sent += 1
        self._trim()
        sent = self._dispatch(incident, repeat=(st.repeat_count > 0))
        if sent:
            self.alerts_sent += 1
        return sent

    # ------------------------------------------------------------------

    def _dispatch(self, incident: Incident, *, repeat: bool) -> bool:
        if self.notifier is None or not getattr(self.notifier, "enabled", False):
            logger.warning(
                "[INCIDENT_TELEGRAM] event=BLOCKED_NOT_CONFIGURED",
                incident_id=incident.incident_id,
            )
            return False
        text = self._format(incident, repeat=repeat)
        try:
            self.notifier.send(
                text,
                severity=incident.severity.value,
                event_type="INCIDENT",
                correlation_id=incident.correlation_id or incident.incident_id,
            )
            return True
        except Exception as err:
            logger.error(
                "[INCIDENT_TELEGRAM] event=SEND_FAILED",
                incident_id=incident.incident_id,
                error=str(err),
            )
            return False

    @staticmethod
    def _format(incident: Incident, *, repeat: bool) -> str:
        d = incident.as_dict()
        head = "🚨 <b>INCIDENT ALERT</b>" if not repeat else "🔁 <b>INCIDENT REPEAT</b>"
        lines = [
            head,
            "━━━━━━━━━━━━━━━━━━━━━",
            f"🆔 <b>Incident:</b> <code>{d['incident_id']}</code>",
            f"⚠️ <b>Severity:</b> {d['severity']}",
            f"🧩 <b>Component:</b> <code>{d['component']}</code>",
            f"📌 <b>Symptom:</b> <code>{d['operation']}</code>",
            f"🔬 <b>Root cause:</b> {d['root_cause_status']}",
            f"📊 <b>Impact:</b> {d['impact']['affected_trades']} trades / "
            f"{d['impact']['affected_records']} records",
        ]
        if d["correlation_id"]:
            lines.append(f"🔗 <b>Correlation ID:</b> <code>{d['correlation_id']}</code>")
        if d["repeated_count"] > 1:
            lines.append(f"🔁 <b>Repeat count:</b> {d['repeated_count']}")
        if d["related_bug_id"]:
            lines.append(f"🐞 <b>BUG:</b> {d['related_bug_id']}")
        # Impact summary (spec 48): NO stack traces, NO raw exceptions.
        return "\n".join(lines)


__all__ = [
    "ALERT_SEVERITIES",
    "DEFAULT_COOLDOWN_SEC",
    "DEFAULT_REPEAT_COOLDOWN_SEC",
    "IncidentAlertState",
    "IncidentTelegramNotifier",
]
