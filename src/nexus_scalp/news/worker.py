"""News Intelligence Worker (PHASE 12).

Isolated, restart-safe, cancellable background worker for the News Engine.
Follows the repository's worker contract (see research/worker.py):

    * NEVER blocks trading: invoked through ``asyncio.to_thread`` from the
      LiveEngine periodic task; heavy fetch/analysis NEVER runs inside
      ``_process_tick_pipeline()``.
    * FAILURE-ISOLATED: every cycle is wrapped; a failure logs
      ``[NEWS_WORKER] event=FAILURE`` and the worker continues. A broken
      source never stops the engine; the engine never stops trading.
    * RESTART-SAFE: ``start()``/``stop()`` manage state and a persisted
      checkpoint (``news_worker_state``); a queue of pending job ids is
      stored so missed cycles recover.
    * BOUNDED: max queue size, deduplicated job ids, priority (breaking /
      high-importance first), retry with backoff, job expiration.
"""

from __future__ import annotations

import contextlib
import queue
import time
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.news.engine import NewsEngine
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.news.worker")

#: Default bounded queue capacity for pending article-analysis jobs.
DEFAULT_MAX_QUEUE: int = 1000
#: A queued job older than this is dropped as expired.
JOB_EXPIRY_SEC: float = 6 * 3600.0
#: Analysis jobs per cycle (CPU + optional API bounded).
ANALYZE_PER_CYCLE: int = 10


