# ALT Console — the alternative React UI (`frontend/`, served at `/alt`)

Owner: ALT-UI-PRO lane. Status: **active** (presentation layer; legacy `Web/`
remains the primary UI until parity is validated).

> **Recreation note.** This page previously held the ALT-UI-PRO architecture +
> test-inventory record written by the lane's documentation sibling. That copy
> was lost (it was never committed: `git log --all -- docs/alt-ui.md` is empty,
> and no blob for it survives in the object db). The text below was rewritten
> from the **code, tests and commits on `feat/alt-ui-pro-ux`** as of
> HEAD `d19bfbf2` (2026-09-13). Every number in this doc is reproducible with
> the command printed next to it — if a sibling moves code, re-run and fix the
> number instead of trusting this paragraph.

Related records (the enforced version of most claims below is a test):

| Doc | Lane | Enforcing suite |
|---|---|---|
| [`alt-ui-auth-review.md`](alt-ui-auth-review.md) | SEC-1 token/transport hygiene | `tests/unit/test_alt_ui_token_hygiene.py` (24) |
| [`alt-ui-static-security.md`](alt-ui-static-security.md) | SEC-2 static serving / auth surface | `tests/unit/test_alt_ui_static_auth.py` (15) |
| [`alt-ui-supply-chain.md`](alt-ui-supply-chain.md) | SEC-3 deps / lockfile / dist / privacy | `tests/unit/test_alt_ui_supply_chain.py` (40) |
| [`../scripts/README-alt-ui.md`](../scripts/README-alt-ui.md) | BACKEND-1 standalone host | `tests/unit/test_alt_ui_standalone_server.py` (31) |
| [`PRO_TEST_INVENTORY.md`](../tests/js/PRO_TEST_INVENTORY.md) | JS test inventory | this file lists *what runs where* |

---

## 1. What it is

A second operations console for the Nexus Scalp Engine: React 19 + TypeScript +
Vite 8, **built to static files and served by the existing FastAPI process**.
It is a presentation layer only.

Three rules are non-negotiable and gated, not aspirational:

1. **The backend is the source of truth.** No authoritative NSE state (LIVE vs
   PAPER, engine running, broker connection, trading permission) is ever
   *decided* in the browser. Every rendered value comes from a backend payload
   or shows `—` / `UNKNOWN`. Nulls are gaps, never zeros.
2. **Node is build/dev only** (DEC-0002). The production artifact is
   `frontend/dist/`; the launcher must never spawn a Node process, and there
   is no Node runtime in the packaged release.
3. **No direct data access.** The console talks to FastAPI (REST + SSE). Never
   SQLite, never MT5, never the filesystem.

### Dependency surface (10 packages, pinned by name *and* range)

Runtime: `react`, `react-dom`, `react-router-dom`, `@tanstack/react-query`,
`zustand`. Build/dev: `typescript`, `vite` (rolldown-backed),
`@vitejs/plugin-react`, `@types/react`, `@types/react-dom`. Adding, removing or
re-ranging any of them fails
`test_declared_deps_match_allowlist` in `tests/unit/test_alt_ui_supply_chain.py`
unless `DEP_ALLOWLIST` is edited in the same commit — see
[`alt-ui-supply-chain.md`](alt-ui-supply-chain.md) §1. No CDN, no third-party
script tags, no remote fonts (`Web/` BUG-047 lineage carries over).

---

## 2. How it is served

| Habitat | How | Notes |
|---|---|---|
| Dev | `cd frontend && npm run dev` → `http://127.0.0.1:5173/alt/` | Vite proxies `/api`, `/health`, `/healthz`, `/ws`, `/web`, `/api/ticks/stream` to `NSE_API_ORIGIN` (default `http://127.0.0.1:8080`). Same-origin to the browser → no CORS, working SSE. |
| Engine process (default) | `create_app()` mounts `_AltSpaStaticFiles` at **`/alt`** | `src/nexus_scalp/web/server.py` ~1683–1744. Resolver order: `NEXUS_ALT_UI_DIR` → `<repo>/frontend/dist` → `<cwd>/frontend/dist`; no `index.html` → no mount, and the log says so (`[ALT-UI] no built frontend/dist found`). Additive: `/` still serves the 315 KB legacy `Web/index.html` byte-identical (pinned by SEC-2/R1). |
| Standalone host | `python scripts/serve_alt_ui.py --port 8088` | Stdlib-only (`http.server` + `http.client`), reverse-proxies the API, relays SSE unbuffered, fail-closed `502 BACKEND_UNREACHABLE`. Bound to 127.0.0.1 unless `--host`/`--allow-remote`. See `scripts/README-alt-ui.md`. |

