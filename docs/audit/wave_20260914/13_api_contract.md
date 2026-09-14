# Lane 13 — API / Service-Contract Audit (wave 20260914)

Scope: `src/nexus_scalp/web/` FastAPI surface (legacy dashboard API + `/api/v1`
platform), auth boundary (WEB-AUTH-P0), health vs readiness semantics,
validation/error contracts, unbounded queries, slow handlers, debug exposure,
and contract drift across the three consumers: legacy `Web/` bundle, alt React
`frontend/` (served at `/alt`), and the marketplace contract.

Repo: `NexusTradingForexBot` @ `9431edd2` (branch `nse/master-active-scalper-wave`).
Method: static grep + executed probes via `./.venv/Scripts/python.exe`
(repo root, `fastapi.testclient.TestClient` on `create_app(engine_ref=None)`
and a token-equipped client). **VERIFIED** = observed in executed output;
**CODE** = static read with file:line. Zero writes outside this file; git untouched.

---

## 1. Route enumeration (VERIFIED — live route table)

`create_app()` → 380 routes total: 373 APIRoute ops + 4 docs routes
(`/docs`, `/openapi.json`, `/redoc`, `/docs/oauth2-redirect`) + 2 WebSocket
routes (`/ws`, `/web`) + 1 StaticFiles mount (`/alt`).

| Group | Ops | Evidence |
|---|---|---|
| `/api/v1/*` (12 routers, `api_v1_wiring.register_api_v1`) | **86** | capabilities reports `endpoint_count: 86` (VERIFIED) |
| legacy `/api/*` (non-v1) | 251 route ops / 246 distinct paths | live table (VERIFIED) |
| static bundle + pages (`/`, `/app.js`, `/command_center.html`, …) | ~35 | `server.py:1678-1913` CODE |
| WebSocket `/ws` + `/web` | 2 | `server.py:1660` CODE |

Registration seams (CODE): `server.py:1942` diagnostics/state, `:2690` debug
+research (which itself registers model-governance `:2140`, news/liquidity
`:2156`, factory/dependency/news-intel/db-console include_router `:2164-2186`,
command-center `:2197`), `:2698` operator, `:2704` calibration, `:2714-2767`
replay, `:2770` SSE, `:2887` api_v1 wiring, `:2891` `_install_web_auth_if_enabled`
(defined `:2904`).

## 2. Health vs readiness classification

| Endpoint | Semantics | Status |
|---|---|---|
| `GET /health` | HealthEngine verdict; 200 only when READY and no critical FAILs; 503 `{verdict, checks}` otherwise (DEGRADED also 503 — docker `healthcheck.sh` parses the 503 body, by design). 60s cache (`_HEALTH_TTL_SEC`, `diagnostics_state_routes.py:48,127`) | VERIFIED 200 READY (cold 3.2s, warm 7ms) |
| `GET /api/health` | **no route** — 404, yet still in `auth.py PUBLIC_PATHS` allowlist and in the `server.py:2898` comment. The known note is CONFIRMED: doctor/A2A probe was fixed to `/health` (BUG-267, `release/health.py:603` comment) | VERIFIED 404 |
| `GET /healthz` | allowlisted, no route → 404 | VERIFIED |
| `GET /api/v1/system/health` | HealthEngine verbatim, `{data,meta}` envelope, 3s TTL | VERIFIED 200 |
| `GET /api/v1/system/readiness` | `ready = verdict != "NOT READY" and no FAIL in 6 required infra layers` | VERIFIED — see §8 honesty gap |
| `/api/debug/health`, `/api/research/health`, `/api/forensics/health`, `/api/news/health`, `/api/dependency/health`, `/api/models/governance/health`, `/api/models/shadow70/health`, `/api/diagnostics/health` | eight domain-scoped "health" endpoints, each a different vocabulary, none liveness/readiness | CODE |
| `/api/status` `health` section | `_build_health_section` per-subsystem live verdict strings | CODE `server.py:511` |

