# 🧠 AGENT REASONING PROTOCOL — Nexus Scalp Engine (NSE)

> **Purpose:** The operating manual for any AI agent working on this
> repository. It defines how the agent must THINK before, during, and after
> every change. This is a REAL trading system handling REAL capital — the
> agent is not merely a code generator; it is a caretaker of architecture,
> capital safety, and system truth.
>
> **Read first, in order:** `Agent/PROJECT_GRAPH.md` (map) →
> `Agent/ARCHITECTURE_CONTRACT.md` (laws) → this protocol (how to operate).
> The master architecture map `agents/skill.md` and bug ledger
> `agents/bugs.md` are mandatory context before ANY edit.
>
> This protocol is intentionally COMPATIBLE with (not a replacement for) the
> MASTER MULTI-AGENT CONTRACT v2 → `agents/multi-agent-git-contract.md` and
> the repo registries (`agents/contracts.md`, `agents/change_control.md`,
> `agents/taskboard.md`, `agents/runtime_invariants.md`,
> `agents/repository_state.md`, `agents/locks.yaml`, `agents/decisions/`).

---

## 1. Agent Mission

The agent is responsible for, in priority order:

1. **Preserving architecture.** Every change lands in the correct layer
   (Domain → Ports → Adapters → Features → Models → Training → Signals →
   Risk → Execution → Application → Web) and respects dependency direction.
2. **Protecting trading safety.** Capital protection and risk correctness are
   NON-NEGOTIABLE. `RiskEngine` and `OrderManager` are authoritative
   boundaries — nothing bypasses them (INV-003/004).
3. **Understanding consequences.** Before editing any shared function, trace
   its consumers (`git log -- <file>`, `git blame`, grep call sites). A
   signature or semantic change ripples across subsystems (ports → adapters →
   callers).
4. **Finding root causes.** Fix the CAUSE, not the symptom. The bug ledger
   (`agents/bugs.md`, 5,200+ lines) is the institutional memory: check whether
   the symptom you see is a known bug class before re-diagnosing. Use the
   debugging methodology in §3.
5. **Improving system quality.** Add regression tests with every fix,
   maintain observability, keep the gates green (beforePush), and keep
   documentation truthful (never mark VERIFIED what you did not verify).

The ABSOLUTE DIRECTIVE from `agents/skill.md`: **READ-ONLY for codebase files
except `agents/skill.md` and `agents/bugs.md`** — meaning: never modify
repository files for the sake of it; every modification must be a deliberate,
justified, contract-compliant change. Registries (contracts/taskboard/change_
control/repository_state/locks) are ADDITIVE — append your rows, never rewrite
others'.

---

## 2. Required Reasoning Process

Before EVERY change, walk this sequence. Skipping a step is a defect.

```text
1. Understand the problem
2. Locate affected architecture layer
3. Trace data flow
4. Identify dependencies
5. Evaluate side effects
6. Implement minimal safe change
7. Validate behavior
8. Document change
```

### Step 1 — Understand the problem
- Reproduce or precisely describe the observed behavior first.
- Search history before reimplementing: `git log -S<symbol>`,
  `git grep`, `agents/bugs.md` (grep `^## BUG-` tail FIRST — parallel agents
  write BUG-NNN concurrently; claim the next FREE number at write time).
- REUSE > REFACTOR > CREATE.

### Step 2 — Locate the affected architecture layer
- Use the layer map in PROJECT_GRAPH §2 / skill.md §1. Which layer owns this
  behavior? Is your change IN that layer, or does it cross a boundary?
- Cross-layer changes are architectural changes: they need a CHANGE-ID
  (`agents/change_control.md`), a taskboard claim (TASK-ID), and — for shared
  function contract changes — the `SHARED API CHANGED` commit tag.

### Step 3 — Trace data flow
- What are the inputs, transformations, and outputs of the code you touch?
- For trading paths: tick → features → decision → risk → execution → ledger →
  telemetry → learning. Identify WHERE in this chain your change sits.
- For DB schema: the ONLY legal change path is a versioned migration
  (INV-013) — never DDL outside migration control.

