"""Economic-calendar worker + News/Calendar unification (Phase 6/7/11).

Scheduling model (Phase 11 — never block the tick path):
    * ``CalendarWorker.tick()`` is invoked from the LiveEngine periodic
      maintenance kick via ``asyncio.to_thread`` (the SAME off-loop path the
      NewsWorker uses — ``WorkerSupervisor.kick_worker``), NEVER inline in
      ``_process_tick_pipeline``,
    * throttled internally: full re-fetch at ``refresh_interval_sec``
      (default 900s; the FF mirror rate-limits aggressively — verified 429
      with Retry-After), so typical cycles are bookkeeping-only,
    * every fetch result persists to ``news.db`` (calendar_events +
      calendar_worker_state tables — same DB, separate provenance/lifecycle
      per Phase 7),
    * the tick path consumes ONLY the in-memory envelope (cache-only read,
      INV-001 compliant) via ``latest_envelope()``.

Market Context unification (Phase 7): ``build_market_context()`` returns the
canonical composite {news: CurrentNewsContext-like summary, calendar:
envelope summary} with SEPARATE provenance/lifecycle per leg.
"""

from __future__ import annotations

import contextlib
import json
import time
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.calendar.gate import EventGatePolicy, evaluate_event_window
from nexus_scalp.calendar.models import (
    CALENDAR_MAX_STALE_SEC,
    CalendarEnvelope,
    CalendarHealth,
)
from nexus_scalp.calendar.providers import FedFOMCProvider, FFCalendarProvider
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.calendar.worker")

CALENDAR_SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS calendar_events (
        event_id TEXT PRIMARY KEY,
        event_type TEXT DEFAULT '',
        title TEXT NOT NULL,
        country TEXT NOT NULL,
        scheduled_at TEXT NOT NULL,
        importance TEXT NOT NULL,
        source TEXT NOT NULL,
        source_url TEXT DEFAULT '',
        retrieved_at TEXT NOT NULL,
        timezone TEXT DEFAULT 'UTC',
        status TEXT NOT NULL,
        actual TEXT DEFAULT '',
        forecast TEXT DEFAULT '',
        previous TEXT DEFAULT '',
        model_version TEXT DEFAULT 'econ_event_v1'
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS calendar_worker_state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        last_success_at TEXT DEFAULT '',
        last_failure_at TEXT DEFAULT '',
        last_error TEXT DEFAULT '',
        last_http_status INTEGER,
        consecutive_failures INTEGER NOT NULL DEFAULT 0,
        fetch_count INTEGER NOT NULL DEFAULT 0,
        event_count INTEGER NOT NULL DEFAULT 0,
        provider_states TEXT DEFAULT '{}',
        updated_at TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_calendar_events_sched ON calendar_events(scheduled_at);",
]