Verdict: the liveness/readiness split is *only* coherent on the `/health` +
`/api/v1/system/{health,readiness}` triangle; everything else named
"health" is domain diagnostics. Dead allowlist entries (`/api/health`,
`/healthz`, `/favicon.ico` — favicon also 404s, VERIFIED) should be removed
or given routes.

## 3. Auth boundary (WEB-AUTH-P0)

Installed as outermost Starlette middleware LAST in `create_app`
(`server.py:2891`, `auth.py:346-467`). Fail-closed: every path 401s
`{ok:false,error:{code:"UNAUTHORIZED"}}` except the allowlist; LIVE mode
forces auth. Verified behaviors (token via `NSE_WEB_AUTH_TOKEN`):

* 401 without creds on `/api/status`, `/api/v1/*`, `/docs`, `/openapi.json` (VERIFIED).
* Public: `/`, `/index.html`, `/alt`, `/alt/`, `/app.js`, `/api_client.js`,
  static bundle, `/vendor/`, `/assets/`, `/static/`, `/health` (VERIFIED 200).
* Cookie transport (BUG-267): `Set-Cookie: nse_web_auth=<token>; HttpOnly;
  Max-Age=43200; Path=/; SameSite=strict` issued on `COOKIE_BOOTSTRAP_PATHS`;
  a cookie-bearing TestClient then 200s `/api/status` (VERIFIED).
  **No `Secure` flag** (VERIFIED header text) — fine on loopback HTTP,
  silent risk behind any TLS-terminating proxy misconfig.
* Precedence Bearer header > `X-NSE-Token` > cookie > `?token=` query;
  opt-out `NSE_WEB_AUTH_COOKIE_DISABLE=1`; `NSE_WEB_AUTH_DISABLE=1` exists with
  a warning log (VERIFIED boot warning).
* Constant-time compare ✓; traversal guard in `is_public_path` ✓ (BUG-267).

Findings:

1. **WebSocket auth bypass (VERIFIED in-process):** the auth layer is
   `BaseHTTPMiddleware` — it never sees `scope["type"]=="websocket"`, and a
   tokenless `websocket_connect("/ws")` succeeded under TestClient. Production
   is shielded only because uvicorn boots `ws="none"`
   (`cli/engine_boot.py:625`, VERIFIED CODE) so real upgrades 404 — any other
   launcher (`uvicorn nexus_scalp.web.server:...`, `scripts/serve_alt_ui.py`
   proxy, a future ws-websockets install) exposes the unauthenticated snapshot
   loop (`server.py:1660-1676` sends full `get_system_state()`). The WS
   handlers are dead weight either way: realtime contract is SSE-only
   (`/api/ticks/stream`), legacy `Web/app.js:3511` uses `EventSource`, and the
   React `websocket/realtimeSocket.ts` is an SSE shim. Recommend deleting
   `/ws`+`/web` or gating at the ASGI layer.
2. **Single shared token = full admin.** SSE forces the token into a URL query
   param (`?token=`, `auth.py:326`) → token appears in access logs/proxies;
   CORS is `allow_origins=["*"]` (`server.py:420`) though `allow_credentials=False`
   contains it. No read-only vs operator scope: one credential opens
   `/api/positions/close`, `/api/engine/mode`, `/api/db/console/query`.
3. Legacy bundle needs no per-call auth wiring (cookie rides ~50 raw
   `fetch()`/`NX.api`/EventSource sites) — consistent with the BUG-261/267
   design note. No drift found: all 39 distinct `/api/*` literals in `Web/*.js`
   resolve to live routes (VERIFIED by cross-check).

## 4. Validation + error contracts (per surface)

Three coexisting error grammars (VERIFIED live responses):