### Step 4 — Identify dependencies
- Who calls this? Who is called? What contracts are consumed?
  (`agents/contracts.md` index). What invariants constrain it?
  (`agents/runtime_invariants.md`, INV-001..021).
- Ports: any `IMT5Port` signature change must update DirectMT5Adapter,
  RemoteMT5GatewayAdapter, AND PaperMT5Adapter.

### Step 5 — Evaluate side effects
- Hot path? (`_process_tick_pipeline`) → INV-001: zero synchronous DB, no
  blocking I/O, no model training. Offload via `asyncio.to_thread`, queues,
  or caches.
- Execution/risk touched? → state hot-path impact, blocking behavior, failure
  isolation, broker interaction, safety implications (multi-agent contract §9).
- ML/features touched? → document dimensions, schema version, compatibility,
  replay impact, dataset/live parity. News Intelligence? → provider reuse
  (Factory `complete_json`), prompt grounding + injection defense, response
  validation, separate AI table (never overwrite deterministic truth),
  recoverable status + audit, bounded batch. Forensic UI? → `NX.api` only,
  `normalizeError` (no raw fetch text in DOM), KPIs via `deriveKpis` from
  ONE array, distinct loading/empty/error/loaded, concurrency/dedup guards.
- Parallel agents? → other sessions edit the same tree right now. Preserve
  unknown WIP; never `reset --hard` / `clean -fd` on unknown work; re-check
  `git status` immediately before staging.

### Step 6 — Implement minimal safe change
- Small coherent commits at architectural boundaries (never one giant commit).
- Follow repo conventions: frozen Pydantic domains (`model_copy`), enum
  serialization in web responses, bitwise Polars operators, no function-local
  `import time` in live_engine hot paths (BUG-074).
- CRLF discipline: this repo is mostly CRLF and the patch tool mis-applies
  edits on CRLF files — use byte-exact replaces via execute_code, verify with
  `py_compile`, re-read before re-patching. Web/index.html is LF; Web/app.js
  is CRLF.
- Never add hidden coupling, duplicate business logic, or bypass risk
  controls (see §4).

### Step 7 — Validate behavior
- Run the focused test file, then the full gate (beforePush):
  ruff check + ruff format + mypy src + pytest tests/unit, always via
  `.venv/Scripts/python.exe -m <tool>`.
- Tests: EXTEND the existing test file that covers the module (user
  preference) — new file only when no home exists; regression guards for bugs.
- Added observability: structured logs with event tags (e.g. `[POSITION_EXIT_EVAL]`,
  `[EXPERIENCE_OUTCOME] event=...`) that mirror the existing vocabulary.
- Report verification HONESTLY: VERIFIED / PARTIALLY VERIFIED / NOT VERIFIED
  in the commit body — never claim verified when it isn't.

### Step 8 — Document change
- Commit contract (see §6): `<AGENT-NAME>: <imperative summary>`, body with
  Agent/Role/Scope/Why/Implementation/Verification/Risk/Handoff; tags
  `SHARED API CHANGED` / `ARCHITECTURE CHANGE` when applicable.
- Append to the right registries; update `agents/skill.md` additively after
  architectural changes; append to `agents/bugs.md` after real bugs; write a
  handoff doc to `docs/agent_handoffs/` for substantial work.

---

## 3. Debugging Methodology

Follow this chain; do not jump from Symptom to Fix.

```text
Symptom
   │
   ▼
Reproduction
   │
   ▼
Data Flow Analysis
   │
   ▼
Root Cause
   │
   ▼
Fix
   │
   ▼
Regression Prevention
```

### Symptom
- Capture the EXACT observed behavior, timestamps, log lines
  (`[MT5_CALL]`, `[POSITION_EXIT_EVAL]`, `[EXPERIENCE_OUTCOME]`, `[NEWS_AI]`,
  `[NEWS_PRUNE]`, `[UI_ERROR]` with `component/action/endpoint`, structured
  fields), and DB state (including `news_ai_analysis`, `news_prune_audit`,
  `article_status`). A symptom without evidence is a rumor.
- For News/Forensic symptoms, capture the AI status (`GET /api/news/ai-status`
  — NOT_CONFIGURED/AVAILABLE/UNAVAILABLE/MISCONFIGURED), per-article
  `analysis_status` (completed/completed_insufficient/failed/reused), and the
  frontend state machine (idle/analyzing/done vs loading/empty/error).
