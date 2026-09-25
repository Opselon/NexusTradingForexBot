# Crash-Safety Architecture (NSE)

> Status: HARDENED_WITH_GAPS baseline documented 2026-09-01 (rev 2 — checker
> regex fix + 381-site silent-handler backlog tracked as P1 violations).
> Owner: Nexus-Main (crash-containment master pass).
> Scope: the failure contract of every major execution boundary, from process
> startup through model tensor execution, trading execution, Web/API, updater,
> release pipeline, and shutdown.
> Companion documents: `failure-recovery-matrix.md` (per-subsystem failure
> matrix), `artifacts/forensics/crash-safety-audit.json` (machine audit).

---

## 1. Governing principle

The requirement is NOT "the application must never crash". The requirement is:

```text
NO SILENT CRASH      NO SILENT EXCEPTION      NO FALSE SUCCESS
NO FAKE HEALTH       NO FABRICATED MODEL OUTPUT
NO UNSAFE TRADING AFTER CRITICAL FAILURE
NO CORRUPTED STATE AFTER FAILURE      NO LOST TRACEBACK
```

Every important boundary follows one conceptual pipeline:

```text
OPERATION -> EXPECTED RESULT -> FAILURE -> DETECT -> CLASSIFY
  -> LOG TO CONSOLE (full traceback for unexpected exceptions)
  -> UPDATE SYSTEM STATE (truthful, never silently "healthy")
  -> RECOVER / RETRY / DEGRADE / ROLLBACK / FAIL-CLOSED
  -> RETURN TRUTHFUL RESULT -> EMIT STRUCTURED EVENT WHEN APPLICABLE
```

`except Exception: pass` is acceptable ONLY when the handler is (a) explicitly
part of the domain contract, (b) observable, and (c) unable to create unsafe
behavior. The two sanctioned classes today:

1. **Idempotent migration DDL** — `ALTER TABLE/ADD COLUMN/CREATE INDEX` on
   existing schema objects (`AuditRepository`, strategy-factory store). The
   failure is duplicate-column noise; the migration end-state is re-verified.
2. **Best-effort isolation writes** — a failure-isolated observability or
   enrichment write (telemetry, governance snapshot) whose loss cannot affect
   trading decisions; the loss itself is logged at error/warning level.

Static enforcement: `scripts/ci/anti_crash_static.py` (section 48 check).
P1 violations (bare except, BaseException outside deliberate boundaries,
silent handlers `pass/continue/return None/False/0/{}` — including the
two-line indented form) fail the run; reviewed-and-accepted sites carry an
inline `# anti-crash: allow (<reason>)` marker or live in the documented
allowlists (migration DDL files, `EXPECTED_SILENT_HANDLERS` probe chains).

**Baseline status (honest):** the first checker revision had an
indent-anchoring hole — two-line silent handlers escaped detection. An
adversarial self-test caught it; the fix surfaces **381 real silent-handler
sites** (top: `live_engine.py` 48, `debug_snapshot.py` 16, `pro_auto.py` 13,
`server.py` 13). Most are reviewed fail-safe defaults or observability-only
writes, but until each carries either truthful state logging or an explicit
allow marker, the check's default mode reports them. Use `--warn-only` to
downgrade to advisory while the backlog is triaged (see the audit JSON,
`residual_gaps[0]`).

---

## 2. Console logging policy (unexpected exceptions)

One structured logging system exists: `observability/logging.py`
(structlog, severity-split `logs/<sev>/YYYY/MM/`, redaction). Never add a
second one.

For every unexpected exception at a major boundary the console/file record
carries: timestamp (ISO-8601 +03:30), `[ERROR]`/`[CRITICAL]` level, component,
exception type, message, and `exc_info=True` (full traceback). Redaction
(`_redact_sensitive_fields`) applies everywhere; secrets never reach console.

Known redaction trap (BUG-177 class): phrase evidence WITHOUT `key=VALUE`
shape when the value is a non-secret enum-like token — the high-entropy
scrubber rewrites bare lowercase values (e.g. `reconstruction_source=NONE`)
into `[REDACTED_SECRET]`, destroying the distinction the log exists to show.