| Surface | Validation failure | Not-found | Unavailable |
|---|---|---|---|
| `/api/v1` | `{"error":{"code":"VALIDATION_ERROR","details":{"errors":[{"field","issue","input_present"}]} …}}` 422 (custom handler, no input echo) | `RESOURCE_NOT_FOUND` 404 | `ENGINE_UNAVAILABLE`/`RESOURCE_UNAVAILABLE` 503 `retryable:true` |
| legacy `_err()` | FastAPI default `{"detail":[…]}` 422 (pydantic) | `{"available":false,"success":false,"error":{"code":…}}` HTTP 200 (!) | same 200-envelope |
| legacy raw | `HTTPException` → `{"detail":"Trading Engine offline."}` 400 / 404 `{"detail":"Not Found"}` | — | — |

Concrete defects:

* **HTTP-200 errors in legacy:** `/api/operator/decisions/999999999` → 200 with
  `error.code:"NOT_FOUND"`; `/api/db/console/query` rejections → 200. Clients
  must parse bodies, not status codes (VERIFIED).
* **Unknown legacy error codes:** `_err("NOT_FOUND"|"FACTORY_UNAVAILABLE"|"INVALID_REQUEST"|…)`
  are not in `ERROR_CODES` (`web/errors.py:63-73`) so the message silently
  degrades to *"The server could not complete this request."* — observed for
  `NOT_FOUND` (VERIFIED). 28 distinct codes used in route files vs 9 registered.
* **Mixed 422 grammars per surface** (v1 envelope vs FastAPI default array) are
  undocumented; `API_REFERENCE.md` §4 documents only the v1 one.
* **db-console keyword filter is substring-based, not statement-aware**
  (`db_console.py:437-447`): `SELECT created_at FROM audit_signals` is rejected
  — "'CREATE' is not allowed" (VERIFIED). False-positives on any query touching
  a column/label containing INSERT/UPDATE/DELETE/REPLACE/CREATE…; meanwhile
  `PRAGMA` is allowed by the prefix list, so console queries can run engine
  pragmas (single-statement check stops chaining; write-pragmas are mostly
  blocked only by the same substring bug).
* `PUT /api/algo/config` requires ALL five fields (full-model replace, VERIFIED
  422 "Field required") — no partial update, no range constraints at the model;
  safety relies entirely on `engine.apply_runtime_update` validation (CODE).
* `POST /api/simulation/tick` is paper-only guarded server-side (CODE
  `server.py:2560-2570`), good; `POST /api/engine/toggle` requires a body even
  though it takes no meaningful fields except `active` — 422 when omitted
  (VERIFIED).

## 5. Unbounded queries / limits

Bounded (VERIFIED CODE + probes): v1 pagination (`page_size ≤ 200`,
`parse_pagination` 422 VERIFIED; `fetch_rows_bounded` hard ceiling 5000 with
server-side LIMIT); `/api/operator/decisions` `limit ≤ 500`; `/api/chart/history`
`count` clamped to 5000 (VERIFIED `requested:5000` for `count=999999`);
`/api/db/console/rows|query` capped 500 (`MAX_ROWS/QUERY_LIMIT`);
research events/gates/evidence clamp `MAX_READ_LIMIT=2000`; model registry
`list_models` clamp 500; factory stores clamp; marketplace seeds/scores
page-limited; deep-page v1 decisions fetches `offset+limit` rows (bounded ≤
5000/req, VERIFIED page 30 returns cleanly).

Weak / unbounded (CODE unless noted):

1. **`/api/account/trades` (legacy `diagnostics_state_routes.py:976`)**:
   `limit`/`offset` forwarded raw to `AuditRepository.get_broker_trades` →
   `LIMIT ?` with a client value. VERIFIED probe: `get_broker_trades(limit=-1)`
   returns the **entire table** (negative SQLite LIMIT = no limit; 4051/4051
   rows) and `limit=100000` → full table. Route-level clamp missing; also the
   fallback `get_ledger_trades` behaves identically (371 rows at `limit=-1`).
