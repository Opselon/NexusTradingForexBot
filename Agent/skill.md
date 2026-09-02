# 🧭 Agent Skill — Nexus Scalp Engine (NSE)

> **What this file is:** a concise, **upgraded** entry point for any AI agent
> that landed in `Agent/` (capital A). The **canonical** master map is
> `agents/skill.md` (forensic badges, §1-§20 — read it first). This file
> resolves the `Agent/` vs `agents/` confusion you found and records the
> upgrades made on 2026-08-22 so every agent inherits them.
>
> **Read order:** `agents/skill.md` (canonical) → `Agent/PROJECT_GRAPH.md`
> (full map) → `Agent/ARCHITECTURE_CONTRACT.md` (LAWS) →
> `Agent/AGENT_REASONING_PROTOCOL.md` (operating manual) → this file (what
> changed).

---

## 0. Path contract (fixes the `Agent/` vs `agents/` split)

- **`agents/` (lowercase, repo root):** canonical registry. Contains
  `skill.md` (2,900+ lines, forensic badges), `bugs.md` (BUG-NNN ledger),
  `multi-agent-git-contract.md`, `contracts.md`, `runtime_invariants.md`
  (INV-001..021), `change_control.md`, `taskboard.md`, `repository_state.md`,
  `locks.yaml`, `decisions/`, `hermes-kanban-swarm.md`.
- **`Agent/` (capital A, repo root):** upgrade layer. Contains
  `PROJECT_GRAPH.md` (system map), `ARCHITECTURE_CONTRACT.md` (laws),
  `AGENT_REASONING_PROTOCOL.md` (how to think), `TEST_OPTIMIZATION_REPORT.md`,
  `DATABASE_MIGRATION_STATUS.md`, and **this file** `skill.md`.
- **Rule:** always read `agents/skill.md` first (it is the executable-truth
  map). `Agent/skill.md` is an **alias + upgrade log** — it never replaces
  the canonical file. Commit messages follow the Hermes contract:
  `Hermes-<Role>: <imperative>` with body `Agent/Role/Scope/Why/…`.

---

## 1. What was upgraded (2026-08-22 — News Intelligence 0100 + Forensic Incident Center overhaul)

This upgrade synced `Agent/` docs to the live codebase at 2026-08-22.
Below is the SHORT skill entry every agent inherits (absolute directive).

### 1.1 News Intelligence 0100 — architecture

- **Decision:** AI is an *interpretation layer* (`news/ai_service.py`), not a
  replacement for the deterministic News Engine. Isolation is structural:
  `news_ai_analysis` (AI) vs `news_analysis` (deterministic) — never overwrite.
- **Single LLM source:** `strategies/factory/provider.py::LLMGenerationProvider.complete_json`
  is the ONLY LLM path (reused, not duplicated). `resolve_factory_provider`
  prefers the live engine's `strategy_factory.provider` (hot-reload aware);
  fallback builds from `SettingsService.get_factory_llm_config()`. One key,
  one store, one config (`analysis_version=news-ai-v1`, rule `news-prune-v1`).
- **Prompts:** injection-defended (`<<<ARTICLE_START>>>` DATA delimiters +
  system prompt labels article as UNTRUSTED EXTERNAL DATA); deterministic
  context (importance_score, xauusd_relevance, direction, entities/topics)
  passed as trusted CONTEXT. Body cap 4000 chars, `temperature=0.2`,
  `max_tokens=1200`, `response_format=json_object`.
- **Validation:** `_validate_response` normalizes sentiment to
  BULLISH/BEARISH/NEUTRAL/MIXED, caps key_facts/uncertainties at 20, requires
  content or `insufficient_evidence=true`; malformed → `analysis_status='failed'`.
- **Persistence:** `news/database.py` adds `news_ai_analysis` + `news_prune_audit`
  + `article_status` ACTIVE/IRRELEVANT (migration-safe: `ALTER TABLE … DEFAULT 'ACTIVE'`
  before indexes; never auto-classify existing rows). `set_article_status`
  is idempotent + audited (`pau_*`); `count_articles_by_status` for
  `status_counts`; `list_articles(status_filter)` for `GET /api/news?status=`.
- **Runtime config:** `configuration/runtime_config.py` `news.auto_analysis_enabled`
  (bool, default `False`, validated+coerced, persisted in the runtime snapshot;
  deterministic local-only when enabled — no external LLM unless Factory is configured).
- **Batch:** bounded concurrency `NEWS_AI_BATCH_CONCURRENCY=3` via ThreadPoolExecutor,
  cap 200 ids, per-item isolation (one failure never fails the batch).

### 1.2 News Intelligence 0100 — API & UI

