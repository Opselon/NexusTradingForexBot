# src/nexus_scalp/news/worker.py

- PURPOSE: NewsWorker — isolated, restart-safe, cancellable background
  fetch+analysis loop for the News Engine. Follows the repo's worker
  contract (research/worker.py).
- ARCHITECTURE LAYER: Application (background task).
- RESPONSIBILITY: bounded cyclic ingestion + analysis, priority queue of
  pending article jobs with dedup/retry/expiry, persisted checkpoint,
  context refresh off the event loop.
- DEPENDENCIES: NewsEngine, observability.logging; stdlib queue/time.
- CONNECTS TO: LiveEngine periodic task (invoked via asyncio.to_thread —
  NEVER inside _process_tick_pipeline); Web status via
  format_news_worker_status.
- KEY CONCEPTS:
  - Constants: DEFAULT_MAX_QUEUE=1000, JOB_EXPIRY_SEC=6h, ANALYZE_PER_CYCLE=10.
  - Restart-safety: `start()` -> `_restore_checkpoint()` (line 111) reads
    news_worker_state and re-enqueues the stored pending article ids
    (last_checkpoint, up to 200, comma-joined); `stop()` -> `_save_checkpoint`
    (line 94); cycle_count + last_cycle_at + last_error persisted so missed
    cycles recover after a restart.
  - Job queue (line 61): queue.PriorityQueue maxsize=bounded, `_queued_ids`
    set for dedup, `_enqueue` maps priority via prio = 1.0 - priority (lower
    tuple[0] = higher priority); queue full -> drop + log, never block.
  - `_drain_next` (line 145): pops jobs, discards from _queued_ids, drops
    jobs older than JOB_EXPIRY_SEC (stale event, logged).
  - `tick()` (line 166): interval-gated; cycle = 1) ingest_cycle(max_sources=
    8); 2) `_queue_recent_unanalyzed()` (up to 50 recent non-duplicate
    articles lacking analysis, priority by importance_score); 3) drain up to
    ANALYZE_PER_CYCLE jobs with retry cap 3 (failure -> re-enqueue at
    priority 0.3 with backoff, exhausted -> logged FAILED and dropped); 4)
    refresh live context OFF the event loop (line 209-212 — the tick path
    only ever reads the cached object); 5) save checkpoint. Whole cycle
    wrapped: `[NEWS_WORKER] event=FAILURE` logged, worker continues.
  - `enqueue_analysis` (line 251): API/Analyze-button path — returns
    QUEUED/ALREADY_QUEUED_OR_FULL without blocking.
  - `format_news_worker_status` (line 263): JSON-serializable telemetry
    (running, cycle_count, queue sizes, news state/available/stale/events).
- HOT PATH / PERFORMANCE: tick() is called off the tick path; heavy fetch
  (httpx) + analysis never run inside the tick pipeline; queue ops are
  O(1); checkpoint writes happen once per cycle.
- EDGE CASES & PITFALLS: `_save_checkpoint`/`_restore_checkpoint` swallow
  exceptions (debug-level) so a broken DB row never kills the worker;
  retries reset on success only; a job that fails 3x is dropped (re-analysis
  of the article can still be triggered via API/next restart since
  _queue_recent_unanalyzed re-scans by "no analysis row" — note the failed
  article has no row, so it WILL be re-queued next cycle despite the retry
  cap); `request_cancel` sets a flag that tick() never actually checks —
  cancellation is cooperative via stop() in practice.