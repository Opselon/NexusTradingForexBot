# Failure-Recovery Matrix (NSE crash-containment pass)

> Status: baseline 2026-09-01. Companion to `crash-safety.md` and
> `artifacts/forensics/crash-safety-audit.json`.
> Every row = subsystem × failure → expected console log, state, recovery,
> exit, and the test that proves it. GREEN means failure-injected → detected →
> logged → safe behavior → regression exists (never "a try/except exists").

Legend — State: truthful runtime state after the failure. Log: minimum
console/file record. Rec: recovery policy. Exit: CLI/process exit contract.
Test: primary regression evidence.

| # | Subsystem | Failure | Log (console) | State | Rec | Exit | Test |
|---|---|---|---|---|---|---|---|
| 1 | startup | missing/invalid config at launch | red panel `cfg_detail=Parse error…` + doctor FAIL | NOT READY | halt | 1 / EXIT_RUNTIME | launcher doctor contract; BUG-149/156 lineage |
| 2 | startup | `_preflight_or_raise` raise (model dir missing) | panel with real error | halted pre-READY | halt | 1 | launcher/CLI preflight paths |
| 3 | startup | MT5 connect fails ×3 | `[MT5_CONNECT]` attempts + CRITICAL + incident telemetry | engine stopped | fail-closed | shutdown | run_loop connect contract |
| 4 | startup | corrupt/width-mismatched checkpoint | CRITICAL `Checkpoint dimension mismatch; quarantining` | model quarantined `.corrupt` | no silent replace | raise | BUG-141 suite |
| 5 | config | hot-reload save with invalid value | validation error, snapshot version unchanged | old valid config ACTIVE | reject swap | 0 (rejected write surfaced) | runtime_config validate_field suite |
| 6 | DI | service construction raises (e.g. order_manager absent at reconciliation) | `[EXECUTION_RECONCILIATION] STARTUP_FAILED (isolated)` + incident | degraded reconciliation, trading gated | retry next cycle | n/a (loop continues) | engine init-order tests (BUG-105/130) |
| 7 | database | worker batch insert fails | `Audit Background Worker failed to insert batch` (error) | queue drained (task_done per item) | backoff 1s, continue | n/a | audit worker tests |
| 8 | database | DB locked/corrupt on read helper | error log + `exc_info=True` then sentinel | degraded sentinel (truthful) | BUG-142 discipline | n/a | test_forensic_repair_account_and_audit |
| 9 | database | migration fails | `DB_MIGRATION_FAILED` state + web report FAILED | original bytes restored | transactional rollback | EXIT_RUNTIME | DatabaseMigrator fail_after test |
| 10 | market data | tick fetch failure | raised → run_loop error log w/ traceback | stale feed watchdog armed | watchdog reconnect/resync | n/a | G29 stalled-stream contract |
| 11 | candle | duplicate/late tick | duplicate suppressed (no re-pipeline) | last REAL decision re-surfaced | skip + service workers | n/a | BUG-169 suite |
| 12 | features | NaN/Inf feature value | `Non-finite feature sanitized` warning (per index) | 0.0 + clip [-3,3] | sanitized (documented) | n/a | to_tensor_input contract |
| 13 | 50D | width ≠ 50 | RuntimeError `Feature contract violation …` | inference blocked | fail-closed | n/a | 50D gate tests |
| 14 | 70D | missing INVALID liquidity snapshot | RuntimeError `refusing to feed fabricated values` | inference BLOCKED this tick | fail-closed | n/a | BUG-125 / 70D assembly tests |
| 15 | 70D | bounds/hash violation | `SchemaContractError` w/ family+index | inference blocked | fail-closed | n/a | validate_70d_vector tests |
| 16 | tensor | shape mismatch at forward | real RuntimeError surfaced w/ component context | decision blocked | never resize | n/a | BUG-175 cross-schema regression |
| 17 | model load | artifact hash mismatch | `ManifestValidationError artifact hash mismatch` | MODEL FAILED | fail-closed | EXIT_RUNTIME | model-doctor/validate suites |
| 18 | model load | declared scaler missing | `scaler declared in manifest but file missing/corrupt` | MODEL FAILED | fail-closed | n/a | runtime T24 contract |
| 19 | inference | forward raises mid-tick | `[INFERENCE] in-trade inference failed (isolated)` w/ traceback | probs=None, position mgmt CONTINUES | protective stops active | n/a | Phase-15 exit contract tests |
| 20 | GPU/CPU | CUDA unavailable | device=cpu selection logged | CPU execution | validated fallback | n/a | LocalModelRuntime device pick |
| 21 | regime | classifier raises | run_loop error log w/ traceback | prior regime state retained / UNKNOWN | retry next tick | n/a | regime classifier suite |
| 22 | liquidity | governor snapshot invalid | assembly raises → `[INFERENCE] 70D assembly failed` warning | 70D inference blocked | fail-closed | n/a | liquidity_runtime v2 tests |
| 23 | strategy | policy exception mid-evaluate | run_loop error log w/ traceback | no proposal emitted | safe NO_TRADE default | n/a | policy suites |
| 24 | risk | sizing input NaN/None/Inf | `INVALID_INPUT_NAN_INF_NONE` reason | volume=0.0 | fail-closed | n/a | test_risk_engine |
| 25 | risk | clamp engine raises | `Risk engine clamp failed; falling back to hard cap` | HARD_MAX_LOTS bound | bounded fallback (logged) | n/a | order manager clamp tests |
| 26 | execution | ambiguous market-order result | `retcode %s but live position found (ticket=%s)` or terminal failure | verified fill OR confirmed failure | broker-truth probe, NO blind retry | n/a | ambiguous-fill recovery tests (BUG-142 verified-safe list) |
| 27 | execution | 3 consecutive rejections | CRITICAL `TRANSITIONED TO SAFE_MODE` | SAFE_MODE (orders blocked) | operator reset | n/a | execution suite |
| 28 | news | one source fails | `[NEWS_FETCH] source=… status=FAILURE failures=N` + backoff | source degraded, others live | exponential backoff ≤1h | n/a | fetcher health/backoff tests |
| 29 | news | worker cycle fails | `[NEWS_WORKER] event=FAILURE` + exc_info | last_error set, next cycle retries | bounded retries per article | n/a | news worker tests |
| 30 | workers | worker loop crash (telegram) | `[TELEGRAM_WORKER] event=CRASH …` + `_worker_crash` captured | truthful failure state | loop continues w/ backoff | n/a | telegram notifier tests |
| 31 | async | `_kick_worker` hung > timeout | `[WORKER_KICK] event=TIMEOUT … detaching hung call` | call detached, inflight cleared | continue | n/a | worker kick contract |
| 32 | web/api | endpoint raises | `WEB_ERROR endpoint=… request_id=… exception_type=…` + traceback (log only) | safe JSON `{error:{code,message,request_id}}` | sanitized 500 | HTTP 500 | web/errors contract tests |
| 33 | SSE | payload not JSON-serializable | `[SSE] event=SERIALIZATION_ERROR fields=…` | `error` frame w/ failed_fields; counters bumped | stream survives | n/a | BUG-110 tests |
| 34 | CLI | command raises | error panel + EXIT_RUNTIME (human == JSON decision) | structured failure | correct non-zero exit | EXIT_RUNTIME | CLI e2e suites |
| 35 | diagnostics | analyzer crashes / none run | `FAILED`/`error` status; `check raised:` health row | status=error (never clean) | truthful verdict | non-zero | diagnostics engine tests |
| 36 | forensics | check body raises | `CHECK-RAISED … group raised: …` UNKNOWN row | worst-status aggregation | visible UNKNOWN (never PASS) | gate exit per verdict | deploy gate tests (BUG-162) |
| 37 | release | checksum/manifest mismatch | FAIL row w/ detail | overall FAIL | no install | EXIT_RELEASE | test_release_system (4-layout matrix) |
| 38 | updater | SHA-256 mismatch on download | `SHA256_MISMATCH — artifact discarded` | state FAILED, install untouched | re-download cleanly | EXIT_UPDATE(4) | BUG-171 probe servers |
| 39 | updater | crash mid-install | next `update status` → ROLLBACK_REQUIRED + `CRASH_REQUIRES_ROLLBACK` | last-known-good preserved | rollback required first | EXIT_UPDATE(8) on rollback path | updater state-machine tests |
| 40 | updater | rollback w/o backup | FAILED_SAFE panel w/ actionable hint | FAILED_SAFE (truthful) | user data untouched | EXIT_RUNTIME(1) | BUG-173 rewritten tests |
| 41 | shutdown | one worker stop() raises | logged, remaining stops proceed | queue drained, no stale healthy claim | cleanup never masks primary | clean/1 per primary | _shutdown_async ordering tests |
| 42 | lifecycle | launcher `start` on empty-claim pidfile | grace window (BUG-170-hardening) then liveness check | single engine guaranteed | second starter reports RUNNING | 0 | BUG-170 concurrency probes |
| 43 | stop | taskkill rc=128 (dead pid) | `already stopped (stale pidfile)` warning | pidfile unlinked | honest no-op | 0 | BUG-172 tests |
| 44 | model CLI | model-validate w/o probabilities (fixed) | REAL probs via LocalModelRuntime; cross-schema → SCHEMA_MISMATCH panel | real metrics, honest verdict | fail-fast on mismatch | EXIT_RUNTIME | test_bug175_model_validate_probs (6) |
| 45 | dataset CLI | `--schema` unknown value (fixed) | explicit rejection panel | no ghost dataset | fail-fast | EXIT_USAGE | test_bug176_schema_flag (5) |

