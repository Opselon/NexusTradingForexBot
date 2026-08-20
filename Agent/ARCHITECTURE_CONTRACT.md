# ⚖️ ARCHITECTURE CONTRACT — Nexus Scalp Engine (NSE)

> **Purpose:** The LAWS of the system. These rules are non-negotiable. Any
> change that violates a rule here is a forbidden change, regardless of the
> reasoning that motivated it. Where this file conflicts with `agents/skill.md`
> or `agents/runtime_invariants.md`, the more specific/canonical source wins
> (code > skill.md > registries), and this file must be updated to match
> reality — the CODE is the final source of truth.
>
> **Related:** `Agent/PROJECT_GRAPH.md` (the map), `Agent/AGENT_REASONING_
> PROTOCOL.md` (how to operate), `agents/runtime_invariants.md` (INV-001..021),
> `agents/contracts.md` (registry), `agents/skill.md` (master map).

---

## 1. Layer Rules

| Layer | Can | Cannot |
|---|---|---|
| **Domain** (`domain/`) | Define business concepts; pure logic; frozen Pydantic contracts | Access databases; call external APIs; contain I/O of any kind; be mutated (use `model_copy`) |
| **Ports** (`ports/`) | Define the abstract interfaces (`IMT5Port`, `IGatewayPort`); dependency inversion | Implement external logic; hold state |
| **Adapters** (`adapters/`) | External IPC; Win32 MT5 bindings; ZMQ gateway; paper simulation; SQLite WAL persistence | Contain business/trading decisions; block the tick path; write synchronously on the hot path |
| **Features** (`features/`) | Compute causal feature vectors (50D/60D/70D); regime classification; liquidity structure; pure math | Do I/O; read the DB per tick; leak future bars (> decision_at are invisible); fabricate values (fallback must be deterministic + logged) |
| **Models / ML** (`models/`, `training/`, `model_generation/`, `model_lifecycle/`) | Train/validate candidates; artifact-first manifests; inference without DB | Auto-promote; overwrite the Champion; silently reshape schema; run heavy work on the tick path; hold order authority |
| **Signals** (`signals/`) | Multi-confluence routing; rule matrix; SMC God Mode; generate TradeProposals | Bypass regime guardian; generate orders directly (proposals only) |
| **Risk** (`risk/`) | Capital allocation; dynamic lot sizing; margin/tier clamps | Be bypassed for ANY order; return negative/NaN volume |
| **Execution** (`execution/`) | Order dispatch; position state machine; exits; protection; reconciliation | Skip risk validation; exceed `HARD_MAX_LOTS=10.0`; exceed `MAX_TOTAL_EXPOSURE=1`; trust unverified cancellation |
| **Application** (`application/`) | Async orchestration; tick loop; workers; model bundle lifecycle | Block the event loop; do sync DB I/O on the hot path (INV-001); `import time` inside hot functions (BUG-074) |
| **Learning layers** (`experience/`, `intelligence/`, `research/`, `shadow/`, `governance/`, `incidents/`, `forensics/`, `hygiene/`, `news/` analysis) | Analyze, score, recommend, reject, observe, alert, quarantine (advisory), clean (operator-gated), report | Place/modify/close orders; hold an adapter/order-manager/risk-engine (INV-002); mutate raw financial truth (INV-007); run on the tick path |
| **Web/API** (`web/`, `Web/`) | Serve canonical state; serialize enums; stream SSE/WS; sanitize errors | Recompute trading intelligence in JS; leak exception text (`str(e)`) to clients (BUG-040); fabricate data (never synthetic candles/SMC boxes); expose secrets (mask tokens) |
| **Persistence** (`adapters/database/`, `database/`, `settings/`) | WAL-ledger writes via background queue; versioned migrations; settings via SettingsService | Accept DDL outside migrations (INV-013); let UI/Telegram write live.yaml (INV-010/BUG-080); delete broker truth without archive + operator gate |