class CalendarWorker:
    """Owns the fetch loop + persistence for the forward calendar layer."""

    def __init__(
        self,
        db: Any,
        *,
        providers: list[Any] | None = None,
        refresh_interval_sec: float = 900.0,
        policy: EventGatePolicy | None = None,
    ) -> None:
        self.db = db
        self.providers = providers or [FFCalendarProvider(), FedFOMCProvider()]
        self.refresh_interval_sec = float(refresh_interval_sec)
        self.policy = policy or EventGatePolicy()
        self._envelope: CalendarEnvelope | None = None
        self._last_fetch_mono: float = 0.0
        self.last_error: str = ""
        self.cycle_count: int = 0
        self._ensure_tables()

    # -- schema ----------------------------------------------------------

    def _ensure_tables(self) -> None:
        """Idempotent, additive tables in news.db (dedicated DB, no audit mix)."""
        try:
            with self.db._connect() as conn:
                for ddl in CALENDAR_SCHEMA_SQL:
                    conn.execute(ddl)
        except Exception as e:
            logger.error("[CALENDAR] schema init failed (worker disabled-safe)", error=str(e))

    # -- worker surface ----------------------------------------------------

    def tick(self) -> bool:
        """One bounded worker cycle. Returns True when a fetch happened.

        Called OFF the event loop (asyncio.to_thread via kick_worker).
        Failure-isolated: an exception logs and returns False; the live
        path keeps serving the last persisted envelope.
        """
        try:
            now_mono = time.monotonic()
            if now_mono - self._last_fetch_mono < self.refresh_interval_sec:
                return False
            self._last_fetch_mono = now_mono
            self.cycle_count += 1
            merged = self._fetch_all()
            self._persist(merged)
            self._envelope = merged
            self.last_error = merged.fetch_error
            logger.info(
                "[CALENDAR] event=UPDATE health=%s events=%d future_high=%d fetch_error=%s",
                merged.health.value,
                len(merged.events),
                len(merged.future_high_impact()),
                merged.fetch_error or "(none)",
            )
            return True
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            logger.error("[CALENDAR] event=CYCLE_FAILURE (isolated)", error=str(e))
            return False

    def _fetch_all(self) -> CalendarEnvelope:
        """Fetches all providers and merges into ONE canonical envelope."""
        now = datetime.now(UTC)
        events = []
        errors: list[str] = []
        any_success = False
        newest_success: datetime | None = None
        for provider in self.providers:
            env = provider.fetch(now=now)
            errors.extend([f"{provider.provider_id}: {env.fetch_error}"] if env.fetch_error else [])
            if env.health == CalendarHealth.GOOD:
                any_success = True
                if env.last_success_at and (
                    newest_success is None or env.last_success_at > newest_success
                ):
                    newest_success = env.last_success_at
            # dedupe by event_id (FOMC can appear in both providers)
            events.extend(env.events)
        by_id: dict[str, Any] = {}
        for e in events:
            by_id.setdefault(e.event_id, e)
        merged_events = sorted(by_id.values(), key=lambda e: e.scheduled_at)
        health = CalendarHealth.GOOD if any_success else CalendarHealth.INVALID
        if not any_success:
            errors.append("no provider succeeded")
        return CalendarEnvelope(
            provider=",".join(p.provider_id for p in self.providers),
            generated_at=now,
            last_success_at=newest_success,
            health=health,
            events=tuple(merged_events),
            fetch_error="; ".join(errors)[:500],
        )

    # -- persistence -------------------------------------------------------

    def _persist(self, envelope: CalendarEnvelope) -> None:
        """Writes events + worker state (idempotent upserts, news.db)."""
        try:
            with self.db._connect() as conn:
                for e in envelope.events:
                    conn.execute(
                        """
                        INSERT INTO calendar_events
                            (event_id, event_type, title, country, scheduled_at,
                             importance, source, source_url, retrieved_at, timezone,
                             status, actual, forecast, previous, model_version)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(event_id) DO UPDATE SET
                            status=excluded.status,
                            actual=excluded.actual,
                            forecast=excluded.forecast,
                            previous=excluded.previous,
                            retrieved_at=excluded.retrieved_at
                        """,
                        (
                            e.event_id,
                            e.event_type,
                            e.title,
                            e.country,
                            e.scheduled_at.isoformat(),
                            e.importance.value,
                            e.source,
                            e.source_url,
                            e.retrieved_at.isoformat(),
                            e.timezone,
                            e.status.value,
                            e.actual,
                            e.forecast,
                            e.previous,
                            e.model_version,
                        ),
                    )
                conn.execute(
                    """
                    INSERT INTO calendar_worker_state
                        (id, last_success_at, last_failure_at, last_error,
                         last_http_status, consecutive_failures, fetch_count,
                         event_count, provider_states, updated_at)
                    VALUES (1, ?, ?, ?, NULL, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        last_success_at=excluded.last_success_at,
                        last_failure_at=excluded.last_failure_at,
                        last_error=excluded.last_error,
                        consecutive_failures=excluded.consecutive_failures,
                        fetch_count=excluded.fetch_count,
                        event_count=excluded.event_count,
                        provider_states=excluded.provider_states,
                        updated_at=excluded.updated_at
                    """,
                    (
                        envelope.last_success_at.isoformat() if envelope.last_success_at else "",
                        datetime.now(UTC).isoformat() if envelope.fetch_error else "",
                        envelope.fetch_error,
                        sum(p.state.consecutive_failures for p in self.providers),
                        sum(p.state.fetch_count for p in self.providers),
                        len(envelope.events),
                        json.dumps(
                            {p.provider_id: p.state.to_dict() for p in self.providers}, default=str
                        ),
                        datetime.now(UTC).isoformat(),
                    ),
                )
        except Exception as e:
            logger.error("[CALENDAR] persist failed (state stays in memory)", error=str(e))

    # -- live read surface (cache-only, tick-safe) --------------------------

    def latest_envelope(self) -> CalendarEnvelope | None:
        """The in-memory envelope (never touches DB/network). INV-001 safe."""
        return self._envelope

    def health_summary(self) -> dict[str, Any]:
        """Operational health for /api/news/health + weekly liveness report."""
        env = self._envelope
        provider_states = {p.provider_id: p.state.to_dict() for p in self.providers}
        return {
            "calendar_health": env.health.value if env else CalendarHealth.INVALID.value,
            "calendar_age_sec": round(env.age_sec, 1) if env and env.last_success_at else None,
            "max_stale_sec": CALENDAR_MAX_STALE_SEC,
            "event_count": len(env.events) if env else 0,
            "future_high_impact_count": len(env.future_high_impact()) if env else 0,
            "fetch_error": env.fetch_error if env else "never fetched",
            "providers": provider_states,
            "cycle_count": self.cycle_count,
            "last_error": self.last_error,
        }

    def gate_verdict(self, now: datetime | None = None) -> dict[str, Any]:
        """Current event-window verdict (for policy + telemetry)."""
        return evaluate_event_window(envelope=self._envelope, now=now, policy=self.policy).to_dict()