## Residual gaps (YELLOW, tracked)

| Gap | Evidence | Risk | Next action |
|---|---|---|---|
| ~594 `except Exception` sites log without traceback (best-effort contexts, migrations) | `artifacts/forensics/anti_crash_static_report.json` `HANDLER_NO_TRACE` | P2 observability debt; majority reviewed-EXPECTED | triage hot-path files first (live_engine, order_manager, server) then add `exc_info=True` |
| 8 thread-spawn sites without `add_done_callback` | same report `SPAWN_NO_DONE_CALLBACK` | LOW each (all capture crashes in-loop) — but none is centrally monitored | optional watchdog aggregation task |
| 2 `check=False` subprocess sites with wide windows | same report | LOW (runner kills tree + status TIMEOUT) | narrow result-inspection window |
| BUG-164 regression does not pin the "Dataset not found" panel text | reviewer report (1f60832) | P2 test confidence | pin panel text |
| model-replay returns prediction error INSIDE payload with exit 0 | reviewer report (1f60832) | P2 misleading-success class | fold into BUG-175 family follow-up |
| no automated GPU-failure injection test (CUDA OOM/driver) | this matrix | P3 (CPU fallback documented + device-pick tested) | add device-forced unit probe |
| no automated disk-full injection on audit worker | this matrix | P3 | add tmpfs-limited probe |