class NewsWorker:
    """Background news fetch + analysis loop."""

    def __init__(
        self,
        engine: NewsEngine,
        interval_sec: float = 60.0,
        max_queue: int = DEFAULT_MAX_QUEUE,
    ) -> None:
        self.engine = engine
        self.interval_sec = float(interval_sec)
        self.max_queue = int(max_queue)

        self.running = False
        self.cycle_count = 0
        self.last_cycle_start: datetime | None = None
        self.last_cycle_duration: float = 0.0
        self.last_error: str = ""
        self._last_run_ts: float = 0.0
        self._cancel_requested: bool = False
        self.auto_analysis_enabled: bool = False

        # bounded, deduplicated, priority job queue (article_id, priority)
        self._jobs: queue.PriorityQueue[tuple[float, str, float]] = queue.PriorityQueue(
            maxsize=self.max_queue
        )
        self._queued_ids: set[str] = set()
        self._retries: dict[str, int] = {}
        self._enqueue_ts: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._restore_checkpoint()
        logger.info("[NEWS_WORKER] event=START status=RUNNING")

    def stop(self) -> None:
        if not self.running:
            return
        self.running = False
        self._save_checkpoint()
        logger.info("[NEWS_WORKER] event=STOP status=IDLE")

    def request_cancel(self) -> None:
        self._cancel_requested = True
        logger.info("[NEWS_WORKER] event=CANCEL_REQUESTED")

    # ------------------------------------------------------------------
    # Checkpoint (restart-safe)
    # ------------------------------------------------------------------

    def _save_checkpoint(self) -> None:
        try:
            pending = list(self._queued_ids)[:200]
            self.engine.db.save_worker_state(
                {
                    "scope": "news",
                    "cycle_count": self.cycle_count,
                    "last_cycle_at": (
                        self.last_cycle_start.isoformat() if self.last_cycle_start else ""
                    ),
                    "last_error": self.last_error,
                    "last_checkpoint": ",".join(pending),
                }
            )
        except Exception as e:
            logger.debug("[NEWS_WORKER] checkpoint save skipped", error=str(e))

    def _restore_checkpoint(self) -> None:
        try:
            state = self.engine.db.load_worker_state()
            if not state:
                return
            self.cycle_count = int(state.get("cycle_count", 0) or 0)
            pending = str(state.get("last_checkpoint", "")).split(",")
            for article_id in pending:
                if article_id and article_id not in self._queued_ids:
                    self._enqueue(article_id, priority=0.5)
        except Exception as e:
            logger.debug("[NEWS_WORKER] checkpoint restore skipped", error=str(e))

    # ------------------------------------------------------------------
    # Job queue (bounded + dedup + priority + expiry)
    # ------------------------------------------------------------------

    def _enqueue(self, article_id: str, priority: float = 0.5) -> bool:
        if article_id in self._queued_ids:
            return False
        if self._jobs.qsize() >= self.max_queue:
            logger.warning("[NEWS_QUEUE] event=FULL dropped=%s", article_id)
            return False
        try:
            # PriorityQueue: lower tuple[0] = higher priority.
            prio = max(0.0, 1.0 - float(priority))
            self._jobs.put_nowait((prio, article_id, time.time()))
            self._queued_ids.add(article_id)
            self._enqueue_ts[article_id] = time.time()
            return True
        except queue.Full:
            logger.warning("[NEWS_QUEUE] event=FULL dropped=%s", article_id)
            return False

    def _drain_next(self, now: float) -> str | None:
        """Pops the next valid job (expired jobs are dropped)."""
        while not self._jobs.empty():
            try:
                _, article_id, enqueued = self._jobs.get_nowait()
            except queue.Empty:
                return None
            self._queued_ids.discard(article_id)
            enq = self._enqueue_ts.get(article_id, enqueued)
            if now - enq > JOB_EXPIRY_SEC:
                self._retries.pop(article_id, None)
                self._enqueue_ts.pop(article_id, None)
                logger.info("[NEWS] event=STALE article_id=%s", article_id)
                continue
            return article_id
        return None

    # ------------------------------------------------------------------
    # Cycle
    # ------------------------------------------------------------------

    def tick(self) -> bool:
        if not self.running:
            return False
        now = time.time()
        if now - self._last_run_ts < self.interval_sec:
            return False
        self._last_run_ts = now
        self.cycle_count += 1
        self.last_cycle_start = datetime.now(UTC)
        started = time.perf_counter()
        try:
            # 1. ingestion pass (bounded sources)
            ingest_stats = self.engine.ingest_cycle(max_sources=8)

            # 2. analysis — PRO AUTO when ON: drain ALL pending via Factory LLM
            #    (pro prompt + local fallback + junk purge) so variables are
            #    accurate and the DB stays clear. OFF = ingest-only, cheap.
            analyzed = 0
            _pro_summary: dict[str, Any] | None = None
            if getattr(self, "auto_analysis_enabled", False):
                try:
                    from nexus_scalp.news.pro_auto import run_pro_cycle as _run_pro_cycle

                    _svc = getattr(
                        getattr(self.engine, "live_engine", None), "settings_service", None
                    )
                    _pro_summary = _run_pro_cycle(
                        self.engine.db,
                        engine=self.engine,
                        settings_service=_svc,
                        limit=200,
                        prune_junk=True,
                    )
                    analyzed = int(_pro_summary.get("analyzed", 0) or 0)
                except Exception as _pro_err:
                    logger.warning("[NEWS_WORKER] PRO cycle fallback to local", error=str(_pro_err))
                    self._queue_recent_unanalyzed()
                    for _ in range(ANALYZE_PER_CYCLE):
                        article_id = self._drain_next(time.time())
                        if article_id is None:
                            break
                        retries = self._retries.get(article_id, 0)
                        if retries >= 3:
                            self._retries.pop(article_id, None)
                            logger.error(
                                "[NEWS_WORKER] event=FAILED article_id=%s retries_exhausted",
                                article_id,
                            )
                            continue
                        result = self.engine.analyze_article_id(article_id)
                        if result.get("ok"):
                            self._retries.pop(article_id, None)
                            analyzed += 1
                        else:
                            self._retries[article_id] = retries + 1
                            if retries + 1 < 3:
                                self._enqueue(article_id, priority=0.3)

            self.last_cycle_duration = time.perf_counter() - started
            self.last_error = ""
            # 3. refresh the live news context OFF the event loop so the tick
            #    path only ever reads the cached object (never the DB).
            try:
                self.engine.context.refresh()
            except Exception as ctx_err:
                logger.debug("[NEWS_CONTEXT] worker refresh failed", error=str(ctx_err))
            self._save_checkpoint()
            logger.info(
                "[NEWS_WORKER] event=UPDATE",
                cycle=self.cycle_count,
                ingested=ingest_stats.get("new", 0),
                analyzed=analyzed,
                duration_ms=round(self.last_cycle_duration * 1000.0, 1),
            )
            return True
        except Exception as err:
            self.last_cycle_duration = time.perf_counter() - started
            self.last_error = str(err)
            logger.error(
                "[NEWS_WORKER] event=FAILURE",
                cycle=self.cycle_count,
                error=str(err),
                exc_info=True,
            )
            return False

    def _queue_recent_unanalyzed(self) -> None:
        """Queues recent articles lacking analysis (bounded, deduped, idempotent).

        Skips tombstoned analyzed hashes so re-ingested stories never re-queue.
        """
        try:
            articles = self.engine.db.list_articles(limit=50, include_duplicates=False)
            for art in articles:
                if len(self._queued_ids) >= self.max_queue:
                    break
                ah = str(art.get("article_hash") or "")
                with contextlib.suppress(Exception):
                    if ah and self.engine.db.is_analyzed_hash(ah):
                        continue
                if self.engine.db.get_analysis(art["article_id"]):
                    # Backfill tombstone so future re-ingest of same hash stays suppressed
                    with contextlib.suppress(Exception):
                        if ah:
                            ex = self.engine.db.get_analysis(art["article_id"]) or {}
                            self.engine.db.remember_analyzed_hash(
                                ah,
                                title=str(art.get("title", "")),
                                analysis_id=str(ex.get("analysis_id", "")),
                            )
                    continue
                priority = float(art.get("importance_score", 0.0) or 0.3)
                self._enqueue(art["article_id"], priority=priority)
        except Exception as e:
            logger.debug("[NEWS_WORKER] queue scan skipped", error=str(e))

    # ------------------------------------------------------------------
    # Public enqueue for API-triggered analysis
    # ------------------------------------------------------------------

    def enqueue_analysis(self, article_id: str, priority: float = 0.5) -> dict[str, Any]:
        """API-triggered analysis job (AI Analyze button). Returns job status
        without blocking; the worker processes it in the background.

        Idempotent: already-analyzed stories return SKIPPED_ALREADY_ANALYZED
        instead of re-queuing (prevents AI confusion on duplicate fetches).
        """
        # Idempotent: don't re-queue already-analyzed stories
        with contextlib.suppress(Exception):
            art = self.engine.db.get_article(article_id)
            ah = str((art or {}).get("article_hash") or "")
            if art and ah and self.engine.db.is_analyzed_hash(ah):
                return {
                    "ok": True,
                    "article_id": article_id,
                    "status": "SKIPPED_ALREADY_ANALYZED",
                    "worker_running": self.running,
                    "reason": "hash already analyzed",
                }
            if self.engine.db.get_analysis(article_id) is not None:
                return {
                    "ok": True,
                    "article_id": article_id,
                    "status": "SKIPPED_ALREADY_ANALYZED",
                    "worker_running": self.running,
                    "reason": "article already analyzed",
                }
        added = self._enqueue(article_id, priority=priority)
        return {
            "ok": True,
            "article_id": article_id,
            "status": "QUEUED" if added else "ALREADY_QUEUED_OR_FULL",
            "worker_running": self.running,
        }


def format_news_worker_status(worker: NewsWorker) -> dict[str, Any]:
    """JSON-serializable worker telemetry."""
    engine = worker.engine
    ctx = engine.current_context()
    return {
        "running": worker.running,
        "cycle_count": worker.cycle_count,
        "interval_sec": worker.interval_sec,
        "status": "RUNNING" if worker.running else "IDLE",
        "last_error": worker.last_error or "",
        "queue_size": worker._jobs.qsize(),
        "queued_ids": len(worker._queued_ids),
        "news_state": ctx.state.value,
        "news_available": ctx.available,
        "news_stale": ctx.stale,
        "active_events": ctx.active_event_count,
    }