2. **Latent `SQLITE_MAX_VARIABLE_NUMBER` bomb in operator analytics:**
   `/api/operator/funnel` pulls `id … LIMIT 50000` then builds
   `WHERE id IN (?,?,…)` with one param per row (`operator_routes.py:92,492`);
   `/api/operator/summary` same at 20000 (`:267`). VERIFIED on this build
   (SQLite 3.53.1): 32766 params ok, 50000 params → `too many SQL variables`.
   Breaks at ~33k ledger rows; the audit DB already holds 1469 signals and the
   retention window is weeks.
3. `GET /api/v1/marketplace/repairs?seed_id=…` has **no LIMIT** on the
   filtered branch (`marketplace.py:417-424`) vs `LIMIT 100` unfiltered.
4. `GET /api/v1/marketplace/rankings` scans **all** `mk_score_snapshots` +
   `mk_seeds` per request, sorts in Python, ignores `dimension` for the ORDER BY
   (it is echoed only) (`marketplace.py:241-293`).
5. `GET /api/experience/models` passes `limit` through `max(1,int(limit))` with
   no ceiling (`experience/provenance.py:168`) — any client `limit=10**9`.
6. Command-center `fleet/spatial/timemachine` default `limit=2000` but
   `spatial` runs a full `api.inspector(strategy_id)` per entry (N+1 heavy
   reads, `command_center_integration.py:100-121`).
7. `v1 fetch_rows_bounded` + several stores (`list_registry`, `list_models`,
   `list_runs`, `list_autopsies`) **silently return `[]` on PostgreSQL**
   (`if not _is_sqlite: return []`) — with the DATABASE PORTABILITY program
   live, an empty page is a *false* contract signal, not an error.

## 6. Slow endpoints (sync work, event-loop exposure)

Measured with engine=None (cold TestClient, this machine, 132 MB audit.db):
`/api/status` 6235 ms first call → 19 ms after 60s caches; `/health` 3209 → 7 ms;
`/api/v1/system/health` 2222 ms (3s TTL); `/api/db/hygiene` 1218 ms;
`/api/v1/database/integrity` 421 ms; `/api/v1/system/status` 122 ms;
`/api/operator/summary` 85 ms (VERIFIED).

* SSE `/api/ticks/stream` calls **sync `get_system_state()` from the async
  loop every 0.2s** (`server.py:2796`) — the documented PHASE-28 hot-path fix
  only *caches* the 270-1000 ms version block; the cache **rebuild still runs
  inline on the loop** every 60s (no background refresh), and
  `compute_live_freshness()` is invoked **twice per snapshot**
  (`server.py:1637-1645`). With a live engine this re-introduces periodic
  tick starvation the fix targeted.
* Every other heavy handler is `def` (sync) → FastAPI threadpool — correct
  pattern (no `async def` handler performs blocking IO: scan VERIFIED zero
  matches for sqlite/requests/sleep inside async route bodies).
* `AuditRepository()` is constructed **per request** on several fallback paths
  (`diagnostics_state_routes.py:556,574`; `server.py:1536`; v1 caches it on
  `app.state.audit_v1_repo` — the legacy paths do not; each boot logs
  "Initialized High-Performance SQLite WAL storage" — visible ~30× during
  probe runs, VERIFIED log flood).
* `operator_routes` bypasses `AuditRepository` with raw `sqlite3.connect(...)`
  (read-only URI) — a second persistence access pattern to keep portable
  (CODE `:117`).

## 7. Debug / operator surfaces exposed in production

All behind the single web-auth token (VERIFIED 401s), none behind a feature
flag or loopback-only guard: `/api/debug/*` (10 ops incl. `POST
/api/debug/model-test` which runs inference), `/api/db/console/*` (SQL console +
API-key CRUD), `/api/diagnostics/incidents/*/zip` (archive download),
`/docs` + `/openapi.json` + `/redoc` (full route map, 200 with token, VERIFIED),
`/api/factory/provider-test` (outbound LLM probe), `/api/telegram/test`,
`/api/research/promote`/`/api/models/promotion/execute` (governance mutations).
`auth.py` deliberately keeps `/api/health` allowlisted-but-routeless; nothing
here is *undocumented-public*, but the token is the only wall between a browser
tab and `POST /api/positions/close` — no second factor, no read-only mode, no
audit-of-admin beyond the standard audit rows.

