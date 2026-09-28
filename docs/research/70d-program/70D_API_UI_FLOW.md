# 70D API / UI Flow (TASK-70D-SYSTEM-FLOW-FORENSICS)

> Agent: Hermes-Forensic-70D · 2026-08-19

## 1. UI element → endpoint → backend map

| UI element | Endpoint | Backend function | DB/cache | Response → rendered |
| :--- | :--- | :--- | :--- | :--- |
| Dashboard snapshot | GET /api/status | get_system_state | engine state + audit | LiveUiState sections |
| Live state (canonical) | GET /api/live/state | get_live_state | engine state | contract LiveUiState.2 |
| Chart candles | GET /api/chart/history | get_chart_history | MT5 + aggregator | bars + overlays |
| MT5 status | GET /api/mt5/status | get_mt5_status | broker snapshots | connection/account/positions |
| Liquidity state | GET /api/liquidity/state | get_liquidity_state | governor report | enabled/status/10 values |
| Liquidity toggle | POST /api/liquidity/toggle | set_liquidity_toggle | SettingsService persist | new status |
| Liquidity features | GET /api/liquidity/features | get_liquidity_features | governor snapshot | 10 values + schema |
| News state | GET /api/news/state | get_news_state | news context cache | state + scores |
| News feed | GET /api/news | list | news.db | articles |
| Research summary | GET /api/research/summary | get_research_summary | registry_summary | counts + worker |
| Research health | GET /api/research/health | get_research_health | research_health_summary | WHY-empty evidence |
| Shadow70 summary | GET /api/models/shadow70/summary | get_shadow70_summary | runtime + store | runtime/worker/drift |
| Shadow70 attach | POST /api/models/shadow70/attach | attach_shadow70 | registry + artifact | load verdict |
| Rules | GET/POST /api/rules(+/toggle) | get_trading_rules/toggle | trading_rules_config | matrix |
| Forensics health | GET /api/forensics/health | get_forensic_health | checks engine | check matrix |
| DB status | GET /api/db/status | get_db_migration_status | migration engine | per-domain versions |
| Diagnostics | GET /api/diagnostics/* | incident store | audit.db incidents | incidents/lineage |
| SSE | GET /api/ticks/stream | sse_telemetry_stream | engine state | state/tick/heartbeat |

## 2. 200-but-wrong checks (semantic validation)

| Endpoint | Risk | Verdict |
| :--- | :--- | :--- |
| /api/research/health | 200 + 0 strategies while registry has rows | 🟢 health EXPLAINS why (evidence, eligibility, rejection reasons) |
| /api/chart/history | 200 + empty chart when broker history exists | 🟢 source field + error state; ENGINE_STATE fallback explicit |
| /api/liquidity/state | 200 + zero liquidity | 🟢 status UNAVAILABLE + reason; never fake zeros |
| /api/status model | 200 + default model ID | 🟢 model metadata from champion manager |
| /api/live/state news | 200 + stale timestamps | 🟢 real context with timestamp; default disabled → replaced when engine+news enabled |
| /api/models/shadow70/summary | 200 + runtime IDLE | 🟢 truthful NO_VALIDATED_CANDIDATE / READY states |

## 3. UI event flows (click → JS → API → backend → refresh)

- Refresh / Fetch News / Toggle Rules / Enable-Disable Liquidity / Run
  Research / Run Validation: each has a JS handler → API call → backend
  mutation (SettingsService / governor / audit) → response → state refresh.
- Liquidity toggle: `POST /api/liquidity/toggle` → `gov.set_enabled(actor=web)`
  → SettingsService persist (model.liquidity_features_enabled) → hot-applied →
  `gov.report()` returned (TEST-FLOW-18 covered by integration tests).
- No button appears-to-work-but-does-nothing was found: every UI action has a
  backend side effect (verified in app.js event handlers + server routes).

## 4. Bundle / version consistency (TEST-FLOW-35)

- One canonical web source: Web/ at repo root; served via FileResponse per
  request; packaged release reproduces byte-identical.
- Identity headers: `X-UI-Bundle-Sha256` + `X-UI-Bundle-Source` (REPO|PACKAGED)
  on /app.js (BUG-079 discipline).
- app.js contains the 70D Shadow panel + Liquidity Intelligence panel
  (21 liquidity/70D refs) — frontend knows the 70D surface.

## 5. Verification status

| Area | Status |
| :--- | :--- |
| API schema/envelope | 🟢 (errors.py sanitized envelope + X-Request-ID) |
| Freshness/provenance | 🟢 (timestamps + source per leaf) |
| 200-but-wrong | 🟢 (health endpoints explain, no fake zeros) |
| UI event flows | 🟢 (all actions have backend side effects) |
| Bundle consistency | 🟢 (single source + identity headers) |