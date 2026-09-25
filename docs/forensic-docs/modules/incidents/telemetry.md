# src/nexus_scalp/incidents/telemetry.py

- PURPOSE: Structured incident telemetry collector (TASK-13 STEP-02) —
  bridges canonical runtime events into the IncidentWorker's telemetry
  stream. PREFER structured events over parsing log text (spec 8).
  Produces bounded, non-blocking, never-on-tick-path observation; NEVER
  mutates trading state.
- ARCHITECTURE LAYER: Application (producer-side collector, thread-safe).
- RESPONSIBILITY: IncidentTelemetryCollector (emit/attach/flush,
  bounded ring), ENGINE_EVENT_MAP (canonical engine error class → (error_
  code, severity)), engine_event_to_telemetry (normalizer).
- DEPENDENCIES: threading, datetime, logging. No DB access.
- CONNECTS TO: engine components (emit with event_type/component/error_code/
  correlation_id/ticket/execution_id/severity + payload kwargs),
  IncidentWorker.ingest, web diagnostics (stats).
- KEY CONCEPTS:
  - IncidentTelemetryCollector.emit (line 57): builds a typed event dict
    (severity forced upper-case; payload kwargs merged with event fields
    winning), appends under a lock to a bounded _pending list (MAX_PENDING
    default 5000; overflow → dropped++, returns False — NEVER raises,
    NEVER blocks). Then opportunistically pushes to an attached worker via
    ingest (which ALSO never blocks); on success the event is removed from
    _pending (conflict: flutter of duplicates avoided; on failure stays
    pending for the next flush).
  - flush_to_worker (line 109): atomically swaps pending out under lock,
    then pushes each; a worker ingest exception BREAKS the loop — the
    remainder of the batch stays unconsumed (lost from _pending — only
    workers that raise are expected not to, ingest() is safe).
  - attach() allows wiring the worker after construction (engine side).
  - ENGINE_EVENT_MAP (line 143): canonical engine events → KNOWN_FAILURE_
    CLASSES codes + severity, e.g. LEDGER_WRITE_FAILED →
    SILENT_FINANCIAL_CORRUPTION/CRITICAL, POSITION_CLOSE_FAILED →
    ORDER_REJECTED/HIGH, BROKER_SYNC_FAILED → MT5_CALL_FAILED/MEDIUM.
  - engine_event_to_telemetry (line 174): maps a canonical engine event
    name (upper-cased lookup); unknown classes fall back to the raw event
    type with MEDIUM severity; explicit `severity` wins over the map; adds
    "detail" only when non-empty.
- HOT PATH / PERFORMANCE: emit is O(1) under a lock; the worker's ingest
  is a ring append — producers never block. flush is O(pending).
- EDGE CASES & PITFALLS: the `event in self._pending` removal uses list
  equality — two identical events emitted back-to-back could remove the
  WRONG instance if ingest were synchronous per event, but ingest is a
  ring append and the later event is appended after removal, so the
  removed index is always the just-appended one; flush_to_worker breaks
  on the FIRST ingest exception — remaining events are silently lost
  (not re-queued); timestamp accepts datetime/str/float — ms-vs-seconds
  ambiguity resolved downstream in worker._to_telemetry, not here.