**SPA deep links.** `StaticFiles` only resolves real files, so a refresh on
`/alt/audit/123` used to 404 (fixed in `567ad1ad`). `_AltSpaStaticFiles.get_response`
now returns the `index.html` shell for unknown **non-asset** paths with
`Cache-Control: no-store`, and keeps an honest 404 for a missing `*.js`/`*.css`
so typos are never masked by a fake index (SEC-2/R2).

**Auth is in front of the mount, not inside it.** `/alt`, `/alt/`,
`/alt/index.html` and `/alt/assets/*` are all 401 tokenless and 200 with a
Bearer token — `/alt` is in no public-path allowlist (SEC-2/A1). Static serving
never weakens the API: `/api/*` keeps full WEB-AUTH-P0 token auth.

---

## 3. Layout

```text
frontend/
├── vite.config.ts        # base "/alt/", dev proxy to the engine (:5173)
├── src/
│   ├── main.tsx          # root mount
│   ├── app/AppShell.tsx  # router + chrome (nav, ticker, toasts, i18n dir)
│   ├── pages/{Dashboard,Trading,Positions,Risk,ML,Intelligence,Audit,Settings}/
│   │   └── Dashboard/pro/  # HeroKpiRow, PipelineAgeChain, DecisionHumanCard,
│   │                       # HealthMatrix, AccountMicroCard  (batch-2)
│   ├── components/       # AttentionStrip, CommandPalette, ConnectionIndicator,
│   │   │                 # FeedQualityChip, ModeIndicator, primitives.tsx
│   │   └── pro/          # Indicators, RiskViz, MLViz, AuditTools, OpsChrome,
│   │                     # SlTpEditor + pro-*.css
│   ├── lib/              # PURE MATH / POLICY LAYERS (see §5)
│   ├── api/              # client.ts (fetch wrapper) + domain api modules
│   ├── websocket/        # realtimeSocket.ts + rtMath.ts (SSE decision layer)
│   ├── stores/           # feedStore, uiStore, i18nStore (zustand)
│   ├── hooks/            # useRealtimeSnapshot, useQueryErrorToast,
│   │                     # useMutationFeedback
│   ├── types/            # api.ts, domain.ts, realtime.ts
│   └── styles/           # theme.css, pro-settings.css
```

The split that makes this testable without a browser: **all logic lives in
`lib/` as erasable TypeScript with no runtime side effects**; `components/` and
`pages/` are props-only views. Node 24 strips the types, so
`tests/js/pro_*.test.mjs` import the *real shipped modules* rather than a copy.

---

## 4. Data flow

```
engine (LiveEngine / audit DB / model registry)
   │  REST /api/*           SSE /api/ticks/stream
   ▼                        ▼
src/api/client.ts        src/websocket/realtimeSocket.ts
  auth headers             ?token= (EventSource cannot set headers)
  error-envelope           backoff + jitter cap 30s
  normalizer               heartbeat watchdog
  per-request timeout      version-gap resync → subscribeGap()
   │                        visibility pause
   ▼                        │
@tanstack/react-query       ▼
   │                    src/stores/feedStore.ts   ≤1 Hz coalesced projection
   ▼                        │
pages/ (queries)        components/pro/* (props-only visuals)
```

* **REST.** One `fetch` wrapper (`api/client.ts`) adds
  `Authorization: Bearer` + `X-NSE-Token` + `X-Request-ID`, unwraps the v1
  envelope, normalizes legacy/HTML/empty error bodies without inventing
  structure, and enforces a per-request timeout budget.
* **Realtime.** SSE is the primary transport (production uvicorn runs
  `ws="none"`). Reconnect uses exponential backoff with equal jitter, hard
  capped at 30 s; a heartbeat watchdog decides liveness from *frames*, not from
  `onopen`; a `state_version` gap triggers a throttled resync that `subscribeGap()`
  listeners (e.g. the audit tape) can act on; a hidden tab pauses the stream.
* **Render budget.** Ticks can fly faster than the UI should re-render:
  `feedStore` keeps the freshest truth and commits **at most once per second**
  (AppShell ticker drives `flush`), so chrome (quality chip, meters, KPI row)
  stays cheap while the query-driven tables remain backend-truthful.
* **Errors.** A failed query surfaces through the `useQueryErrorToast` bridge
  (`lib/queryBridge.ts`) — the UI reports failure instead of rendering a stale
  number as if it were fresh.

---

## 5. Visual lanes (`components/pro/*` + `lib/*Math.ts`)