- Check `agents/bugs.md` for the same symptom class FIRST (e.g. BUG-054
  payload contracts, BUG-072/074 exposure locks, BUG-081 exit classification,
  BUG-088 DEAL_REASON inversion, BUG-111 wall-clock timestamps, BUG-118
  champion caching, BUG-119 mode lifecycle, BUG-122 frozen cp1252). For News
  Intelligence / Forensic UI, also check: `news/ai_service.py` validation &
  provider reuse (never a second LLM config), `news/database.py` recoverable
  `article_status` + `news_prune_audit`, `Web/forensic_console.js` single-source
  KPI derivation + `normalizeError`, `Web/news_intelligence.js` state machine
  + bounded batch + auto-prune recoverability.

### Reproduction
- For frontend symptoms (KPI impossible state, `TypeError: Failed to fetch`
  in DOM, modal focus loss, filter divergence), reproduce via the browser
  console: inspect `NX.Forensic.model.deriveKpis(INC_STATE.incidents)` vs the
  header, check `NX.Forensic.normalizeError` paths, and verify `requestSeq`
  / `analyzing[articleId]` dedup — never patch by hand-editing the DOM.
- Write a minimal probe in `scratch/` (name it VERB_WHAT:
  `probe_*` / `repro_*` / `root_proof_*`; keep probes and their output
  OUT of the repo root; scratch/ is excluded from ruff — do not leave
  lint-breaking probes behind). Reproduce deterministically where possible.
- Note REPRO-UNREPRODUCIBLE honestly when the environment cannot be
  replicated (e.g. no real MT5): classify the finding, don't fake it.

### Data Flow Analysis
- Trace the path: which subsystem produced the input? Which transformed it?
  Where was it persisted? Which consumer read it?
- Use the canonical lineages:
  - Trade identity: request_id → `_pending_context_registry` → order_id →
    ledger → experience `exp_<order_id>` → outcome `execution_id`
    (ledger.ticket == outcomes.execution_id; outcomes.idempotency_key ==
    experiences.idempotency_key).
  - Feature: bars+tick → 50D/70D vector → decision → audit signal row
    (payload = exactly 8 fields).
  - Exit: broker DEAL → classify_exit_with_evidence → ledger +
    telegram canonical close.
- Identify the FIRST divergence from expected truth (value lineage: incidents/
  lineage.py, forensics experience-gap, news `news_ai_analysis` vs
  deterministic `news_analysis` + `news_prune_audit` trail, forensic
  `deriveKpis` vs stale separate counters).

### Root Cause
- The root cause is the earliest point where reality diverged — not the
  nearest logger. Examples from the ledger: a function-local `import time`
  froze the exposure cache (BUG-074); inverted DEAL_REASON mapping labeled
  TP as SL (BUG-088); wall-clock timestamps rendered 1970 in the UI
  (BUG-111); history blind-append minted duplicate bars after downtime
  (BUG-058).
- Distinguish BROKER truth vs LOCAL state vs DERIVED truth. Broker wins
  (INV-011); derived tables are rebuildable; raw evidence is immutable
  (INV-007). For News Intelligence: deterministic truth (`news_analysis`)
  vs AI interpretation (`news_ai_analysis`) — AI never overwrites truth;
  recoverable `ACTIVE/IRRELEVANT` with audit, never deletion. For Forensic:
  authoritative list vs derived KPIs — never dual counters; raw fetch errors
  vs normalized toasts — never `String(e)` in DOM.

### Fix
- Minimal, layered, in the correct owner: risk fixes in RiskEngine, execution
  fixes in OrderManager, learning fixes never touch execution authority.
- Preserve the feature you are securing — a mitigation that kills the
  feature's purpose is the wrong mitigation.
- Include the regression test IN THE SAME COMMIT.

### Regression Prevention
- Add the failing case to the existing test file for that module (or name it
  `test_<module>_bugNNN.py` for a bug-specific guard).
- Append the BUG-NNN entry to `agents/bugs.md` with root cause chain,
  evidence, and regression guard names.
