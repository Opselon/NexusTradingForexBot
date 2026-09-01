# Observability Log Contract — frozen 2026-09-01 (Agent 2)

Authoritative in-repo reference for the log-noise contracts proven in the
operational-observability sprint and pinned by
`tests/unit/test_operational_log_hygiene.py` +
`tests/unit/test_observability_contract_freeze.py` +
`tests/unit/test_logging_redaction.py`.

Any future change to these behaviors MUST update those tests in the same
commit (test-first rule: a new repetitive runtime event needs an
aggregation/transition/rate-limit test before merge).

Companion tooling: `src/nexus_scalp/observability/event_aggregator.py`
(EventBatchAggregator — bounded, thread-safe, first-occurrence-immediate).

---

## Core invariants (all events)

1. SEVERITY ladder: DEBUG = diagnostic detail; INFO = normal state / bounded
   informational transitions; WARNING = first failure or actionable
   degradation; ERROR = actual correctness/runtime failure. Repeated normal
   state messages must NEVER escalate to WARNING.
2. REPRESENTATION vs BEHAVIOR: aggregation changes only the log
   representation. Analysis/decision semantics are never altered to reduce
   noise.
3. FIRST-OCCURRENCE POLICY: the first occurrence of a repeated event logs
   immediately (timeliness); repeats aggregate into bounded summaries.
4. SINGLETON POLICY: singletons are visible immediately (never delayed
   indefinitely); `flush(only_repeats=True)` emits summaries only for groups
   with count>1.
5. BOUNDEDNESS: an N-event storm of one signature produces ≤2 lines
   (1 immediate + 1 summary). The bound is per DISTINCT signature
   (event, reason, stage, recoverable), never per event.
6. INFORMATION PRESERVATION: every summary retains count, sample_ids
   (first 5, configurable), first_seen, last_seen, reason, recoverable.
7. MEMORY BOUNDS: the aggregator store is capped (MAX_GROUPS=64 signatures);
   unflushed evidence is never evicted to make room.
8. PROCESS-LOCAL: caches and aggregators are per-process. A restart re-logs
   a condition once. Never claim cross-restart deduplication.
9. STALE PROCESS ≠ CURRENT HEAD: before blaming current source for log
   behavior, verify process PID/start time vs the fix commit time (see the
   BUG-182B stale-process protocol in agents/skill.md).
10. SECRET SAFETY: tokens/keys are reported as booleans (token_present=…),
    never values; URLs redacted; numeric `key=value` pairs are exempt from
    entropy redaction (BUG-141b family); credential-shaped values are not.

---

## Per-subsystem contracts

### BLS / news fetch failure (HTTP 403)
- Root cause (PROVEN 2026-09-01): AkamaiGHost edge/network block on the
  client IP, NOT User-Agent. Probes: no UA → 403, app UA → 403, browser UA
  → 403 ("Access Denied" body); robots.txt also 403. The descriptive UA
  `NexusScalpEngine/1.0 (news intelligence)` is valid and must NOT be
  changed to impersonate a browser; a proxy is unnecessary.
- Expected flow: short repeated failures → WARNING (with backoff_sec);
  extended backoff (>300s) → INFO `FAILURE_DEGRADED` naming backoff_sec +
  next_retry_at; backoff skips → counted (`backoff_skips`, persisted every
  10th skip, per-skip line at DEBUG); success after failures → ONE
  `RECOVERED` INFO then `SUCCESS`, failures reset to 0, backoff cleared.
- Backoff is exponential, bounded at 3600s. Never convert to a tight retry
  loop. BLS stays in degraded mode while blocked; other feeds unaffected.

### Telegram (dormant-worker invariant)
- `enabled=false` ⇒ NO worker thread, NO heartbeat, NO retry loop, NO
  repeated BLOCKED_NOT_CONFIGURED; `health_state()=STOPPED`; send() fails
  fast (<0.5s) with truthful config_error counters.
- `enabled=true` + valid credentials ⇒ worker runs normally.
- `enabled=true` + missing credentials ⇒ self-resolves to disabled/dormant
  with a single actionable diagnostic (token presence as booleans only).