## 8. Observable "why not trading" state — what /api actually exposes

Consumers today:

| Need | Where it exists | Gap (VERIFIED engine=None unless noted) |
|---|---|---|
| mode | `/api/v1/runtime/mode` `{mode, effective_mode, engine_attached, replaying}`; `/api/status.execution_mode/runtime_mode`; `/api/live/state.market.execution_mode` | ✓ present, `null` when engine offline |
| gates (historical) | `/api/operator/funnel` (stage/gate counters), `/api/operator/no-trade` (gates, reasons, hourly trend), `/api/v1/decisions/no-trade{,/reasons}`, `/api/v1/decisions/{id}/gates` | historical only |
| gates (live, current tick) | **absent** — `/api/live/state` has no `gates` block; only `health.subsystems` + `live_freshness` + per-prediction `reason` strings | no per-candidate pass/fail list for the *current* bar |
| setup score | `radar` (`bar_handler.py:147-165`: `best_setup.quality`, `setups[].quality`, `state: SETUP_READY|WATCHING|NO_SETUP`, `decision_reason`) reaches the client **only via engine snapshot** — `/api/status`, SSE and `/api/live/state.radar`; **null with engine detached** (VERIFIED). Not in `/api/v1` at all. Legacy overlays additionally hard-code `"zone_score": 85.0` for open-position rectangles (`server.py:1518`, CODE) — a *synthetic* score inside the "NEVER a synthetic value" snapshot (contract violation, low severity, overlay-only) | no v1 route; React UI must consume legacy shape |
| block reason | `ai_reason`/`strategy.reason` (current proposal), `predictions[].reason` (last 40), `reason_code` per decision | ✓ but scattered across three keys |
| honest READY vs can-trade | `/health` + `/api/v1/system/readiness` = **infrastructure readiness only** | **VERIFIED contradiction:** `readiness.ready:true, verdict:"READY"` while `system_status.engine_attached:false`, `/api/v1/positions` 503 ENGINE_UNAVAILABLE, `execution_mode:null`. Nothing in the readiness layer requires engine-running, MT5-connected, trading-switch-on, or gate-pass. The only true *can-trade* answer is per-strategy `GET /api/command-center/execution-safety/{strategy_id}` (`can_trade, eligibility_state, blockers`, CODE `command_center_routes.py:387-416`) — no global equivalent, and `/api/live/state.strategy.score/state` are **hardcoded `None`** (`diagnostics_state_routes.py:666-668`). |

Missing observable state for a client to answer "why not trading" in one call:
a single `can_trade` boolean with `block_reasons[]` (engine off / MT5 down /
mode PAPER / guard OPEN / no fresh tick / gate X blocked), a live `gates[]`
per-current-bar list, and a first-class `/api/v1/trading/eligibility` (+ radar
score) exposure. `radar.state` is the closest existing token and only exists
when an engine is attached.

## 9. Contract drift — docs vs routes vs clients

1. **Counts disagree three ways:** spec §7 total **65** ops
   (`API_PLATFORM_V1.md:198`); `API_REFERENCE.md:4` claims **68** documented
   ops and **257** legacy routes; live = **86** v1 ops + 251 legacy ops
   (VERIFIED). 24 live v1 ops are outside spec §7 (indicators domain ×6,
   audit/events, observability/{events,metrics}, features/groups,
   decisions/no-trade/reasons, **entire marketplace domain ×12**, and a
   `/positions/history/{ticket}` path-drift of spec op 32) — the marketplace
   was added under a different spec ("ARCH_SPEC §3", `api_v1/marketplace.py:3`).
