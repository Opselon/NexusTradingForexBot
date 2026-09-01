# Observability Map — Nexus Scalp Engine (NSE)

> TASK-OBS-AUDIT (2026-08-31, HEAD baseline 1f60832; delivered at dcc80d7).
> Agent: Hermes-ObsForensic. AUDIT-ONLY: zero runtime code changed.
> Machine-readable companion: `artifacts/forensics/observability-audit.json`.
> Method: black-box reconstruction from runtime evidence (logs / DBs / artifacts /
> live API / CLI) FIRST, then targeted read-only code sweeps for every claim.
> Evidence markers: [L]=live-probed today, [DB]=DB census, [LOG]=log sample, [C]=code file:line.

## 1. What the system actually produces today

| Channel | Reality (verified) |
|---|---|
| Logs | severity-split `logs/{critical,error,warning,info}/YYYY/MM/*.log`, ISO-8601 **+03:30**, 10 MB part rotation, retention 30/30/90/365 d [C observability/logging.py:388-394]. Only 08-29 + 08-31 exist; 08-30 absent. ~190 MB on 08-31 (131 MB error). |
| Log record | `ts [level] msg [logger] key=value…` + `[COMPONENT] event=X` prefixes; tracebacks present (60 in 08-31 error log). **`category`/`error_code` fields absent from file records; `correlation_id` never bound (`bind_correlation_id` has zero callers [C logging.py:691])**. |
| DBs | audit.db (61 tables, 114 MB) = decision/execution/ledger/governance/research/incident evidence, UTC ISO strings; candle_intel.db = per-bar decision forensics with **distinct `bar_ts` (event) vs `ts` (processing)**; news.db; strategies.db. `audit_broker_*` store raw MT5 **epoch ints (GMT+3 server-local)** — a third time dialect. |
| Incidents | `incidents` + `incident_events` tables + `artifacts/incidents/INC-*.json/md` with root_cause_status, evidence list, timeline, fingerprint, correlation_id column (populated 1/6 rows). |
| Web API | `/api/live/state` (timestamps{tick,features,inference,proposal} UTC, provenance map, live_freshness{state,age_ms,sequences}, health, state_version, is_stale) [L]; `/api/debug/state` 24-section snapshot incl. per-worker state + SSE diag + correlation_id [L]; `X-Request-ID` echo middleware live [L, probed 200]. |
| CLI | `nexus status --json`, `nexus forensic --snapshot --json` (17-group matrix, per-check timestamp/duration/evidence) [L, executed]. |
| Updater | `%LOCALAPPDATA%/NexusScalpEngine/update/update-state.json` — last entry `{state: FAILED_SAFE, correlation_id: upd-…, updated_at}` [L]. That correlation id appears in **zero** log lines. |

## 2. Forensic identity chain (as-built)

```text
request_id (uuid, policy) ── doubles as broker client order_id
   → TradeProposal → [EXEC_TRACE] log (id REDACTED today) → audit_signals.request_id
execution_id (EXEC-YYYYMMDD-HHMMSS-xxxxxx)
   → generated per decision → embedded into audit_orders.reason → audit_orders.execution_id
   → /api/debug/trace/{execution_id} join endpoint
ticket (broker) → position_lifecycle_events.sequence → audit_ledger → trade_autopsies
experience_id = "exp_"+request_id → audit_experiences (execution_id column 0% filled [C intelligence.py:556-573])
update correlation_id (upd-…) → update-state.json only (not logged)
deploy-gate correlation_id (fh-…) → deploy_gate_result.json only
per-boot correlation: NONE (no boot/run id; correlation contextvars never bound)
```

**Verdict: chain is PARTIAL and broken at the log↔DB seam.** DB side is strong
(request_id 100% in signals/experiences; EXEC- ids on 2026-08-20 orders); the log
side destroys the same ids (OBS-002). Live-probed SELL_LIMIT lifecycle
`EXEC-20260831-175500-a92169`: signal→order→lifecycle seq 83..87→ledger CLOSED
+6.24 joined cleanly **through the DB alone**; the log side offers only
second-granularity time correlation.

## 3. Subsystem observability map (required columns per task §46)