def build_market_context(
    *,
    news_context: Any | None,
    calendar_worker: CalendarWorker | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Canonical composite Market Context (Phase 7).

    Separate provenance + lifecycle per leg:
        news: derived from analyzed articles (backward-looking, decaying)
        calendar: scheduled events (forward-looking, timestamp-anchored)
    """
    now = now or datetime.now(UTC)
    out: dict[str, Any] = {"generated_at": now.isoformat()}
    if news_context is not None:
        with contextlib.suppress(Exception):
            out["news"] = {
                "available": bool(getattr(news_context, "available", False)),
                "state": str(getattr(getattr(news_context, "state", None), "value", "NORMAL")),
                "confidence": float(getattr(news_context, "confidence", 0.0) or 0.0),
                "freshness": float(getattr(news_context, "freshness", 0.0) or 0.0),
                "active_high_impact": len(getattr(news_context, "active_high_impact", []) or []),
                "stale": bool(getattr(news_context, "stale", False)),
            }
    if calendar_worker is not None:
        with contextlib.suppress(Exception):
            out["calendar"] = calendar_worker.health_summary()
            out["event_gate"] = calendar_worker.gate_verdict(now=now)
    return out


def weekly_liveness_report(
    *,
    news_db_health: list[dict[str, Any]],
    calendar_worker: CalendarWorker | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Phase 1D — answers 'which source has been dead for how long?'.

    Combines per-source news health rows + calendar provider state into one
    bounded, persistable report (the caller decides the sink: audit queue,
    Telegram, or artifacts JSON). Pure read + aggregate; never mutates.
    """
    now = now or datetime.now(UTC)
    sources: list[dict[str, Any]] = []
    for row in news_db_health or []:
        last_ok = str(row.get("last_success_at") or "") or None
        success_age: float | None = None
        if last_ok:
            with contextlib.suppress(ValueError):
                dt = datetime.fromisoformat(last_ok.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                success_age = max(0.0, (now - dt).total_seconds())
        failures = int(row.get("consecutive_failures", 0) or 0)
        healthy = bool(row.get("healthy", 0))
        if not row.get("last_success_at"):
            classification = "DEAD" if failures > 0 else "UNKNOWN"
        elif success_age is not None and success_age > 48 * 3600:
            classification = "STALE"
        elif healthy:
            classification = "ALIVE"
        elif failures > 0:
            classification = "DEGRADED"
        else:
            classification = "UNKNOWN"
        sources.append(
            {
                "source_id": row.get("source_id", ""),
                "health": classification,
                "last_success_age_sec": success_age,
                "failure_count": failures,
                "last_http_status": row.get("last_status"),
            }
        )
    dead = [s for s in sources if s["health"] == "DEAD"]
    return {
        "report_version": "news_liveness_v1",
        "generated_at": now.isoformat(),
        "sources": sources,
        "dead_count": len(dead),
        "dead_sources": [s["source_id"] for s in dead],
        "calendar": calendar_worker.health_summary() if calendar_worker else None,
    }