`structlog` renders to stdout: tests must assert via `capsys` or the module
logger mock, never `caplog` (BUG-142 lesson).

---

## 3. Boundary-by-boundary contract

### 3.1 Startup chain (launcher / CLI `start`)

Chain: banner -> preflight doctor -> config load -> logging init -> adapter
bind -> `LiveEngine.__init__` -> `_preflight_or_raise()` -> web app ->
`asyncio.gather(server.serve(), engine.run_loop())`.

* Invalid/missing config at doctor time → red FAIL panel + `sys.exit(1)`
  (launcher) / `EXIT_RUNTIME` (CLI). Launch never proceeds.
* `_preflight_or_raise` failure → panel with the real error + exit 1.
* Engine `run_loop` fatal → `logger.critical(..., exc_info=True)` + Telegram
  notify + re-raise; `asyncio.gather(..., return_exceptions=False)` makes the
  first fatal failure tear down both server and engine (no half-alive state).
* `run_concurrently` exception → FATAL panel + `EXIT_RUNTIME`. The "terminated
  cleanly" panel in the launcher `finally` is cosmetic; the exit code and the
  FATAL panel carry the truth.
* MT5 connect: 3 bounded attempts with console+Telegram visibility; final
  failure = `MT5_CONNECT_FAILED` incident + engine shutdown (fail-closed).
* Cold-start model artifact absent → fresh 50D bootstrap (documented
  contract); corrupt width-mismatched checkpoint → quarantined `.corrupt` +
  `RuntimeError` (never silently replaced).

**Never claim READY/HEALTHY/LIVE after a failed startup stage.** The web
`/health` endpoint maps `HealthEngine` verdicts: READY/DEGRADED → 200,
NOT READY or critical FAIL → 503, raised check → 503 UNHEALTHY (checked
explicitly, never a silent PASS).

### 3.2 Configuration

* `AppConfig` is bootstrap-only; live reads go through `RuntimeConfigStore`
  snapshots (versioned, hot-reload).
* Hot reload: a rejected/broken save leaves the previous valid snapshot
  active (`settings_service` + runtime store write paths validate before
  swap). Partial application of a failed config is forbidden.
* Telegram credentials only via `settings_service.set_telegram()` (INV-010).

### 3.3 Database (AuditRepository)

* Hot path never touches SQLite; all writes queue to one background worker
  (`audit_worker`). Queue-full → `logger.error` drop (never a silent loss).
* Worker batch failure → error log + `task_done()` for every item (queue
  cannot deadlock `close()`); 1s backoff.
* Read helpers log failures with `exc_info=True` before returning their
  degraded sentinels (BUG-142): a broken audit DB cannot masquerade as
  "nothing to reconcile".
* `close()` joins worker with timeout; shared-conn close is best-effort.
* Migration failure inside the updater restores original DB bytes
  (transactional, `DatabaseMigrator`).

### 3.4 Market data / candles

* `get_last_tick` raises on failure (never fake data); duplicate-tick
  re-pipeline suppressed (BUG-169) — the last real decision is re-surfaced,
  no synthetic `NO_TRADE conf=0.0`.
* Watchdog: disconnected → bounded reconnect + broker-history resync;
  connected-but-quiet > 15s → STALE incident + resubscribe (never a masked
  dead feed). Aggregator drops any in-memory history on `reseed`
  (broker REPLACE+ALIGN, INV-008 — no fabricated candles).

### 3.5 Features / 50D / 70D (hard invariants)

* 50D: `to_tensor_input()` raises on any width ≠ 50; NaN/Inf sanitized to 0.0
  with a logged warning and clipped to [-3, 3] (`_validate_50d_tensor` double
  gate at assembly).
* 70D: `validate_70d_vector` raises `SchemaContractError` on dimension /
  finiteness / bounds / hash violations. Missing liquidity VALID snapshot →
  RuntimeError (fabricated liquidity values forbidden). 70D assembly failure
  with a 70D champion → inference BLOCKED for that tick (fail-closed), never
  silently resized.