**Dependency direction:** Domain ← Ports ← Adapters ← Features ← Models ←
Signals ← Risk ← Execution ← Application ← Web. Lower layers never import
higher layers. Research/strategies/intelligence never import execution/risk/
adapters (tested).

---

## 2. Execution Safety Rules

1. **No order can reach execution without passing risk validation.**
   Route: proposal → RiskEngine `calculate_dynamic_volume()` → OrderManager
   `dispatch_order()` → adapter → broker. Any bypass is a critical defect.
2. **Absolute execution clamps (never weakened):**
   - `HARD_MAX_LOTS = 10.0` — enforced in `_clamp_volume()` at dispatch.
   - `MAX_TOTAL_EXPOSURE = 1` — one logical position at a time.
   - Free margin: margin required ≤ 20% of free margin.
   - Account tier caps (0.02 / 0.50 / 2.00 / 10.0 lots).
3. **Circuit breaker:** 3 consecutive broker rejections → `SAFE_MODE`, order
   dispatch halts. Never removed/reset without operator action.
4. **Pending-order cancellation is complete ONLY when the broker confirms
   removal.** `ACTIVE`/`UNKNOWN` broker state keeps the exposure slot locked;
   retcode 0 means "never reached the server" — the slot stays occupied until
   broker truth is queried (BUG-072/073/074). Retry bounded (≤3), idempotent.
5. **Position management rules:**
   - Emergency states (`LOSS_HARD_EXIT`, `PROFIT_GIVEBACK_CRITICAL`,
     `S09_CRITICAL_HOLD_SCORE_BREACH_BAILOUT`) are honored on FIRST
     observation; an emergency close of one leg closes ALL sibling legs
     sharing the originating order_id.
   - Exit timing derives from the CURRENT TICK timestamp, never host wall
     clock (clocks can be hours ahead — BUG-055).
   - Break-even / trailing labels require `was_sl_modified` proof —
     a stop that never moved is `HARD_SL_HIT`, never "breakeven" (BUG-081).
6. **Broker truth wins** over stale local state for exposure reconciliation
   (INV-011). `reconcile_missed_closes` restores the originating request_id
   from the immutable ledger OPENED row.
7. **Learning/reconciliation can NEVER block protective execution.** All such
   paths are exception-isolated, queued, or backgrounded.
8. **Emergency behavior:** broker disconnect → truthful
   `LIVE_CONFIGURED / MT5_DISCONNECTED` (never fake LIVE); guard telemetry
   counts duplicate/suppressed ticks; kill-switch and risk/survival alerts
   reach the operator via TelegramNotifier.

---

## 3. ML Safety Rules

1. **Training isolation:**
   - Candidate training writes ONLY candidate/staging artifacts
     (`model_generation/`, `model_lifecycle/` trainer paths). The Champion
     artifact path is NEVER touched by any training run (champion hash
     invariance is tested).
   - Training runs OFFLINE/BACKGROUND only — heavy PyTorch work NEVER inside
     `_process_tick_pipeline()`; workers via `asyncio.to_thread`.
   - A failed/interrupted run stays FAILED/INCOMPLETE — never VALIDATED.
2. **Validation requirements — no model enters production without:**
   - Backtest (deterministic, friction-aware)
   - Walk-forward (temporal folds, purge + embargo)
   - Out-Of-Sample (hard gate: OOS failure ⇒ REJECTED regardless of
     in-sample win rate)
   - Robustness (spread/slippage/latency stress → degradation measured)
   - Calibration (ECE ≤ 0.15), class-collapse protection (≤95% dominance),
     min-evidence floor (100 rows), OOS macro-F1 > 0.34 / balanced-acc
     > 0.34 floors (60D+ hardening)
   - The 12-gate model_lifecycle chain and/or the governance 14-gate
     verification.
