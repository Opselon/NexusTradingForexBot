# src/nexus_scalp/web/server.py

- **PURPOSE:** The FastAPI control-center server — 7,310 lines, 168 routes
  (REST + SSE + WebSocket) exposing the entire engine: live state,
  accounting, experience/intelligence/research, model lifecycle +
  governance + shadow70, news, liquidity, debug/forensics, settings,
  engine control, chart history, UI assets. The dashboard's backend.
- **ARCHITECTURE LAYER:** Web/API (presentation). Reads engine state and
  canonical stores; only CONTROL actions (toggle/mode/close/modify) touch
  execution, always through the engine's sanctioned methods.
- **RESPONSIBILITY:** (a) `create_app(engine_ref)` — app factory wiring
  routes + middleware + static UI; (b) `serialize_enums(obj)` /
  `canonical_json(obj)` — the recursive Enum→str serializer EVERY
  response/SSE/WS payload must pass (the "Object of type ActionType is
  not JSON serializable" class of crash — BUG-guarded invariant);
  (c) `ServerState` — the shared live-state holder (bars, overlays,
  requests table, tick count, mode) updated by LiveEngine;
  (d) `StateVersioner` — monotonic state versioning for UI staleness
  detection; (e) error-safe route helpers shared with web/errors.py
  (never `except Exception: {"error": str(e)}` — the CodeQL
  stack-trace-exposure rule; all 500s become the safe envelope via
  middleware, X-Request-ID correlation preserved through
  Web/api_client.js); (f) the route surface (grouped below);
  (g) UI bundle serving: `_ui_bundle_sha256` fingerprint + forensic
  bundle-identity check (BUG-079: served bundle must match the repo);
  (h) SSE `/api/ticks/stream` (serialization-error-diag, sse_diag
  observability) + WebSocket `/web` `/ws`.
- **DEPENDENCIES:** fastapi/starlette, uvicorn, every subsystem facade
  (accounting core, experience, intelligence, research, model lifecycle,
  news, settings service, debug snapshot builder, forensics, hygiene,
  audit repo), Web/ static assets.
- **CONNECTS TO:** LiveEngine (state + control), the UI (Web/), Telegram
  (settings/testing), ALL subsystem APIs, tests
  (test_web_security, test_debug_snapshot_phase20, test_frontend_assets,
  integration API suites).
- **KEY CONCEPTS:**
  - Route families (168 total): UI/assets (/, app.js, api_client.js,
    tailwind, fontawesome) · live/status/health · db (hygiene/status) ·
    diagnostics (incidents/lineage/forensics/search/trace/report/zip) ·
    rules (get/toggle) · live state/accounting · account (summary/trades/
    growth/performance×4/equity-curve/drawdown/trades/{id}/strategies/
    performance/intelligence) · engine (toggle/mode) · config + runtime-
    config (get/diagnostics/apply) · settings + telegram · chart/history ·
    mt5/status (294 lines of broker truth incl. provider snapshots) ·
    algo config · positions (modify/close) · simulation/replay · debug
    (features/model-test/health/ipc-telemetry/state/snapshots/trace/
    compare) · experience · intelligence (timeline/autopsies/behavior/
    anomalies/evolution/self-heal) · research (25 routes: summary/
    registry/detail/trace/gates/events/evidence/worker/queue/analytics/
    preflight/retry-gate/cancel/discover/validate/self-heal/
    repair-outcomes) · models (summary/integrity/champion/challengers/
    runs/comparison/train/worker/shadow/shadow70/governance×20/
    promotion approve-rollback-execute/emergency freeze-unfreeze-disable)
    · news (17 routes) · liquidity (state/features/toggle) ·
    ticks/stream (SSE).
  - **No synthetic numbers:** every accounting/reporting response carries
    `available`/`has_data` flags and renders true absence; the wisdom
    "an unavailable metric renders as n/a, never as a fake zero" is
    enforced at the source (AccountingCore) and respected here.
  - **Control endpoints are thin wrappers** over engine methods
    (toggle/close/modify/mode/attach...) — the engine remains the single
    authority; the web layer never re-implements trading logic.
  - `/api/engine/mode` persists via settings_service (the BUG-119/INV-010
    path: UI mode selector → settings DB, never live.yaml) and the badge
    reflects the REAL runtime mode.
  - **Debug Hub** (`/api/debug/state` + snapshots ring + compare) is a
    PURE renderer of `build_debug_snapshot` — the JS never computes
    trading intelligence (documented contract).
- **HOT PATH / PERFORMANCE:** SSE/WS streams are the hot surface: bounded
  exponential reconnect, stale detection, sanitized error paths; the 900-
  bar chart window bounds payloads; `/api/news/keywords` is the known
  worst endpoint (keyword×article regex recompilation — P3, see issues
  ledger; fix documented in news-keywords-pattern-cache reference).
- **EDGE CASES & PITFALLS:**
  - Every new endpoint MUST pass payloads through `serialize_enums`/
    `canonical_json` — enum leaks are the most common route bug class.
  - Handler exceptions must use errors.py helpers (X-Request-ID envelope);
  - `create_app` wires shared state via app.state (background_tasks,
    debug_snapshot_store, sse_diag) — keep the naming stable (the
    sse_diag `version` NameError was a shipped bug, now fixed).
  - RUF006/E402 per-file ignores exist for this module (documented in
    pyproject) — don't "fix" them blindly.