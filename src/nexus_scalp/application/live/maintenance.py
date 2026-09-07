"""MaintenanceCycle — periodic non-trading housekeeping of the live loop.

P1 seam L3 (god-file decomposition, extraction 3 of the live-engine wave):
the time-throttled maintenance cycle leaves ``application/live_engine.py``
verbatim (behavior-preserving extraction). Contains: audit retention purge
(BUG-054), database-hygiene cycle (TASK-11/22 + Telegram report), incident
response cycle (TASK-13, INV-019), daily Telegram performance summary
(BUG-057), background worker kicks, and the governance health snapshot (TASK-6).

Both call sites (run_loop's per-iteration block and the duplicate-tick
heartbeat path) delegate here — the duplicate implementations collapse into
ONE owner.

State ownership: throttles and worker handles stay at the composition root
(LiveEngine), reached through ``self.om``; this module owns the CYCLE LOGIC.
Every stage is failure-isolated: a maintenance fault never disturbs ticks.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.application.live.maintenance")


class MaintenanceCycle:
    """Time-throttled housekeeping cycle of the live loop (composition root)."""

    def __init__(self, om: Any) -> None:
        self.om = om

    async def run_cycle(self, *, now_t: float) -> None:
        """Runs one maintenance pass (all stages internally throttled)."""

    # BUG-054: audit retention purge (throttled ~6h, bounded batched
        # deletes, NEVER on the tick path). Failure is isolated: a purge
        # error must never disturb trading.
        now_t = time.time()
        if now_t - self.om._last_audit_purge_time >= self.om._audit_purge_interval_sec:
            self.om._last_audit_purge_time = now_t
            try:
                await asyncio.to_thread(self.om.audit.purge_old_audit_data)
            except Exception:
                logger.error("Audit retention purge failed (isolated)")
    
        # TASK-11 + TASK-22: database hygiene cycle (config-driven
        # cadence; AUDIT_ONLY first run, off the tick path via
        # asyncio.to_thread; never deletes unless the operator enabled
        # apply_deletes and execution mode is not LIVE).
        if self.om._hygiene_scheduler is None and now_t - self.om._last_hygiene_time > 0:
            try:
                from nexus_scalp.hygiene.hygiene_runtime import (
                    RuntimeCleanupScheduler,
                    RuntimeHygieneSettings,
                )
    
                hyg_cfg = getattr(self.om.config, "database_hygiene", None) or {}
                hygs = RuntimeHygieneSettings.from_mapping(
                    hyg_cfg.model_dump()
                    if hasattr(hyg_cfg, "model_dump")
                    else dict(hyg_cfg)
                )
                base_dir = getattr(self.om.config, "base_dir", None) or Path.cwd()
                self.om._hygiene_scheduler = RuntimeCleanupScheduler(
                    repo_root=base_dir,
                    settings=hygs,
                    execution_mode=self.om._runtime_mode
                    or str(
                        getattr(self.om.config, "execution_mode", "PAPER") or "PAPER"
                    ).upper(),
                )
            except Exception as hyg_init_err:
                logger.warning(
                    "[DB_HYGIENE] event=INIT_FAILED (isolated)",
                    error=str(hyg_init_err),
                )
        if (
            self.om._hygiene_scheduler is not None
            and self.om._hygiene_scheduler.settings.enabled
            and now_t - self.om._last_hygiene_time
            >= self.om._hygiene_scheduler.light_interval_sec
        ):
            self.om._last_hygiene_time = now_t
            try:
                deep = self.om._hygiene_scheduler.is_deep_due(now_t)
                # Run the scheduler cycle on a thread; it owns the
                # worker + quarantine + consistency + reports.
                cyc = await asyncio.to_thread(self.om._hygiene_scheduler.run_cycle, deep=deep)
                # Bounded Telegram REPORT (cooldown-gated, never spam).
                if self.om._hygiene_scheduler.settings.telegram_report and (
                    self.om.notifier is not None and self.om.notifier.enabled
                ):
                    tel = cyc.get("telemetry", {})
                    if (
                        not self.om._hygiene_scheduler._audit_done
                        or self.om._hygiene_scheduler.is_telegram_due(now_t)
                    ):
                        from nexus_scalp.hygiene.report import (
                            build_telegram_report_text,
                        )
    
                        text = build_telegram_report_text(
                            tel, self.om._hygiene_scheduler._cycle_number
                        )
                        self.om.notifier.send(text, severity="INFO")
                        self.om._hygiene_scheduler.mark_telegram_sent(now_t)
            except Exception as hyg_err:
                logger.warning(
                    "[DB_HYGIENE] event=CYCLE_FAILED (isolated)",
                    error=str(hyg_err),
                )
    
        # TASK-13: incident response cycle (throttled ~60s, off the
        # tick path via to_thread; observability-only, INV-019). The
        # worker correlates structured telemetry into incidents and
        # persists them; it can never block or alter trading.
        if now_t - self.om._last_incident_time >= self.om._incident_interval_sec:
            self.om._last_incident_time = now_t
            try:
                if self.om._incident_worker is None:
                    self.om._ensure_incident_worker()
                if self.om._incident_worker is not None:
                    await asyncio.to_thread(self.om._incident_worker.tick)
            except Exception as inc_err:
                logger.warning(
                    "[INCIDENT_WORKER] event=CYCLE_FAILED (isolated)",
                    error=str(inc_err),
                )
    
        # Daily Telegram performance summary (BUG-057): throttled to
        # once per 24h; built from the canonical accounting core (never
        # synthetic numbers). Failure is isolated.
        if now_t - self.om._last_daily_summary_time >= self.om._daily_summary_interval_sec:
            self.om._last_daily_summary_time = now_t
            try:
                # Performance Intelligence upgrade: deterministic
                # multi-stage report generator (reporting package)
                # consumes the canonical AccountingCore read-only and
                # produces the structured JSON contract + Telegram text.
                from nexus_scalp.accounting import PeriodKind
                from nexus_scalp.reporting import (
                    PerformanceReportEngine,
                    format_deep_report,
                    format_telegram_daily,
                )
    
                engine = PerformanceReportEngine(
                    core=self.om.accounting_core, kind=PeriodKind.DAY
                )
                container = engine.generate()
                compact = format_telegram_daily(container)
                deep = format_deep_report(container)
                try:
                    if self.om.notifier.enabled:
                        # MESSAGE 1 = compact summary; MESSAGE 2/3 =
                        # deep intelligence (deterministic split when
                        # the deep text exceeds one message).
                        self.om.notifier.send(compact, severity="INFO")
                        if len(deep) > 3500:
                            for chunk in _split_telegram_report(deep):
                                self.om.notifier.send(chunk, severity="INFO")
                        else:
                            self.om.notifier.send(deep, severity="INFO")
                except Exception:
                    pass  # Telegram failure is isolated
            except Exception as summary_err:
                logger.error(
                    "[TELEGRAM_REPORT] event=FAILURE error_type=GENERATION error=%s",
                    summary_err,
                )
    
        # ACCOUNT HISTORY: bounded background broker-history sync
        # (watermark + overlap, idempotent). Never on the tick path.
        if self.om._history_sync_started:
            try:
                self.om._kick_worker("HISTORY_SYNC", self.om.history_sync_worker.tick)
            except Exception as wkr_err:
                logger.warning("[HISTORY_SYNC_WORKER] event=KICK_FAILED error=%s", wkr_err)
    
        # PHASE 09: intelligence worker kick (throttled internally). It
        # runs in a worker thread and is fully failure-isolated; a
        # failure can never disturb the tick loop.
        if self.om._intelligence_worker_started:
            try:
                self.om._kick_worker("INTELLIGENCE", self.om.intelligence_worker.tick)
            except Exception as wkr_err:
                logger.warning("[INTELLIGENCE_WORKER] event=KICK_FAILED error=%s", wkr_err)
    
        # PHASE 09B: research worker kick (throttled internally, runs in
        # a worker thread). Research NEVER runs inside the tick
        # pipeline; a failure here can never disturb trading.
        if self.om._research_worker_started:
            try:
                self.om._kick_worker("RESEARCH", self.om.research_worker.tick)
            except Exception as wkr_err:
                logger.warning("[RESEARCH_WORKER] event=KICK_FAILED error=%s", wkr_err)
    
        # PHASE 10: controlled training worker kick (heavy CPU work is
        # bounded to worker threads; training can NEVER block ticks).
        if self.om._training_worker_started:
            try:
                self.om._kick_worker("TRAINING", self.om.training_worker.tick)
            except Exception as wkr_err:
                logger.warning("[TRAINING_WORKER] event=KICK_FAILED error=%s", wkr_err)
    
        # PHASE 11: shadow-aggregation worker kick (bounded, isolated).
        if self.om._shadow_worker_started:
            try:
                self.om._kick_worker("SHADOW", self.om.shadow_worker.tick)
            except Exception as wkr_err:
                logger.warning("[SHADOW_WORKER] event=KICK_FAILED error=%s", wkr_err)
    
        # PHASE 12: news intelligence worker kick (bounded, isolated).
        if self.om._news_enabled and self.om._news_worker_started:
            try:
                self.om._kick_worker("NEWS", self.om.news_worker.tick)
            except Exception as wkr_err:
                logger.warning("[NEWS_WORKER] event=KICK_FAILED error=%s", wkr_err)
    
        # TASK-6: bounded governance health snapshot (~5 min cadence,
        # queued write, failure-isolated — never blocks ticks).
        try:
            self.om._save_governance_health_periodic()
        except Exception as gov_err:
            logger.debug("[MODEL_GOVERNANCE] periodic health skipped", error=str(gov_err))
