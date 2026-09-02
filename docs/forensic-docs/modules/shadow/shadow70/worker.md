# src/nexus_scalp/shadow/shadow70/worker.py

- PURPOSE: Bounded async persistence worker for 70D shadow observations
  (TASK-05-70D-SHADOW spec 17/18/39/40): shadow work never blocks the
  tick path; bounded queue with drop (SHADOW_BACKPRESSURE telemetry);
  persistence batched and asynchronous through the AuditRepository
  background writer; bounded in-memory buffers.
- ARCHITECTURE LAYER: Application (background writer thread).
- RESPONSIBILITY: Shadow70Worker (enqueue/flush/status/start/stop),
  Shadow70QueueItem, format_shadow70_status.
- DEPENDENCIES: threading, queue, store (Shadow70Store), models
  (Shadow70Observation), logging.
- CONNECTS TO: runtime observation path (enqueue), store flush batches,
  UI status (format_shadow70_status).
- KEY CONCEPTS:
  - enqueue(): the tick-path entry — queue.Queue(maxsize=MAX queue
    default 2000) put_nowait; full queue → dropped++ + SHADOW_BACKPRESSURE
    warning, returns False (never blocks, never raises).
  - Writer thread (_run): daemon thread "shadow70-writer"; get(timeout=1)
    loop appends to _pending_observations; flush when pending >=
    batch_size (100) or every flush_interval_sec (5s) on timeout.
  - flush(): swaps pending lists under a lock, then pushes each
    observation through store.save_observation; per-item failures →
    persist_errors++; events/health/drift pending lists flushed too.
  - stop(flush=True): sets the stop event, joins (10s timeout), then a
    final flush of whatever is pending.
  - status(): enqueued/persisted/dropped/persist_errors/queue_size/
    max_queue/running/last_flush_at — REAL counters;
    format_shadow70_status: None → {"available": False}; else
    {"available": True, **status()} — truthful worker status for the UI
    (never fake values, per the honest-70D-evaluation-harness contract).
  - queue_health_callback: optional hook appending a state dict to
    _pending_events.
- HOT PATH / PERFORMANCE: enqueue is O(1) put_nowait; the writer thread
  does the DB queue puts off-path; batches bounded (100) and interval
  flushed — no unbounded accumulation.
- EDGE CASES & PITFALLS:
  - The in-memory queue combined with the AuditRepository queue means two
    layers of buffering; a crash between enqueue and flush loses
    observations (bounded by design — spec 18 accepts drops).
  - flush() swallows per-item store failures into persist_errors only —
    the observation is gone (no retry queue).
  - stop(flush=True) may block up to 10s join + flush; stop(flush=False)
    DROPS pending observations (caller choice).
  - qsize-based drop in enqueue races with the writer draining — slight
    over/under-dropping is possible but bounded.
  - status().last_flush_at uses datetime.fromtimestamp(self._last_flush,
    UTC) with a time.monotonic-based _last_flush value? No —
    _last_flush is initialized to time.time() and updated in flush() to
    time.time() — consistent.