| Lane | Code | Pinned by |
|---|---|---|
| UI-1 indicator math + SVG strips | `lib/indicatorMath.ts` (SMA/EMA/RSI14/ATR14/drawdown/spread, gap-truthful) + `pro/Indicators.tsx` (SparkLine, PriceBand, SpreadStrip, RsiStrip, AtrStrip) | `pro_indicators.test.mjs` (39) |
| UI-2 risk / guardian visuals | `lib/riskVizMath.ts` + `pro/RiskViz.tsx` | `pro_risk_viz.test.mjs` (21) |
| UI-3 ML / 70D feature + latency + shadow70 | `lib/mlVizMath.ts` + `pro/MLViz.tsx` | `pro_ml_viz.test.mjs` (43) |
| UI-4 audit forensics (JSON tree, timeline, matrix, CSV) | `lib/forensicsMath.ts` + `pro/AuditTools.tsx` | `pro_forensics.test.mjs` (35) |
| UI-5 ops chrome + SL/TP editor | `lib/opsChromeMath.ts` + `pro/OpsChrome.tsx`, `pro/SlTpEditor.tsx` | `pro_ops_chrome.test.mjs` (18) |
| RT realtime hardening (logic lanes) | `websocket/rtMath.ts`, `websocket/realtimeSocket.ts` | `pro_realtime.test.mjs` (30) |
| LOGIC transport hardening | `api/client.ts` | `pro_api_client.test.mjs` (32) |
| Realtime feed-health view model | `lib/feedHealth.ts`, `stores/feedStore.ts` | `pro_feed.test.mjs` (21) |
| Dashboard pro page (hero KPIs, pipeline-age chain, decision humanizer, health matrix, feed chip) | `pages/Dashboard/pro/*` | `d19bfbf2`; rendered by the AppShell route `/` |

Cross-cutting safety rules that these suites assert *structurally as well as
numerically*: a heuristic may never manufacture a verdict word, a gate without
evidence, or a direction with no provided previous value; `computeTrend` needs
a real `prev`; only a parent-advanced `state_version` key may flash the quote
tape (`flashKeyDiffers` is strict equality); view components perform no I/O and
declare no fetch/react-query/state (`SAFETY:` tests read the shipped source).

---

## 6. Security invariants (summary — the gate is the doc of record)

* **Token lifecycle (P1/P2).** `?token=` deep link → captured → stored in
  `sessionStorage["nse.altui.token"]` (per-tab, per-origin) → **scrubbed from
  the URL** with `searchParams.delete("token")` + `history.replaceState`, in
  that order. Re-materializing the token through `location.href/assign/replace`
  or `pushState` is banned, as are `window.name` and any `document.cookie =`
  write. `window.__NSE_WEB_TOKEN__` is the in-memory boot seam for a serving
  template.
* **localStorage is for visual prefs only.** Allowlist: `nexus.ui.lang`
  (shared with the legacy dashboard — same key, same dict-membership rule as
  `Web/ux_i18n.js`), `nse.altui.sidebar`, `nse.altui.dense`. NSE state never
  lives in the browser.
* **XSS hygiene (SEC-2/3).** `lib/safeHtml.ts` (`safeExternalUrl`,
  `safeInternalPath`) is the *only* file allowed to touch URL sanitization;
  bare `DOMParser` is banned line-wise; `dangerouslySetInnerHTML` with a
  `router`-style `to={{}}` sink is pinned to the audited nav registry file.
  Gate: `tests/unit/test_alt_ui_xss_hygiene.py` (14).
* **Open items owned by other lanes** (do not silently "fix" them here):
  SEC-2/C1 — the shell served as a real file (`/alt/index.html`) carries **no
  `Cache-Control`**, so a proxy may pin a stale shell after a redeploy
  (xfail-pinned until server.py sets `no-store`); SEC-2/W1 — WebSocket `/ws`
  and `/web` are outside `BaseHTTPMiddleware` and accept tokenless
  connections (auth lane); SEC-2/A2 — percent-encoded traversal passes the
  *text* `..` check in `is_public_path()` (safe today because uvicorn hands the
  middleware the decoded ASGI path; fix requested in `auth.py`).

---

## 7. i18n and accessibility

`src/lib/i18n.ts` is a port of the legacy `Web/ux_i18n.js` (CHG-0048): the same
key contract, the same preference rule, the same dictionaries — **EN (identity)
+ FA + DE + ES + AR**, 264 `alt.*` keys, complete in all four non-English dicts.
`fa`/`ar` set
`dir=rtl`. `t(key, fallback, vars)` returns the fallback — which *is* the
English source string — so a missing translation can never blank the UI. The
language picker lives on the Settings page and shares the `nexus.ui.lang` key
with the legacy dashboard (one preference, two consoles).

Visual conventions forced by the lanes above: an explicit `UNKNOWN` word (never
a blank, never a dash pretending to be data), gaps drawn as gaps in the SVG
series, badge vocabulary that is not green-only, and structured confirm dialogs
(no generic "Are you sure?").

---

## 8. Testing and CI