2. **Spec-ops that no longer exist:** `GET /positions/history`,
   `GET /positions/pending`, `GET /positions/{ticket}/context` (spec 32-34).
   Worse: `/api/v1/positions/history` and `/positions/pending` now resolve to
   `position_detail(ticket:int)` → **422 int_parsing** (VERIFIED) — a
   documented path is actively error-shaping, and `frontend/src/api/positionsApi.ts:5`
   still *documents* `GET /api/v1/positions/history` in its header while the
   code calls legacy `/api/account/trades` (client-side doc drift).
3. **`API_REFERENCE.md` §2 says "Authentication: None for local use"** —
   false since WEB-AUTH-P0: 401 VERIFIED on `/api/v1/system/version` without a
   token. §3 claims `meta.api_version` on envelopes — VERIFIED absent
   (`meta` = `{request_id, generated_at}` only). §8 "Only three mutations exist
   in v1" — VERIFIED **10** v1 POSTs (6 of them real marketplace mutations:
   install/enable/disable/repair/run-research + the `read_only:true` flag in
   `/api/v1/system/capabilities` output contradicts them).
4. **Duplicate/shadowed legacy routes (VERIFIED via router matcher + responses):**
   * `POST /api/news/analyze/{article_id}` registered twice — the
     news-liquidity handler (`news_liquidity_mslie_routes.py:592`) is registered
     first and wins; the news-intelligence one (`news_intelligence_routes.py:117`)
     and the batch variant `/api/news/analyze/batch` (`:154`) are
     **unreachable** (a call to `…/batch` is handled as article_id="batch").
   * `GET /api/news/ai-status` is **shadowed** by the catch-all
     `GET /api/news/{article_id}` (`news_liquidity_mslie_routes.py:762`,
     registered earlier): live response is the detail handler's
     `{"available":false}` — identical to a random article id (VERIFIED),
     while the real AI-status handler returns `{success:true,ai_status:…}`.
     Both React `newsApi.ts` and legacy `news_intelligence.js` call it → they
     silently consume the wrong shape.
   * `@router.get("/api/models/shadow/runs")` +
     `@router.get("/api/models/shadow70/summary")` are **stacked decorators on
     one handler** (`model_governance_routes.py:364-366`) — `/api/models/shadow/runs`
     returns the *70D* summary, byte-identical (VERIFIED), contradicting spec
     op 47's ShadowStore runs. Naming-collision hazard for any client.
5. **Three snapshots of one truth:** `/api/status` (loose), `/api/live/state`
   (`contract:"LiveUiState.2"`), SSE `state` event (same LiveUiState.2 via
   `get_system_state`), plus `/api/v1` domain slices — the v1 surface does not
   carry radar/news/gates sections, so the React app consumes **both** grammars.
   VERIFIED by literal cross-check: every one of the 142 `/api/*` literals in
   `Web/*.js` and all 252 in `frontend/src` resolve to live routes (no dangling
   client calls), but the React console still calls **199 distinct legacy**
   paths vs **53** `/api/v1` (≈4:1) — v1 is not yet the console's contract of
   record. `positionsApi.ts:5` documents `GET /api/v1/positions/history` in its
   header while the same file calls legacy `/api/account/trades` (item 2).
   No version/compat matrix exists for the legacy grammar (its
   `{available,success}` convention is documented only as a footnote in
   `API_PLATFORM_V1.md` §1).
6. **Marketplace contract is the one aligned triangle:** `Web/marketplace.js`
   (frozen paths via `NX.api`, pinned by
   `tests/unit/test_web_marketplace_contract.py`) and `marketplaceApi.ts` use
   the identical `/api/v1/marketplace/*` set; bodies: install `{count?}`
   (1..500 validated, VERIFIED 404-first for unknown pack — count validation is
   *behind* the pack lookup), enable `{mode}` required (VERIFIED 422),
   repair `{trigger}` **optional** server-side though `marketplace.js:503`
   always sends `"MANUAL_TRIGGER"` (CODE, harmless).
