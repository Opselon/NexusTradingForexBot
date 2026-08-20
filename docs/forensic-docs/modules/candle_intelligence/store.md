# src/nexus_scalp/candle_intelligence/store.py

- PURPOSE: Candle Intelligence Store — Part 1: Schema + connection +
  background worker. Isolated local SQLite persistence
  (artifacts/candle_intel.db): 12 tables, all with the required audit
  columns. No network, no cloud, no remote telemetry.
- ARCHITECTURE LAYER: Persistence (infrastructure; isolated DB).
- RESPONSIBILITY: schema bootstrap (12 tables + indexes), WAL journal,
  RAM ring buffers + async batched writer (tick path never blocks on
  disk), bounded read facade, integrity/db-size observability; record_*
  methods are attached from store_writes at module load.
- DEPENDENCIES: config, observability.logging; stdlib sqlite3/threading/
  queue/collections/math/os/json/time.
- CONNECTS TO: engine (store.record_*) and consumers of recent_decisions/
  recent_closures/recent_vetoes; reader connection used for history.
- KEY CONCEPTS:
  - TABLES (line 31): candles, candle_closures, candle_patterns,
    market_regimes, feature_vectors, trade_proposals, trade_decisions,
    open_positions, exit_signals, risk_evaluations, rule_vetoes,
    audit_log — the 12 required by spec §8.
  - `_COMMON` audit columns (line 48): ts, symbol, timeframe, regime,
    pattern_name, pattern_score, candle_close_classification,
    decision_type, risk_state, reason_codes, raw_payload,
    computed_payload — appended to every table (leading comma; schema
    fragments end with a bare column/UNIQUE clause).
  - Deterministic serialization: `_j` (line 211) — NaN/Inf -> None,
    datetimes -> isoformat, sort_keys + compact separators; `_common_kwargs`
    builds the shared audit dict.
  - Performance architecture (docstring line 262): record_* enqueue onto
    an in-memory ring buffer and return in O(1) microseconds; a dedicated
    daemon thread `candle_intel_writer` drains into SQLite in batched
    transactions (max_batch_size rows per commit, 0.3s flush interval,
    WAL). SQLite connection owned by the worker thread only; reader
    connection is check_same_thread=False.
  - `enqueue` (line 388): put_nowait (queue.Full -> False), mirrors into
    per-table ring (deque maxlen=2000) for instant reads.
  - `query_recent` (line 441): RAM ring first (thread-safe snapshot under
    _write_lock, newest-first), DB fallback (ORDER BY id DESC) only when
    the ring is empty (restart/deep history); `_jsonify` converts stored
    JSON strings back in place; bounded limit [1,2000].
  - `flush`/`close`: drain queue with timeout (shutdown/checkpoint);
    context-manager support.
  - `db_size_bytes` (page_count*page_size) and `integrity_ok`
    (PRAGMA integrity_check) for observability.
  - `_attach_writes` (line 517): lazily imports store_writes (cycle
    avoidance) and binds record_candle/record_candle_closure/
    record_patterns/record_regime/record_risk/record_decision/
    record_veto/record_audit_log onto the class; declared as class attrs
    for type checkers.
- HOT PATH / PERFORMANCE: O(1) enqueue; batching amortizes commit cost;
  WAL allows concurrent reader+writer; flush interval 0.3s bounds
  read-latency staleness.
- EDGE CASES & PITFALLS: queue maxsize=20,000 — sustained overload drops
  writes silently (enqueue False, record_* returns False/0);
  `INSERT OR IGNORE` in _flush_batch means UNIQUE(bar_ts) collisions are
  silently skipped (candles/closures) — duplicate bars lose data without
  a warning; a worker crash (daemon thread) leaves rows in RAM rings lost
  at process exit unless close()/flush() ran; ring snapshots are
  per-table newest-first via reverse() — order matches DB id order only
  while the queue backlog is empty; no index on symbol/timeframe (only ts)
  for the 12 tables.