- GENERAL WORKER INVARIANT: any worker with a heartbeat must treat the
  enabled state as a hard prerequisite for starting that heartbeat. Optional
  modules: disabled means DORMANT, not active-and-failing.

### Strategy DEGRADED (experience evaluator)
- DEGRADED is a legitimate evaluator signal (recency-expectancy math,
  `degrade_expectancy_threshold_r`); classification is FROZEN — never
  touched for logging convenience.
- Logging: ENTERED → log immediately; STILL_DEGRADED → bounded reminder
  (≤1 per 600s per family, bounded 512-family dict); RECOVERED → one
  transition event. Severity INFO by business design.

### Orphan classification / dataset rejection (research dataset)
- Aggregation key: (event, reason, stage, recoverable). Do not casually
  change the key.
- Producers: `ORPHAN_CLASSIFIED_UNKNOWN` (recoverable=false — honest unknown
  provenance; classify-once cache is per-process) and `DATASET_REJECTED`
  (recoverable per `_RECOVERABLE_REASONS`).
- Flush boundary: end of each dataset `build()`; `only_repeats=True`
  (singletons were already logged at first occurrence).
- `recoverable=true/false` must NEVER disappear during aggregation.

### PRO_AUTO 'LLM empty' (news pro_auto)
- The condition is CYCLE-level (Factory LLM provider unavailable/degraded:
  429 storm → circuit open → 401 auto-disable), not article-level.
- First article logs the WARNING immediately; repeats aggregate
  (reason = last_error, or NO_ATTEMPT_PROVIDER_UNAVAILABLE when the gate
  was already down); flush at the PRO cycle boundary via
  `flush_llm_empty_aggregate()` inside `run_pro_cycle`.
- The local deterministic fallback ALWAYS runs (behavior unchanged).
- Live evidence for the fix: 914 identical WARNING lines on 2026-09-01,
  each with failures=0/requests=0/last_error='' (zero marginal signal).

### DB hygiene
- `verdict=ACTION_REQUIRED` does NOT mean destructive repair. Production
  posture is AUDIT_ONLY (dry_run=true, apply_deletes=false,
  delete_candidates=0). No automatic delete/vacuum/archive without an
  explicitly authorized separate repair operation.
- `INITIAL_AUDIT_COMPLETE` is self-contained: verdict,
  consistency_violations, orphans, duplicates, missing_indexes,
  first_violation=<rule>/<table>, recommended_action, auto_delete state,
  report path. Do not reintroduce "one line points to another file merely
  to understand what happened".

### Provider gate (external LLM)
- States: AVAILABLE / RATE_LIMITED / DEGRADED / CIRCUIT_OPEN / HALF_OPEN /
  AUTO_DISABLED. 429 storms open the circuit (bounded cooldown → half-open
  probe); 401 = permanent misconfiguration → AUTO_DISABLE with ONE error
  event; success after degraded → ONE `RECOVERED` event. Failures
  transition into degraded/backoff/bounded retry — never tight loops.

---

## Stale-process diagnostic pattern (MT5 auth loop lesson)
A cadence storm correlated with `MT5 Authorization failed` + thousands of
RETRY events + worker kick timeout is diagnosed by: (1) process PID + start
time vs the fix-commit time, (2) error bucketing by timestamp across
.log/.part-NNN files, (3) requiring the fix's own signature log line in the
post-restart window. A long-lived process keeps pre-fix code in memory:
stale process ≠ current HEAD. Diagnostic output should surface PID, start
time, and build/git identity where the runtime already provides them.

## Live posture shape (evidence-backed 2026-09-01 — do NOT hardcode values)
healthy / degraded / disabled / stopped / rate-limited are DISTINCT states,
observable via health snapshots and event counters — without reading
hundreds of log lines. Snapshot at freeze time: engine clean; news healthy
feeds + BLS/BEA degraded; telegram dormant; database audit-only; research
aggregated; strategy edge-triggered; errors 0.

## Certification command (observability suite)
```
.venv/Scripts/python.exe -m pytest tests/unit/test_operational_log_hygiene.py tests/unit/test_observability_contract_freeze.py tests/unit/test_logging_redaction.py tests/unit/test_telegram_notifier.py tests/unit/test_news_phase12.py tests/unit/test_database_hygiene_task11.py -p no:cacheprovider
```