7. Health-probe drift risk: `docker/healthcheck.sh` treats **DEGRADED as
   healthy by parsing the 503 body**, while `auth` allows `/api/health` which
   404s and doctor probes `/health` (VERIFIED 404 + CODE). One grammar, three
   expectations — the dead `/api/health`/`/healthz` allowlist entries invite a
   future route added at a *different* semantics than `/health` (503-on-DEGRADED
   vs the v1 `{data}`-200-always shape — `/api/v1/system/health` returns 200 even for NOT READY
   (CODE `api_v1/system.py:107-120`: only a None verdict fails, no 503 branch;
   VERIFIED: engine-detached probe still 200 READY).
8. Static-bundle contract: every `Web/` file needs a hand-written `@app.get`
   route AND an `auth.py` allowlist entry AND (for cookie fix) `COOKIE_BOOTSTRAP_PATHS`
   — three parallel lists already diverged twice (WEB-UI-BOOTSTRAP, BUG-267);
   a `StaticFiles` mount for the bundle would collapse them (CODE observation).

## 10. Weak / duplicate / undocumented endpoint register (roll-up)

* Weak: `/api/account/trades` (no clamp, negative-limit escape, VERIFIED);
  `/api/operator/funnel|summary` (50k/20k-param IN clause, VERIFIED ceiling);
  `/api/v1/marketplace/rankings` (full-table scan, filter ignored);
  `/api/v1/marketplace/repairs` (unbounded filtered branch);
  `/api/experience/models` (no ceiling); `fetch_rows_bounded` silent-[] on PG;
  `/api/db/console/query` substring filter false-rejects (VERIFIED).
* Duplicate: `/api/models/shadow/runs` ≡ `/api/models/shadow70/summary`
  (VERIFIED); `/api/news/analyze/{article_id}` ×2 (one dead); nine
  domain-"health" endpoints; `/api/status` vs `/api/live/state` vs SSE
  full-snapshot (three grammars); v1 `signals/latest` vs `decisions/latest`
  (same row, two envelopes); `/dependency` vs `/dependency.html`; `/ws`+`/web`
  (dead in prod, unauth in principle).
* Undocumented: all 251 legacy ops (OpenAPI-only, mostly summarized via
  docstrings); 24 live v1 ops outside spec §7; `/api/operator/calibration`;
  `/api/command-center/*`; `/alt` mount behavior (`_AltSpaStaticFiles` SPA
  fallback, `server.py:1708-1741`).
* Dead/ghost: `/api/health`, `/healthz`, `/favicon.ico`, `/static/*`,
  `/assets/*` allowlisted with no routes (VERIFIED 404); spec ops 32-34;
  `docs/api/API_REFERENCE.md` §2/§3/§8 claims stale (VERIFIED).

## 11. Recommended contract fixes (priority order)

1. Add `/api/v1/trading/eligibility` (or extend readiness): global
   `can_trade` + `block_reasons[]` incl. engine-attached, tick freshness,
   guard, gates-live, radar best-setup quality — the single observable the
   client needs (today's `ready:true` ≠ can trade, VERIFIED §8).
2. Fix route shadowing: reorder news routers (register `news_intelligence`
   before the `/api/news/{article_id}` catch-all, or tighten the catch-all
   regex); split the stacked shadow/shadow70 decorators; resolve or delete the
   `/ws`/`/web` endpoints (they are auth-invisible by construction).
3. Clamp `/api/account/trades` limit/offset; replace operator IN-clause
   analytics with aggregate SQL (GROUP BY) to kill the 32k-param cliff; add
   LIMIT to marketplace repairs/rankings.
4. Reconcile docs: regenerate §7 inventory from the live route table
   (`capabilities` is already machine-readable), correct counts, auth section,
   mutation list, `meta.api_version` claim; drop or route `/api/health`+`/healthz`.
5. Normalize legacy error contract to non-200 statuses + registered codes;
   make `fetch_rows_bounded` raise `DEPENDENCY_UNAVAILABLE` on non-SQLite
   instead of returning `[]`; move the 60s version-block refresh off the SSE
   loop (background task); reuse a cached AuditRepository on legacy fallbacks.