**Node (pure logic, no browser, no network):**
```bash
node --test tests/js/pro_*.test.mjs      # alt console lanes: 8 files, 239 tests
node --test tests/js/*.test.js           # legacy Web/ console: 11 files, 113 tests
```
Total on this branch: **352 tests across 19 files, 0 fail** (see
[`tests/js/PRO_TEST_INVENTORY.md`](../tests/js/PRO_TEST_INVENTORY.md) for the
per-file table, the `.mjs` gap, and how to add a suite).

**Type/build gate:** `cd frontend && npm run typecheck` (`tsc -b`) then
`npm run build` — TSX rendering is verified by strict-mode compile + Vite build.

**Python (offline):** `pytest tests/unit/test_alt_ui_*.py` — 134 tests across
six files: token hygiene (24), static/auth surface (15), supply chain (40),
XSS hygiene (14), standalone host (31), runtime contract (10).

**CI reach — and its one honest hole.** `.github/workflows/js-tests.yml` runs
`node --check` on `Web/app.js`, `Web/api_client.js`, `Web/forensic_console.js`
and then a loop over `tests/js/*.test.js`. That glob **excludes the `.mjs`
suites**, so the ALT-UI-PRO logic batteries are green locally and invisible to
CI; the glob now also picks up `tests/js/*.test.mjs` (this workflow's own runner
is `actions/setup-node@…# v7.0.0` with `node-version: "24"`, which is what
Node's type stripping needs). The `frontend/` **build** is still not a CI gate:
no workflow runs `npm ci`/`tsc -b`/`vite build` — deliberate (no bundler in
production, DEC-0002) but worth knowing before trusting a shipped `dist/`.

---

## 9. Environment variables

| Var | Where | Meaning |
|---|---|---|
| `NSE_WEB_AUTH_TOKEN` | backend | token the console must present (`NSE_WEB_AUTH_DISABLE=1` to opt out locally — never with LIVE mode or a routable host) |
| `NSE_WEB_AUTH_COOKIE_DISABLE` | backend | pins header-only transport (used by the SEC-2 suite) |
| `NEXUS_ALT_UI_DIR` | server / host | explicit `dist` directory; disables auto-discovery when set |
| `NSE_API_ORIGIN` | vite dev / standalone host | backend origin to proxy to |
| `NSE_ALT_UI_PROXY_TIMEOUT` | standalone host | upstream request budget |
| `NEXUS_WEB_DIR` | server | legacy `Web/` override (not the alt console) |

## 10. Where to look for a change

| I want to… | Touch | Verified by |
|---|---|---|
| add a derived number to a visual | `src/lib/<x>Math.ts` | the lane's `pro_*.test.mjs` |
| add a tile/component | `src/components/pro/` or `src/pages/<Page>/pro/` (props-only) | `SAFETY:` source scans + `tsc -b` |
| add a copy string | `src/lib/i18n.ts` **all five dicts** | legacy comparison + Settings picker |
| change transport/reconnect policy | `src/api/client.ts` or `src/websocket/` | `pro_api_client` / `pro_realtime` / `pro_feed` |
| change how `/alt` is served | `src/nexus_scalp/web/server.py` (`_AltSpaStaticFiles`) | `test_alt_ui_static_auth.py` |
| change the standalone host | `scripts/serve_alt_ui.py` | `test_alt_ui_standalone_server.py` |
| add a dependency | `frontend/package.json` **+** `DEP_ALLOWLIST` in the supply-chain gate, same commit | `test_alt_ui_supply_chain.py` |
| make a JS suite visible to CI | it must be `tests/js/*.test.js` **or** `.mjs` (glob in `js-tests.yml`) | `js-tests.yml` |

## 11. Known limitations / not yet wired

* Page wiring for the pro components is in flight — a human integrator owns
  `frontend/src` route composition right now; `AppShell.tsx` carries local WIP.
* The `frontend/dist` build step is not CI-gated (§8), so `dist` freshness is a
  release-process responsibility.
* SEC-2/C1 (stale shell cache) and SEC-2/W1 (unauthenticated WebSocket) remain
  open findings in other lanes' files — see §6.
* Parity with `Web/` is **not** declared: the legacy dashboard is still primary
  and `command_center` / Time Machine / spatial views are not ported.

## 12. History (ALT-UI-PRO, this branch)

`567ad1ad` initial console + SPA deep-link fix → `8cfe5a06` standalone host
(BACKEND-1) → `4526cfcc` realtime + transport hardening (LOGIC/RT) → `84170f57`
pro visual lanes UI-1..5 → `42c5321a` i18n sweep, Settings, audit tabs,
dashboard tiles → `d19bfbf2` Dashboard pro page + SEC-1/2/3 gates and docs.