| Subsystem | Entry | Operation ID | Correlation | State | Event | Timestamp | Error | Result | Provenance | Recovery evidence | UI vis | CLI vis | DB persist |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Launcher/boot | NexusTradingForexBot.py | **none (no boot id)** | none | partial (mode/MT5/reconciliation logged) | good text trail | +03:30 | good (critical) | n/a | version via /health only | none (silent death) | — | status --json | none |
| MT5 adapter | connect() | attempt n/3 only | none | CONNECTED/FAILED | MT5_CONNECT | +03:30 | retcode (-6) preserved | bool | account-identity check (BUG-142) | 3-attempt retry visible | health.mt5 | — | audit_broker_* |
| Candle/bar | bar aggregation | none | none | forming/complete | **silent in log** (only in candle_intel.db) | bar_ts vs ts ✔ | — | — | symbol/timeframe ✔ | — | chart | — | candle_intel.db ✔ |
| Features 50/70D | to_tensor_input / build_70d | none | none | LIQUIDITY event stream ✔ | FEATURE_CALCULATION_OK (latency, bars) | ✔ | dim mismatch: text warning w/ expected-vs-actual | schema id+hash at load | — | live/state.features | — | — |
| Tensor | inference path | none | none | silent (no transition events) | none | — | shape-only text on failure | — | — | — | — | — |
| Model | load gate | fingerprint 2b98f333cf1e3f77 [L] | none | CHAMPION VERIFIED [L] | [MODEL] REGISTERED/SCHEMA_VERSION | ✔ | BUG141_GUARD class | — | hash+schema+dim ✔ | model_runtime_health ✔ | live/state.model ✔ | — |
| Inference | _process_tick_pipeline | none | none | freshness gate | **no started/finished events**; throttled console only | ✔ | "Async retrain failed" w/o shapes/device | probabilities in state | — | live_freshness ✔ | — | — |
| Regime | regime_classifier | none | none | transitions | [REGIME TRANSITION] prev/new/prob/reason ✔ | ✔ | — | — | — | chart/regime | — | market_regimes ✔ |
| Liquidity | liquidity_runtime | state_revision 138549 [L] | none | explicit status matrix ✔ | LIQUIDITY events ✔ | ✔ | source failures | available/calculation/source ✔ | live/state.liquidity ✔ | — | — |
| Policy | signals/policy.py | request_id + execution_id | REDACTED in logs | stage | EXEC_TRACE (action/conf/reason/regime/stage) ✔ | ✔ | blocked_by | decision_stage ✔ | audit_signals ✔ | debug/state.policy | — | audit_signals ✔ |
| Risk | risk_engine | none | inherits proposal | — | thin (reason codes embedded in signals) | ✔ | text | verdict | — | debug/state.risk | — | via signals only |
| Execution | order_manager | execution_id ✔ | to orders DB ✔ | 60-scenario router | [EXEC_TRACE]/[TRADE_LINEAGE] | ✔ | scenario reasons in DB | status | audit_orders ✔ | debug/state.execution ✔ | — | audit_orders ✔ |
| Broker result | mt5 adapter | ticket | via orders | ambiguous-fill recovery exists | text | epoch (GMT+3) | retcodes | deals | audit_broker_deals ✔ | — | — | ✔ |
| Accounting | ledger | ticket | — | CLOSED/… | thin | ✔ | — | pnl + exit_reason_source/confidence ✔ | audit_ledger ✔ | — | — | ✔ |
| Workers | hygiene/news/research/intel/training/incidents | cycle counts | none | heartbeat tables + debug/state.workers ✔ [L] | WORKER_KICK TIMEOUT (per worker) | ✔ | last_error in DB | state | research_worker_heartbeat ✔ | restart counts: none | — | worker_state tables ✔ |
| Async tasks | create_task sites | none | none | — | "Task exception was never retrieved" (1x, w/ traceback) | ✔ | traceback | — | — | — | — | — |
| Web/API | FastAPI | req_… + X-Request-ID ✔ | header→response ✔ (→log+DB: NO) | sanitized 500s (web/errors.py) | access log: none | ✔ | code+message+request_id ✔ | — | — | UI shows request id | — | settings_audit (0 rows) |
| SSE | /api/ticks/stream | event type only | none | CONNECTED/latency/reconnect diag ✔ [L] | no per-event id/producer | ✔ | serialization_errors counted ✔ | — | — | sse section ✔ | — | — |
| CLI | Typer | **none** | none | exit codes 0/1/2/3/4/5 | panels | none | panels+codes ✔ (BUG-173) | JSON parity ✔ | — | — | ✔ | — |
| Update | updater | upd-… correlation ✔ | **json only, not logged** | FAILED_SAFE etc. in json | none in logs | ✔ | state machine in json | — | update-state.json | partial (.previous-<ts> dirs) | UI? | update status | release_metadata (0 rows) |
| Release | metadata.py | commit sha in build info | — | — | — | build ts | — | — | checksums/manifest | verify-release CLI ✔ | — | — |
| Shutdown | engine | none | none | **no STOPPING→STOPPED log found** [L, 8/31] | none | — | — | — | — | none | — | — |