* `InferenceValidator` (runtime70) reports the FIRST failing contract stage
  with an explicit code (`SCHEMA_MISMATCH`, `DIMENSION_MISMATCH`, …).

### 3.6 Tensor creation / inference

* Expected-shape mismatches surface as the real `RuntimeError` (mat1/mat2
  style) with component context — never pre-resized to "make it fit"
  (BUG-175 regression: a 50D model on a 70D dataset must fail with an
  explicit mismatch error, not a fabricated metric).
* `_infer_probabilities` logs the failing stage; an in-trade inference
  exception degrades to `probs=None` while protective position management
  CONTINUES (stops never pause) — the failure is logged with traceback.
* Inference exceptions can never become BUY/SELL: the freshness gate plus
  policy default-to-NO_TRADE ensure the last state is only re-surfaced, and
  marked by its original decision_stage.

### 3.7 Model loading / GPU

* Width-contract guard (BUG-141): writers refuse to persist weights that
  contradict the path's declared contract (`[BUG141_GUARD]
  ARTIFACT_WIDTH_CONTRACT_REFUSED`); force-fresh seeds from the path's
  declaration.
* Scaler load failure → `ScalerBundle(None, None)` with a WARNING (documented
  degraded contract), and the declared-hash-missing case is a hard failure in
  the model_generation runtime (never silently skip a declared scaler).
* CPU-only runs are the validated fallback (torch device pick in
  `LocalModelRuntime`); CUDA-specific failures surface as load failures.

### 3.8 Risk (CRITICAL, fail-closed)

* `calculate_dynamic_volume` returns `(0.0, INVALID_*)` reason codes for any
  NaN/Inf/None input — zero size is the SAFE direction; permissive fallbacks
  are forbidden. `OrderManager._clamp_dispatch_volume` falls back to
  HARD_MAX_LOTS on clamp failure (bounded, logged) and rejects volume ≤ 0.
* Broker-native margin/profit verification falls back to a conservative
  estimate with provenance (`FALLBACK_ESTIMATE` / `UNAVAILABLE`), never to a
  fabricated authoritative value.

### 3.9 Execution / broker

* `execute_market_order` ambiguous retcode → live-position probe (ticket
  found ⇒ treated as filled; else terminal failure). No blind retry of a
  non-idempotent market order. Pending orders use a fingerprint idempotency
  guard (`_find_equivalent_pending`) before re-place.
* `send_order` → `order_check` warning fallback, retcode translation, honest
  REJECTED path with consecutive-failure SAFE_MODE transition.
* 3 consecutive rejections → SAFE_MODE (trading halted, visible).

### 3.10 Workers / async tasks

Every long-lived worker owns an exception boundary that logs with traceback
and records a truthful health/`last_error` state:

* Telegram notifier: crash capture (`_worker_crash`), heartbeat, restart-safe.
* Audit DB worker: batch failure logging + queue-drain guarantee.
* Candle-intel / shadow70 writers: bounded queues, backpressure telemetry,
  error-isolated loops.
* Engine `_kick_worker` calls: per-worker isolation (`WORKER_KICK TIMEOUT`
  detaches a hung call, `FAILED` logs it) — a dead worker can never stall the
  tick loop silently.
* `_retrain_task` / web `run_loop` task: tracked and failure-isolated;
  `_shutdown_async` cancels and awaits it.
* Web migration thread: failure recorded in `db_migration_state` as FAILED
  (never left "in progress" forever).

Loop-level: `run_loop` catches per-iteration exceptions (`logger.error(...,
exc_info=True)` + Telegram) and continues with a 1s backoff — the loop itself
must not die from one bad tick, but every exception is visible.

### 3.11 Web / API / SSE

* `web/errors.py` is the single safe-error surface: public payloads carry
  stable `error.code` + generic message + `request_id`; internal logs carry
  the full traceback (`log_web_error`). Middleware sanitizes unhandled 500s.
* SSE serialization failure → structured `error` event (BUG-110) with failed
  fields, counted in diagnostics; the stream survives. SSE/WebSocket client
  failures discard only the dead connection.

### 3.12 CLI / diagnostics / release / updater

* CLI commands map failures to stable exit codes (`EXIT_RUNTIME`,
  `EXIT_USAGE`, `EXIT_RELEASE`, …) and emit human panels that AGREE with the
  JSON payload (BUG-173 contract: FAILED_SAFE → exit 1 + actionable panel).
* `verify_release` failures always produce FAIL rows (checksum mismatch,
  missing artifact, stale web bundle, secrets hit) — a verification exception
  can never read as PASS.
* Update state machine persists every transition; a crash in a mutating state
  is reported `ROLLBACK_REQUIRED` on the next invocation (never blindly
  resumed). `SafeDownloader` validates resume semantics (BUG-171) and discards
  non-conforming .part files. Rollback preserves last-known-good; FAILED_SAFE
  exits non-zero with the real state in the panel.
* Diagnostics engine: infrastructure failures force status `error` — "no
  analyzer ran" can never certify the codebase; forensic `run_checks` turns a
  raised check into an UNKNOWN `CHECK-RAISED` row (visible, never PASS).

### 3.13 Shutdown

`_shutdown_async` stops every worker in dependency order; each stop is
individually guarded so one cleanup failure cannot erase the rest or mask the
primary error. Audit `close()` drains its queue. Adapter disconnect and
notifier shutdown are best-effort. pidfile cleanup follows the BUG-170
atomic-claim + grace-window contract (a live claim is never misread as
stale).

---

## 4. Failure classification vocabulary

`EXPECTED_ERROR` · `RECOVERABLE` · `RETRYABLE` · `DEGRADABLE` ·
`ROLLBACK_REQUIRED` · `COMPONENT_FAILURE` · `FATAL` · `PROPAGATE`

Banned: `IGNORE` / `PASS` / pretend-success / hide. Every handler must have an
explicit reason; the static check enforces the mechanical half of this rule.

## 5. Anti-crash vs fail-closed (decision table, summary)

| Situation | Policy |
|---|---|
| Optional news source fails | DEGRADE + per-source backoff + visible |
| Transient HTTP/download failure | BOUNDED RETRY (max attempts, backoff) |
| Invalid config | REJECT (old valid runtime config stays active) |
| Model load failure | MODEL FAILED / fail-closed (no fabricated inference) |
| Tensor dimension mismatch | FAIL INFERENCE (never resize) |
| Risk calculation failure | FAIL CLOSED (zero size) |
| Ambiguous order result | UNKNOWN → verify broker truth, never blind-retry |
| DB corruption | FAIL CLOSED + migration/rollback transaction |
| Update crash mid-install | ROLLBACK_REQUIRED, last-known-good preserved |
| Shutdown cleanup exception | Log + continue remaining cleanup (never mask primary) |

## 6. Verification

* Regression nets: `test_bug175_model_validate_probs.py`,
  `test_user_hunt_bug170_171.py`, `test_bug162_forensic_cli_gate.py`,
  `test_model_artifact_contract_bug141.py`, `test_missing_outcome_backfill_bug174.py`,
  `test_live_reactivity_bug169.py`, release hardening suite, forensic gate
  suite (BUG-162/166).
* Static: `scripts/ci/anti_crash_static.py` (0 violations baseline
  2026-09-01; warning backlog tracked in the audit JSON).
* Real-execution: engine runtime launch test
  (`tests/integration/test_engine_runtime_launch.py`) must stay 0-error after
  any `LiveEngine`/launcher change; BUG-171 probe servers prove resume
  semantics with real localhost HTTP servers.

## 7. Known residual gaps (tracked, not hidden)

See `failure-recovery-matrix.md` §"Residual gaps" and
`artifacts/forensics/crash-safety-audit.json` `residual_gaps`. Highlights:
`HANDLER_NO_TRACE` warning backlog (~594 sites, majority inside migration /
best-effort contexts — triage order P0 hot path first), `SPAWN_NO_DONE_CALLBACK`
on non-asyncio worker threads (each has its own in-loop capture — documented
per-case in the audit JSON).