- If the fix revealed a new invariant, propose it as INV-NNN (review +
  DEC-XXXX for changes).
- Re-run the full gate and the CI-equivalent command
  (`pytest --ignore=tests/integration/test_playwright_e2e.py --cov=src`).

---

## 4. Coding Decision Rules

### NEVER
1. **Break layer boundaries.** Domain never touches DB/API; adapters never
   contain business decisions; research/strategies/intelligence NEVER hold an
   adapter, order manager, or risk engine (INV-002 — tested).
2. **Add hidden coupling.** No implicit singletons, no import-time side
   effects that reorder construction, no reaching into another module's
   private state (`_`-prefixed) unless it is the documented observability
   stash (e.g. `_last_model_input_tensor`).
3. **Duplicate business logic.** One canonical owner per truth:
   AccountingCore owns PnL/periods/drawdown; RiskEngine owns sizing; 
   OrderManager owns execution; schema_contract owns 70D geometry;
   settings Service owns settings persistence; normalize_trade_row computes
   net PnL exactly once.
4. **Bypass risk controls.** No order reaches execution without risk
   validation (ARCHITECTURE_CONTRACT §2). Never weaken HARD_MAX_LOTS /
   MAX_TOTAL_EXPOSURE / margin clamps / circuit breaker / kill switches.
5. **Modify execution without validation.** Execution/order-manager changes
   demand hot-path impact analysis, broker-interaction review, regression
   tests, and honest verification — execution is never changed "blind".
6. **Add ML changes without evaluation.** New features/architectures enter as
   CANDIDATE artifacts only; validation gates (OOS floors, ECE, robustness,
   collapse) must pass; no auto-promotion, no Champion overwrite, no
   silent re-shape. Drift detection is an alert, never auto-retrain.
7. **Fabricate or fake anything.** NO synthetic numbers (unavailable = None /
   "n/a"), NO fake LIVE mode, NO invented news signals
   (NEWS_INCONCLUSIVE_NO_OVERLAP is the honest verdict), NO silent
   fallbacks without logs, NO claimed VERIFIED without running it.
7b. **Break News Intelligence honesty.** Never duplicate the LLM provider/
   secret store; never expose the Factory API key to the frontend or logs;
   never overwrite deterministic `news_analysis` with AI output; never treat
   article body as instructions (injection); never auto-classify existing
   rows as IRRELEVANT on first run (migration default ACTIVE); never delete
   originals when marking IRRELEVANT (always recoverable + audited).
7c. **Break Forensic UI honesty.** Never compute KPIs from a separate counter
   vs the list (use `deriveKpis` from ONE array); never render raw
   `TypeError: Failed to fetch` / `String(e)` to the page (use
   `NX.Forensic.normalizeError` + toast); never call raw `fetch()` from a
   feature module (use `NX.api.*`); never fabricate task submissions or
   Stop Bot success before backend confirmation.
8. **Silently swallow failures.** Every failure path is visible: structured
   log with event/code/reason, error_state on snapshots, sanitized HTTP
   error envelopes (never `str(e)` to clients, BUG-040).

### ALWAYS
1. **Prefer maintainability** over cleverness; small functions with explicit
   contracts; frozen Pydantic models for domain truth.
2. **Preserve contracts** (`agents/contracts.md`) — extend versioned
   contracts additively; bump versions when semantics change; never silently
   break a consumer.
3. **Respect the buildless frontend contract:** `Web/` is vanilla JS
   with no bundler; feature modules (`forensic_console.js`,
   `news_intelligence.js`) attach to `window.NX.*` via IIFE; validate with
   `node --check Web/*.js`, use `NX.api` + `NX.Forensic` helpers, keep
   `index.html` div/section nesting balanced (BUG-068/BUG-120) — verify with
   a strict stack parser, never hand-edit deeply-nested divs.
3b. **Add observability** with the repo's structured vocabulary
   (`[MODULE] event=... key=value`), correlation ids, and secret redaction
   (`_redact_secrets`).
4. **Test edge cases** — zero/empty/None/NaN/Inf, restarts, parallel agents,
   broker disconnects, split fills, duplicate callbacks: the ledger proves
   the system's edge-case history.
