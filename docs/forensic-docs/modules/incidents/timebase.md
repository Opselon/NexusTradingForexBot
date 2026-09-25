# src/nexus_scalp/incidents/timebase.py

- PURPOSE: Timebase forensics (TASK-13 STEP-07) — read-only probe comparing
  host UTC, Python UTC, DB now, and broker-deal timestamps to determine
  whether TIMEBASE_DIVERGENCE is real and where it matters (spec 22/23/24).
  Never writes trading data; produces artifacts/forensics/timebase_probe.json.
- ARCHITECTURE LAYER: Application (forensic probe), read-only DB access.
- RESPONSIBILITY: classify drift (spec 24) into DISPLAY_ONLY /
  UTC_OFFSET_BUG / NAIVE_DATETIME / BROKER_TIME_MISINTERPRETATION /
  SECONDS_VS_MILLISECONDS / DST_ERROR / PERSISTENCE_ERROR /
  HISTORY_QUERY_ERROR / MATCHING_ERROR / OTHER; list affected subsystems;
  expose per-event timestamp chains (spec 15).
- DEPENDENCIES: sqlite3 (read-only), statistics.median, observability
  logging; lazily imports adapters.mt5.providers.BROKER_SERVER_UTC_OFFSET_
  MINUTES (BUG-070) inside timebase_event_chain.
- CONNECTS TO: incidents worker/telemetry, /api/diagnostics timebase UI,
  incidents.reports bundle.
- KEY CONCEPTS:
  - `_parse_ts` (line 29): tolerant ISO parse — handles "Z", treats naive
    as UTC. Never raises; returns None on garbage.
  - `run()` anchors on host_now (UTC): measures host↔broker median offset
    (broker_offsets from LIVE rows synced within 12h), host↔db, host↔log,
    and derived broker↔db. `_broker_evidence` (line 97) explicitly splits
    LIVE window (synced_at ≥ now-12h) from backfill — a large backfill
    offset is sync-lag, NOT a live clock bug.
  - `_classify` (line 181): DB clock is the canonical UTC anchor. Rules:
    no broker evidence → OTHER; |host_db| > 300s → PERSISTENCE_ERROR;
    |host_mt5| > 3600s → HISTORY_QUERY_ERROR (sync-lag/backfill); >
    300s → BROKER_TIME_MISINTERPRETATION; else DISPLAY_ONLY.
  - `timebase_event_chain` (line 222): per-ticket chain — raw broker
    server-local value vs stored UTC vs ledger UTC (canonical clock) +
    difference_ms; normalization_rule = subtract
    BROKER_SERVER_UTC_OFFSET_MINUTES at sync (BUG-070). Rows stored with
    "+00:00" are flagged pre-fix naive fromtimestamp(epoch, UTC) writes —
    GMT+3 recorded as UTC = 3h future shift (documented, not auto-corrected).
  - `probe_event` (line 129): resolves the chain for one ticket from
    audit_broker_trades (+ledger cross-check); errors degrade to None,
    never raise.
  - `build_timebase_probe` writes JSON (ensure_ascii=False, indent=2,
    default=str) — BOM-free, human-readable.
- HOT PATH / PERFORMANCE: probes are on-demand/periodic only; queries are
  bounded (LIMIT 2000, 10s connection timeouts, closed in finally).
- EDGE CASES & PITFALLS: `host_mt5` uses median of ALL entry/exit offsets —
  a mixed old/new (pre/post BUG-070) trade set dilutes the median; log
  timestamp is file MTIME not content; probe_event's broker query matches
  `trade_id=? OR position_id=?` — position_id rows share the same id
  space as trade_id (both statically bound to ticket string).