## 4. Trace matrix (§47) — reconstructable? (evidence basis: black-box pass)

| Operation | Start | Intermediate | End | Failure | Correlation | Reconstructable? | Missing |
|---|---|---|---|---|---|---|---|
| startup | ✔ boot trail | ✔ subsystem init | **no explicit READY marker** | ✔ critical path | **no boot id** | PARTIAL | boot id, stop evidence for prior instance |
| status | ✔ CLI | — | ✔ exit 0 | ✔ | n/a | YES | — |
| start (daemon) | ✔ O_EXCL pidfile claim | — | — | ✔ loser detection (post BUG-179) | pidfile only | PARTIAL | dev-run starts leave no pidfile [L] |
| stop | — | — | **rc-inspected but no log row** | ✔ BUG-172 panel | pidfile | PARTIAL | stop event not persisted |
| update | ✔ json state | **stage transitions unlogged** | ✔ FAILED_SAFE json | ✔ terminal state | upd-id **json-only** | PARTIAL | stage events, release_metadata rows |
| rollback | ✔ FAILED_SAFE non-zero (BUG-173) | .previous- dir | ✔ | ✔ | upd-id | PARTIAL | — |
| model inference | ✖ no started event | throttled probs console | ✖ no finished event | ✔ exception text | **ids redacted in logs** | PARTIAL | inference lifecycle events, ids in logs |
| feature build | — | ✔ LIQUIDITY stream | — | ✔ expected-vs-actual | none | PARTIAL | feature snapshot hash/summary |
| DB op | ✔ queue enqueue | ✖ | ✖ | ✖ generic "failed to insert batch" [C audit_repository.py:1422-1431] | none | **NO** | table/duration/commit/rollback, drop counters |
| worker | ✔ state rows | ✔ heartbeat [L] | — | ✔ last_error [L] | none | YES | restart count |
| API request | ✔ X-Request-ID | — | ✔ status | ✔ sanitized payload + internal log | **header never reaches logs/DB** | PARTIAL | request id in server log |
| SSE event | ✔ diag counters | — | — | ✔ counted | no event id | PARTIAL | producer/op id per event |
| release verification | ✔ verify CLI | ✔ sums/manifest | ✔ exit | ✔ | file hashes | YES | install→artifact link (release_metadata empty) |
| execution (order) | ✔ signal+order rows | ✔ lifecycle seq | ✔ ledger | ✔ reasons | execution_id ✔ DB-side | YES (via DB) | log-side ids (redacted), broker history gap [L: SELL_LIMIT ticket had 0 broker rows] |
| shutdown | ✖ | ✖ | ✖ | — | ✖ | **NO** | everything |

## 5. Black-box reconstruction results (10 scenarios, runtime evidence only)