5. **Check beforePush** with the project venv
   (`.venv/Scripts/python.exe -m ruff|mypy|pytest`) and report gate status
   truthfully even when blocked by parallel WIP.
6. **Boot-time truth**: read the registries (taskboard/locks/change_control/
   repository_state) and `git status` BEFORE claiming scope.

---

## 5. Trading System Priorities

When two goals conflict, resolve in this order — this is a capital-handling
trading system, and every trade-off decision must be traceable to it:

```text
1. CAPITAL PROTECTION      — never risk what cannot be afforded; margins,
                            tiers, exposure caps, kill switches, safe defaults.
2. RISK CORRECTNESS        — sizing, SL/TP geometry, exit classification,
                            drawdown control must be mathematically right.
3. EXECUTION RELIABILITY   — orders must reach the broker exactly once,
                            verified; cancellations verified; reconciliation
                            with broker truth; no ghost exposure slots.
4. SIGNAL ACCURACY         — model/features must be honest, causally valid,
                            calibrated; validation gates before promotion.
5. PERFORMANCE OPTIMIZATION— 50 ms tick target, memory discipline, no event
                            loop blocking — ALWAYS last; never at the cost
                            of 1-4.
```

Concrete consequences:
- A validation-gate failure wins over a faster training loop.
- A 50 ms latency target does NOT justify skipping risk validation.
- A prettier dashboard never justifies recomputing truth in JS.
- An optimizer never self-tunes live parameters (INV-021).
- A blocked update never interrupts LIVE trading without --force (INV-014).

---

## 6. Commit & Collaboration Protocol (condensed)

Full text: `agents/multi-agent-git-contract.md` (mandatory read).

1. **Boot**: `git status --short`, `git branch`, `git log -10 --oneline`,
   read `agents/skill.md` + `agents/bugs.md`; claim a TASK-ID on the
   taskboard if substantial; check `agents/locks.yaml`.
2. **Identity**: commit messages start `<AGENT-NAME>: <imperative summary>`
   (role-based name, e.g. Hermes-Accounting). Body carries Agent, Role,
   Scope, Why, Implementation, Verification (VERIFIED/PARTIALLY VERIFIED/
   NOT VERIFIED), Risk, Handoff. Tags: `SHARED API CHANGED`,
   `ARCHITECTURE CHANGE`.
3. **Commit-per-step**: implement → test → commit → push → verify (user
   mandate 2026-08-19). Never one giant commit. Re-`git add` immediately
   before `git commit` (parallel `restore --staged` can empty the index);
   verify `git diff --cached --name-only` right before committing.
4. **Parallel hazards**: other agents may ABSORB your staged/untracked work
   into their commits — verify `git show <sha>:<file>` / `git log --all -- <path>`
   before re-doing work; registry rows are additive shared space (re-check
   tails before writing, never restore-over another agent's rows).
5. **Substantial work ends with** the AGENT HANDOFF block (Agent/Role/Task/
   Starting+Ending HEAD/Branch/Commits/Files/Functions/Shared/Architecture/
   New Invariants/Tests/Runtime Verification/GitHub status/Bugs/Known Risks/
   Unfinished/EXACT NEXT-AGENT INSTRUCTIONS) + PR-ready summary.
6. **After any commit/push on this repo, report to the user on Telegram**
   (Persian, structured, with SHA, files, push result, remote verification).

---

## 7. Verification Checklist (before declaring done)

- [ ] Problem understood and reproduced (or honestly classified).
- [ ] Bug ledger grepped; BUG-NNN claimed/appended correctly (or confirmed
      existing entry covers it).
- [ ] Affected layer + dependencies traced; contracts/invariants checked.
- [ ] Change minimal, layered, no bypasses, no duplication.
- [ ] Regression test added to the RIGHT file (extend existing suite).
- [ ] Focused tests + full beforePush gate run with `.venv` toolchain.
- [ ] Registries updated additively (change_control/taskboard/repository_
      state/locks as applicable); skill.md/bugs.md appended if warranted.
- [ ] Commit contract followed; verification state honest.
- [ ] Pushed and verified at origin (fetch + `git log origin/main..HEAD`).
- [ ] Telegram report sent (structure per user preferences).