3. **Dataset rules:**
   - Deterministic, causally safe, replayable: dataset == replay == live
     feature parity (INV-008/020). No future leakage: events/bars at-or-after
     decision time are invisible; triple-barrier horizon overlaps purged;
     embargo after validation folds.
   - News honesty: only events at-or-before the sample timestamp; no overlap
     ⇒ `NEWS_INCONCLUSIVE_NO_OVERLAP` (zero vector), never a fabricated
     signal.
   - Deterministic seeds (torch+NumPy BEFORE model construction — BUG-101);
     non-finite loss / exploding gradients (norm > 5) ⇒ FAILED.
4. **Model deployment rules:**
   - `SHADOW → CHAMPION` is an ILLEGAL transition. The only legal promotion
     path: `READY_FOR_REVIEW → APPROVED → CHAMPION` with an operator actor
     and approval token (INV-015). No automatic promotion exists anywhere.
   - Shadow candidates carry ZERO order authority, every decision is
     `simulated=True`, schema mismatches invalidate decisions (never compared).
   - Model loading passes the deterministic load gate (artifact/hash/manifest/
     schema/dimension/scaler/label/validation/lifecycle); the exact failing
     gate is reported (`MODEL_LOAD_REJECTED`), never a silent fallback.
   - Schema mismatch blocks: never reshape/truncate/pad (INV-009/020).
   - Drift detection alerts only — never auto-retrains, never self-tunes
     live parameters (INV-021).
   - Rollback restores the previous Champion and preserves evidence.
5. **Production inference requires NO database** (`LocalModelRuntime` proven
   with blocked sqlite import). Feature assembly does no DB on the tick path.

---

## 4. Performance Contracts

1. **Hot path (tick):** `_process_tick_pipeline` targets ≤ 50 ms per pulse
   (async design target — NOT a hard real-time SLA). It MUST be:
   - Zero synchronous DB (INV-001: all writes queued, caches only for
     news/rule-matrix/experience context).
   - No blocking I/O, no model training, no heavy aggregation.
   - Feature + regime computations CPU-bounded and bounded in time; liquidity
     swing detection bounded by `LIQUIDITY_HISTORY_LIMIT = 4000` (BUG-106).
2. **Async rules:**
   - Heavy work ALWAYS goes through `asyncio.to_thread` or dedicated worker
     threads: accounting/intelligence/research/training/shadow/news/hygiene/
     incidents/forensics workers — NEVER inline in the loop.
   - Telegram network I/O lives ONLY in the notifier worker — Telegram can
     never block the tick path.
   - No function-local `import time` in live_engine hot paths (BUG-074).
3. **Latency-sensitive areas:**
   - Inference: model call under `torch.inference_mode()`, scaler+model
     cached in the bundle with `_bundle_lock` for hot swap; `_infer_
     probabilities` stashes the post-scaler tensor for forensics (cheap).
   - Champion reads: fingerprint-cached (`st_size`, `st_mtime_ns`) — one
     verify per artifact change, never per poll (~2 Hz web polls) (BUG-118).
   - SSE/WS: incremental updates; enumeration serialization before broadcast;
     corrupted payloads surface `SSE_SERIALIZATION_ERROR` diagnostics.
4. **Memory considerations:**
   - Bounded structures everywhere: experience retrieval top-K bounded,
     `_pending_context_registry` cap 64 + TTL 3600 s, debug snapshot ring 64,
     shadow decision lists bounded, bar windows bounded (900-bar UI standard,
     4000-bar liquidity cap), purge in 500-row batches on a 6 h cadence
     (retention evidence windows: signals 7d, MOVING 3d, guard 13d, candle
     30d, cache 7d, active-state 1d, news health 90d, worker state 30d).
   - Workers are bounded/cancellable/restart-safe with persisted checkpoints.
5. **Failure isolation:** any background worker fault logs
   `[WORKER] event=FAILURE` and continues — a learning/research/news/shadow/
   hygiene/incident failure can NEVER stop trading.

---

## 5. Data / Persistence Honesty Contracts

1. **NO SYNTHETIC NUMBERS.** Metrics that cannot be derived from stored
   evidence are `None` (rendered "n/a"), never fabricated `0.0`. Applies to
   every API endpoint and the dashboard.