- **Routes (`web/news_intelligence_routes.py`, mounted in `web/server.py`):**
  `GET /api/news/ai-status` (secret-free: NOT_CONFIGURED/AVAILABLE/UNAVAILABLE/
  MISCONFIGURED), `POST /api/news/analyze/{id}`, `POST /api/news/analyze/batch`,
  `POST /api/news/auto-prune` (recoverable, audited, explainable
  `importance < 0.30 AND xauusd_relevance < 0.25`), `POST /api/news/{id}/restore`,
  plus `GET /api/news?status=ACTIVE|ALL|IRRELEVANT` (includes `status_counts` +
  per-article `ai_analysis`). Never exposes the API key; never raises.
- **Frontend (`Web/news_intelligence.js` + `Web/app.js` + `Web/index.html`):**
  `window.NewsIntel` (state `{aiStatus, filter, analyzing, batchRunning,
  pruneRunning}`): AI banner (NOT_CONFIGURED CTA → Strategy Factory), per-article
  state machine + in-flight dedup, batch progress, Pro auto-prune with confirm,
  Active/All/Irrelevant filter tabs, `status_counts`, `articleExtrasHTML` card
  enrichment, restore. `loadNewsFeed` now routes via `NX.api.get` with status
  param and `NewsIntel.renderStatusCounts` / `articleExtrasHTML`.

### 1.3 Forensic Incident Center overhaul — architecture + UI law

- **File:** `Web/forensic_console.js` (buildless vanilla JS, IIFE onto `window.NX.Forensic`).
- **Infra:** toast region (ARIA live, stack, auto-dismiss), `normalizeError`
  (maps `TypeError: Failed to fetch` → friendly network message + `console.warn`
  diagnostic, never raw text in DOM), `apiSafe` helper, modal (focus trap/Escape/
  backdrop/focus-restore), `withButtonLock` (duplicate-click guard).
- **Model:** single authoritative incident array; `normSeverity`/`normStatus`
  single normalization boundary; `deriveKpis(incidents)` derives ALL header KPIs
  from the rendered list (fixes the OPEN=2/CRITICAL=1/HIGH=1/MEDIUM=3 impossible
  state); `getFilteredIncidents` never copies. `OPEN_STATUSES` / `RESOLVED_STATUSES`
  are the canonical vocab.
- **Agent Mode:** state machine OFF/IDLE/TRACING/ANALYZING/GENERATING_TASK/RESOLVING/
  ERROR; auto-trace eligible open incidents via real `/api/diagnostics/trace`
  (deduped by `INC_STATE.agentProcessed`), never fabricated.
- **Task Generation:** drawer (review-before-submit) from REAL incident evidence
  (ids/timestamps/symptoms/impact — never invented); provider surface truthful
  (`configured:false` until backend wired; never fabricates success).
- **Safety:** Stop Bot modal requires typing `STOP` (case-sensitive); `confirmStopBot`
  only fires after confirmation; halt is `engine._running=False` (does NOT cancel
  broker pending orders — truthfully documented).
- **Performance/correctness:** `requestSeq` concurrency guard, skeleton loaders,
  filter tabs (Open/Resolved/Resolved by Agent), Worker health hierarchy
  (Status/Cycles/Last OK), never dual counters.
- **Upgraded pages:** `Web/app.js` now owns `INC_STATE` + `renderIncidentKpis` +
  `renderWorkerHealth` + `loadIncidents` (via `NX.api`) + `getFilteredIncidents` /
  `setIncidentFilter` + `renderIncidentList` + `maybeAutoTraceEligible` +
  `agentTraceIncident` + `toggleAgentMode` + `TASK_DRAWER_CTX` + Stop Bot +
  Task drawer (+ `initApp` stop-input/task-backdrop wiring, bid/ask split);
  `Web/index.html` adds Stop Bot modal + Task drawer + `forensic_console.js` +
  `news_intelligence.js` + header BID/ASK split + News AI status/filter/Pro
  controls + Incident filter tabs + `NXDropdown` helper (all verified via
  `git diff HEAD -- Web/*`).

---

## 2. Where it lives in Agent/PROJECT_GRAPH.md

- `§1.9 Telemetry` — now documents the 5 news-intelligence endpoints,
  `status` filtering, `status_counts`/`ai_analysis`, and the two companion
  modules.
- `§1.10 Learning Loop` — news bullet expanded to Phase 12 + Intelligence
  0100 with files, versions, thresholds, and the `auto_analysis_enabled` flag.
- `§2.8 UI` — expanded to `Web/forensic_console.js` + `Web/news_intelligence.js`,
  `NX.api` safe envelope law, and `deriveKpis` single-source KPI rule.