1. **Engine startup 19:26 boot** — YES: config/mode/model fingerprint/scaler(70)/MT5/reconciliation trail.
2. **Failed startup (MT5 auth) 16:59** — YES-PARTIAL: 3× retcode(-6) + critical; no attempt id/boot id.
3. **One inference** — PARTIAL: EXEC_TRACE + audit_signals rich, but ids redacted in log; no inference lifecycle events.
4. **Failed inference** — PARTIAL: "Async retrain failed … mat and mat shapes cannot be multiplied" — no shapes/dtype/device/op-id; scaler refit warning has good expected-vs-actual but sits at WARNING while changing runtime identity.
5. **One update** — NO: json terminal state only; correlation id never logged; stages invisible.
6. **Failed update** — PARTIAL: FAILED_SAFE + upd-id + timestamp; nothing before it.
7. **One CLI op** — PARTIAL: strong JSON surfaces; no per-invocation op id; CLI self-identity stale (commit 53317de vs HEAD) [L].
8. **One API op** — YES-PARTIAL: request-id echo + state/freshness/health rich; debug/trace endpoint DEAD in running instance (OBS-001).
9. **Worker failure** — YES: WORKER_KICK + heartbeat/state tables + news_health per-source.
10. **Order lifecycle** — YES via DB chain (execution_id join); NO via logs alone (redacted ids).
Plus: **"SELL then disappeared"** black-box question — answerable from DB (signal 17:55:00Z → order → POSITION_EXITED 17:56:38Z → ledger +6.24, reason BROKER_DEAL_REASON conf 0.8), NOT from logs.

## 6. Gap ledger (OBS-001..016; full detail in observability-audit.json)

- **P0** OBS-002 (redactor eats EXEC- correlation ids — 2,631/2,631 EXEC_TRACE lines affected [C logging.py:161-163]) · OBS-003 (correlation ids never bound: `bind_correlation_id` 0 callers [C logging.py:691]).
- **P1** OBS-001 (/api/debug/trace dead import `nexus_scalp.adapters.audit_db` while /health=READY [C web/server.py:4769]) · OBS-004 (audit write failures/drops lack identity; rows silently dropped) · OBS-005 (EXPERIENCE rows written with execution_id 0% [C experience/intelligence.py:556-573]) · OBS-006 (update chain: correlation id json-only, stage transitions unlogged, release_metadata empty).
- **P2** OBS-007 shutdown/stop evidence absent · OBS-008 dev-run boots leave no pidfile/start id (BUG-170 covers `nexus start` only) · OBS-009 three time dialects (log +03:30 / DB UTC / broker epoch GMT+3, BUG-070) · OBS-010 audit_orders.latency 81% zeros; execution latency not traceable · OBS-011 async task failures unattributed (no task id/parent) · OBS-012 SSE events lack producer/op id.
- **P3** OBS-013 strategy-factory / dataset-rejection floods (15,289 + 20,373 identical lines/day) · OBS-014 CLI/health identity drift (version 9.0.3/commit 53317de vs HEAD 9.0.5) · OBS-015 request-id not persisted into logs/DB · OBS-016 CLI ops expose no operation id/timestamps.

## 7. Reconstruction scorecard

STARTUP YELLOW · PROCESS RED · DATABASE RED · MARKET DATA YELLOW · CANDLES GREEN (DB) · FEATURES YELLOW · 50D YELLOW · 70D YELLOW · TENSOR RED · MODEL GREEN · REGIME GREEN · LIQUIDITY GREEN · STRATEGY YELLOW · RISK YELLOW · EXECUTION YELLOW (DB-side GREEN, log-side RED) · WORKERS GREEN · ASYNC RED · CLI YELLOW · WEB/API GREEN · SSE YELLOW · UPDATER RED · ROLLBACK YELLOW · RELEASE YELLOW · SHUTDOWN RED.

**Overall: PARTIALLY OBSERVABLE.** The DB layer is the system's real flight
recorder; the log layer loses identity at the exact seam where an incident
investigator needs it (log↔DB join via correlation ids).

## 8. Recommended future fixes (NOT implemented here — audit-only mandate)

1. Allowlist id-shaped values in `_redact_value` (EXEC-/req_/upd-/fh-/INC- prefixes) — OBS-002.
2. Bind correlation context per boot + per decision (wire `bind_correlation_id`) — OBS-003.
3. Fix `adapters.audit_db` import in `/api/debug/trace` (or fail health visibly) — OBS-001.
4. Persist stage transitions for update/rollback into logs + release_metadata — OBS-006.
5. Structured DB-write failure events (table, rows, duration, drop counters) — OBS-004.
6. Thread execution_id through to audit_experiences — OBS-005.
7. Emit SHUTDOWN/STOPPED + explicit STARTUP_COMPLETE markers with a boot id — OBS-007.
8. Deduplicate/escalate flood hotspots; demote WORKER_KICK repeats to state rows — OBS-013.