2. **One canonical owner per truth** (no parallel computation paths that can
   disagree): AccountingCore (PnL/periods/drawdown), RiskEngine (sizing),
   OrderManager (execution), schema_contract (70D), SettingsService
   (settings), `normalize_trade_row` (net PnL once).
3. **Idempotency everywhere:** deterministic dedup keys (`signal_dedup_key`,
   `idempotency_key`, article_hash, trade_id) + UNIQUE constraints +
   `ON CONFLICT DO NOTHING` — duplicate callbacks/replays collapse to one row.
4. **Historical evidence is immutable** (INV-007): corrections flow via
   reconstruction → derived record → provenance; never rewrite raw rows.
5. **Exit classification carries evidence provenance** (INV-013): every
   `exit_mechanism` has evidence_source + confidence; UNKNOWN stays UNKNOWN
   (INV-012); DEAL_REASON 4 = SL, 5 = TP (never inverted, BUG-088).
6. **Schema changes only via versioned migrations** (INV-013); migration-
   owned tables stay OUT of the schema manifest (INV-018); hygiene worker
   never drops schema; destructive DDL requires operator review.
7. **UI/Telegram never mutate canonical state** (INV-010): settings changes
   route via SettingsService (HOT_SAFE / HOT_RESTRICTED / RESTART_REQUIRED…),
   secrets via DPAPI SecureSecretStore — never live.yaml.
8. **Failure is never silent:** every MT5 call reports `[MT5_CALL]`
   operation/status/duration_ms/error; snapshots carry error_state; HTTP
   errors return the sanitized envelope + X-Request-ID (BUG-040).

---

## 6. Change Management Rules

Before modifying architecture (layers, contracts, ports, shared functions,
schema, model lifecycle, hot path, risk/execution semantics), the agent MUST:

```text
1. Check impact      — trace callers/consumers (git log, grep, contracts.md,
                       invariants); identify parallel-agent owners (locks.yaml).
2. Update documentation — claim a CHANGE-ID (change_control.md) + TASK-ID
                       (taskboard.md); update skill.md ADDITIVELY after
                       architectural changes; append bugs.md after real bugs;
                       handoff to docs/agent_handoffs/ for substantial work.
3. Run validation    — regression tests in the same commit; focused tests;
                       full beforePush gate (ruff + format + mypy + pytest
                       via .venv); CI-equivalent run.
4. Confirm no contract violation — walk agents/contracts.md + runtime_
                       invariants.md INV-001..021 + §1-§5 of THIS file.
```

**Required change records:**
- Shared function contract change → commit tag `SHARED API CHANGED` (old/new
  behavior + affected callers).
- Architectural change → commit tag `ARCHITECTURE CHANGE`.
- DB/schema → migration + idempotency + recovery documented.
- Model/feature → dimensions + schema version + compatibility + replay
  impact documented (governance/verify.py checks `liquidity_algorithm_version`
  provenance in manifests).
- LiveEngine/OrderManager/RiskEngine/MT5-adapter → hot-path impact, blocking
  behavior, failure isolation, broker interaction, safety implications.

**Forbidden changes (always):**
- Bypassing risk/execution protection to "fix" a symptom.
- Weakening validation gates to let a candidate through.
- Rewriting raw ledger/experience/broker truth.
- Adding DDL outside migration control.
- Auto-promoting a model (any path).
- Fake LIVE / fake data / fake VERIFIED status.
- Editing other agents' in-flight files without coordination (registries are
  additive shared space).

---

## 7. Verification Checklist (final)

- [ ] This contract reflects the REAL repository (no imaginary architecture).
- [ ] Consistent with `agents/skill.md` (forensic badges) and
      `agents/runtime_invariants.md` (INV-001..021).
- [ ] Every "cannot" is enforced by code or tests (e.g. no-bypass safety
      contract tests, champion-hash invariance tests, blocked-sqlite
      inference test, 70D parity suites).
- [ ] New agents can derive BOTH the map (PROJECT_GRAPH), the operating
      manual (AGENT_REASONING_PROTOCOL), and these laws from these three
      files.