- `§3.5b` (NEW) — four algorithms: `A20b` News AI Service, `A20c` Recoverable
  Auto-Prune, `A20d` News Intelligence API Surface, `A20e` Forensic Incident
  Center Overhaul (with inputs, processing, outputs, and failure isolation).
- `§12 Credits` — notes both canonical maps + the new sections.

## 3. Where it lives in Agent/ARCHITECTURE_CONTRACT.md

- **Layer Rules:** Learning layers row now covers `news/ai_service.py`
  Intelligence 0100 (reuse, grounded prompts, validated `news_ai_analysis`,
  recoverable `article_status`); Web/API row now covers
  `forensic_console.js`/`news_intelligence.js` and the `NX.api`/`deriveKpis` laws.
- **§5 (new 8) Persistence:** News Intelligence honesty — separate AI table
  (`news-ai-v1`), recoverable `ACTIVE/IRRELEVANT` + `news_prune_audit`, never
  delete originals; `GET /api/news?status=` + `status_counts`; secret-free
  `ai-status`.
- **§5b (NEW) News Intelligence & Forensic UI Contracts:** six laws — single LLM
  source, grounded injection-defended prompts, response validation, recoverable
  classification, forensic truthfulness (`deriveKpis` + `NX.api` + distinct
  states + concurrency/dedup), Agent Mode & Task honesty, Stop Bot semantics.

## 4. Where it lives in Agent/AGENT_REASONING_PROTOCOL.md

- **Mission & §2 Step 5:** News Intelligence / Forensic now first-class in the
  mental model (provider reuse, prompt grounding, separate AI table, recoverable
  status + audit, bounded batch; forensic: `NX.api` only, `normalizeError`,
  `deriveKpis` single array, concurrency/dedup, Stop Bot semantics).
- **§3 Debugging:** symptom captures `[NEWS_AI]`/`[NEWS_PRUNE]`/`[UI_ERROR]`,
  `ai_analysis` + `news_prune_audit` + `article_status`; `GET /api/news/ai-status`
  and per-article `analysis_status`; lineage includes news/forensic trails;
  root causes distinguish deterministic vs AI truth and list vs KPI divergence.
- **§4 NEVER/ALWAYS:** new laws `7b` (News Intelligence honesty) and `7c`
  (Forensic UI honesty) + buildless frontend contract (`node --check`,
  `NX.api`/`NX.Forensic` helpers, div/section nesting via parser).

---

## 5. Quick reference (what to grep)

| Concern | Grep / file |
|---|---|
| AI status (secret-free) | `src/nexus_scalp/news/ai_service.py::get_ai_status`, `GET /api/news/ai-status` |
| Per-article AI | `analyze_article_with_ai`, `POST /api/news/analyze/{id}`, `Web/news_intelligence.js::analyzeArticle` |
| Batch (bounded) | `NEWS_AI_BATCH_CONCURRENCY`, `POST /api/news/analyze/batch`, `Web/news_intelligence.js::batchAnalyze` |
| Prune / restore | `auto_prune_irrelevant`, `restore_article`, `article_status`, `news_prune_audit`, `POST /api/news/auto-prune`, `POST /api/news/{id}/restore` |
| Tables | `news_ai_analysis`, `news_prune_audit`, `news_articles.article_status` in `src/nexus_scalp/news/database.py` |
| Runtime flag | `configuration/runtime_config.py::news.auto_analysis_enabled` |
| Provider reuse | `strategies/factory/provider.py::complete_json` |
| Forensic model | `Web/forensic_console.js::NX.Forensic.model.deriveKpis` |
| Forensic errors | `Web/forensic_console.js::NX.Forensic.normalizeError` (never raw fetch in DOM) |
| Forensic routes | `Web/app.js::loadIncidents`, `INC_STATE`, `requestSeq`, `withButtonLock` |
| API envelopes | `web/news_intelligence_routes.py`, `web/server.py::serialize_enums`, `web/errors.py` |

---

## 6. Verification

- `git diff HEAD -- src/nexus_scalp/news/ai_service.py src/nexus_scalp/news/database.py src/nexus_scalp/configuration/runtime_config.py src/nexus_scalp/strategies/factory/provider.py src/nexus_scalp/web/news_intelligence_routes.py src/nexus_scalp/web/server.py Web/app.js Web/index.html Web/forensic_console.js Web/news_intelligence.js` — all diffs reviewed.
- `python -m py_compile` on every touched Python file (below) — must pass.
- Frontend: `node --check Web/app.js Web/forensic_console.js Web/news_intelligence.js` — must pass.
- Invariants: no second LLM config, no key in response, AI table separate, recoverable status + audit, `NX.api` only, `deriveKpis` single source.

---

*This file is intentionally concise. For the full forensic depth, read `agents/skill.md` and the three `Agent/*.md` companions — they remain the authoritative docs.*
