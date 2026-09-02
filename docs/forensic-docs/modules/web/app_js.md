# Web/app.js

- **PURPOSE:** The dashboard's entire interactivity (10,946 lines):
  API polling + SSE/WS consumption, tab switching, live charts (candles +
  SMC overlays), account/performance rendering, rule toggles, config
  editing, news/research/factory/governance/health/incidents/liquidity/
  debug views, feature matrix, snapshot compare, and the no-synthetic-data
  render discipline.
- **ARCHITECTURE LAYER:** Web UI (controller).
- **RESPONSIBILITY:** (a) data plumbing: fetch loops (deduped in-flight
  requests via api_client), SSE /api/ticks/stream reconnect (bounded
  exponential, stale detection), WebSocket; (b) state versioning
  (StateVersioner contract — v— indicator, staleness handling);
  (c) rendering: pure — every value comes from an API field (n/a for
  null, never fake zeros); (d) control actions: rule toggles, config
  saves, engine mode (via settings service path), position modify/close,
  model governance actions — all through the sanctioned endpoints.
- **DEPENDENCIES:** api_client.js (window.NX.api), index.html ids,
  styles.
- **CONNECTS TO:** the 168 REST routes + SSE + WS; tests
  (test_frontend_assets_phase14 — node --check, asset presence; BUG-119
  UI mode selector contract).
- **KEY CONCEPTS:** file is pure CRLF (check `raw.count(b'\r\n')` before
  editing; inserted blocks must be CRLF-normalized — the patch tool
  mangles CRLF JS, use execute_code line-slice replacement + node
  --check); every tab's renderer is a pure function of fetched state.
- **EDGE CASES & PITFALLS:** never compute trading intelligence in JS
  (the debug tab renders snapshot sections only); Engine mode selector
  must reflect the REAL runtime mode (settings DB), never a local flag;
  SSE error paths must surface sanitized messages (X-Request-ID kept).