## Scorecard (evidence-based, 2026-09-01)

| Subsystem | Score | Evidence anchor |
|---|---|---|
| startup | GREEN | doctor/preflight contracts + engine runtime launch test |
| config | GREEN | runtime store validate + hot-reload reject tests |
| DI | YELLOW | reconciliation isolation tested; no generic DI container exists |
| database | GREEN | worker drain tests + BUG-142 read-helper exc_info |
| filesystem | YELLOW | workspace anchoring (BUG-149/156) tested; no disk-full injection |
| market data | GREEN | watchdog + honest tick contract (G29/BUG-169) |
| candles | GREEN | reseed/REPLACE+ALIGN tests |
| features | GREEN | sanitize + width-raise contract tests |
| 50D | GREEN | 50D gate tests |
| 70D | GREEN | validate_70d_vector + BUG-125 + BUG-141 suites |
| tensor | GREEN | BUG-175 cross-schema regression + real RuntimeError surfacing |
| model load | GREEN | manifest/hash/scaler hard-fail tests |
| model inference | GREEN | isolated in-trade failure w/ mgmt continuation |
| GPU/CPU | YELLOW | CPU fallback validated; no OOM injection |
| regime | GREEN | classifier suites |
| liquidity | GREEN | LIQUIDITY_RUNTIME v2 status matrix tests |
| strategy | GREEN | policy suites (0 swallowed exceptions) |
| risk | GREEN | INVALID_* fail-closed matrix + clamp fallback |
| execution | GREEN | idempotency + ambiguous-fill + SAFE_MODE tests |
| news | GREEN | per-source backoff + worker FAILURE exc_info |
| workers | GREEN | per-worker capture (telegram/audit/candle/shadow70) |
| async | GREEN | kick timeout detach + task tracking + shutdown cancel |
| web/api | GREEN | safe-error envelope + middleware 500 sanitize |
| SSE | GREEN | BUG-110 serialization diagnostic + stream survival |
| CLI | GREEN | exit-code contract suites (66 e2e + 170..176 nets) |
| release | GREEN | 4-layout verify matrix + tamper probe |
| updater | GREEN | state machine + crash recovery + BUG-170..173 nets |
| shutdown | GREEN | ordered guarded teardown tests |

Verdict: **HARDENED_WITH_GAPS** — no known silent-crash / false-success /
lost-traceback path on any P0 boundary; remaining work is observability
debt (P2/P3) tracked above, not a safety hole.
