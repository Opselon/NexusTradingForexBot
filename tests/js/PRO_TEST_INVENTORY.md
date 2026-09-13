# tests/js — JS test inventory (ALT-UI-PRO / pro console)

Machine-checked list of what lives in `tests/js/`, which suite pins which
shipped module, and whether CI actually runs it. Written for the pro-console
lane; re-verify with the commands in §5 before editing numbers.

Baseline: worktree `nse-ui-pro-ux`, branch `feat/alt-ui-pro-ux`, HEAD `d19bfbf2`,
Node **v24.18.0**.

```text
19 files · 352 tests · 0 failed          (all pass on this host, this session)
├── 11 × *.test.js   113 tests   legacy buildless `Web/` console + XAUUSD API
└──  8 × *.test.mjs  239 tests   ALT console (`frontend/src`) logic layers
```

---

## 1. The gap this note exists for (and the fix)

`.github/workflows/js-tests.yml` used to collect suites with

```bash
files=(tests/js/*.test.js)          # ← excludes .mjs
```

so **all eight `pro_*.test.mjs` suites — the entire ALT-UI-PRO logic battery
(239 tests) — ran locally and were invisible to CI.** The `.mjs` extension is
required by the pro suites: they `import` real `.ts` modules from
`frontend/src/`, which needs `"type": "module"` semantics plus Node's type
stripping (≥ 22.18; the workflow pins `node-version: "24"`).

The glob is now:

```bash
files=(tests/js/*.test.js tests/js/*.test.mjs)
```

Nothing else in the workflow changed (line-for-line diff = 1, CRLF preserved).
The runner loop is `node "$t"` (no `--test`); verified locally that **all 19
files exit 0 under exactly that invocation** — `node:test` files self-execute
when run as a script, and the three script-style files (`forensic_console`,
`forensic_incidents_dom`, `test_market_radar_ui`) use bare `assert.` and exit
non-zero on failure.

**Still not in CI:** the `frontend/` build. No workflow runs `npm ci`,
`tsc -b --noEmit` or `vite build`, so a type error in `frontend/src` is a local
catch only (and `frontend/dist` freshness is a release-process duty). Known and
intentional per DEC-0002's "no Node at runtime" rule, but recorded here so
nobody assumes the ALT console is fully gated.

## 2. Pro (ALT console) suites — `tests/js/pro_*.test.mjs`

| File | Tests | Module under test (`frontend/src/`) | Lane | Pins |
|---|---|---|---|---|
| `pro_api_client.test.mjs` | 32 | `api/client.ts` | LOGIC-2 transport | `normalizeErrorEnvelope` (v1 / legacy / bare-string / HTML-500 / 401 variants, request-id precedence), auth headers (`Bearer` + `X-NSE-Token` + `X-Request-ID`), 204/empty body never crashes the parser, per-request timeout budget, stream pinning — under a **stubbed `global.fetch`**, no network |
| `pro_realtime.test.mjs` | 30 | `websocket/rtMath.ts`, `websocket/realtimeSocket.ts` | RT/HARDEN | backoff with equal jitter capped at 30 s, heartbeat watchdog liveness (frames, not `onopen`), version-gap resync + `subscribeGap()` throttling, `visibilitychange` pause, timer cleanup — via a `node:module` resolve hook for the `@/` alias |
| `pro_feed.test.mjs` | 21 | `lib/feedHealth.ts`, `stores/feedStore.ts` | REALTIME view model | store commits **at most once per second** while ticks fly, freshest-truth merge, data-age advance, quality transitions honest to backend status |
| `pro_indicators.test.mjs` | 39 | `lib/indicatorMath.ts` (+ `components/pro/Indicators.tsx`) | UI-1/5 | SMA/EMA/RSI14/ATR14/drawdown/spread hand-derived from the spec + the Python authority; gaps preserved (missing counted, **never zero-filled**); `seriesGeometry` one polyline per run; `SAFETY:` engine declares no I/O/fetch/state, `Indicators.tsx` is props-only; the unknown word is literally `UNKNOWN` |
| `pro_risk_viz.test.mjs` | 21 | `lib/riskVizMath.ts` (+ `pro/RiskViz.tsx`) | UI-2 risk/guardian | client-side safety contract: no heuristic may manufacture a verdict word or a gate without evidence |
| `pro_ml_viz.test.mjs` | 43 | `lib/mlVizMath.ts` (+ `pro/MLViz.tsx`, `pro-ml.css`) | UI-3 ML/70D | feature/latency/shadow70 math against backend truth; `@/` value imports resolved by an in-process hook; CSS shape checked as text |
| `pro_forensics.test.mjs` | 35 | `lib/forensicsMath.ts` (+ `pro/AuditTools.tsx`) | UI-4 audit | JSON-tree/timeline/matrix/CSV derivation + a **static safety scan** of the consuming component |
| `pro_ops_chrome.test.mjs` | 18 | `lib/opsChromeMath.ts` (+ `pro/OpsChrome.tsx`, `pro/SlTpEditor.tsx`) | UI-5 ops chrome | `computeTrend` never invents direction without a provided `prev`; `flashKeyDiffers` is strict equality (only a parent-advanced `state_version` may flash the quote tape); SL/TP editor wiring contract |

Import pattern to copy: import the **real** file with an explicit `.ts`
extension (`from "../../frontend/src/lib/fooMath.ts"`), keep the math layer
erasable-TS with zero or `import type`-only edges, and add a tiny
`registerHooks({resolve})` shim only when the module genuinely uses the `@/`
alias. No bundler, no deps, no network, no browser.

## 3. Legacy `Web/` console suites — `tests/js/*.test.js`

These were already in CI; they matter to the ALT lane because they pin the
shared vocabulary (state words, `nexus.ui.lang`, "never green-only", honest
stale/empty states) that `frontend/src` must not contradict.

| File | Tests | Module under test | Style |
|---|---|---|---|
| `cc_design.test.js` | 9 | `Web/cc_components.js` | `node:test`, source-read |
| `cc_state.test.js` | 6 | `Web/cc_state.js` | `node:test`, source-read |
| `command_center_console.test.js` | 12 | `Web/command_center_console.js` | `node:test` |
| `command_center_spatial.test.js` | 14 | `Web/command_center_spatial.js` | `node:test` |
| `command_center_timemachine.test.js` | 5 | `Web/command_center_*.js` | `node:test` |
| `command_center_ui.test.js` | 17 | `Web/command_center_ui.js` | `node:test` |
| `tv_widget_redesign.test.js` | 16 | `Web/tv_widget.js` + `index.html` + `tv_widget.html` + `tv_widget_styles.css` | `node:test`, source-read |
| `xauusd_indicator_math.test.js` | 31 | **live engine API** `GET :8080/api/v1/indicators{,/summary}` | `node:test` |
| `forensic_console.test.js` | 1 | `Web/forensic_console.js` (window shim) | script + `assert` (44 assertions) |
| `forensic_incidents_dom.test.js` | 1 | `Web/forensic_console.js` + DOM shim | script + `assert` (4) |
| `test_market_radar_ui.test.js` | 1 | `Web/app.js` `renderMarketRadar()` (vm shim) | script + `assert` (29) |

The three script-style files report `tests 1` under `node --test` because they
never call `test()` — their real signal is the assertion count in the column
above. `xauusd_indicator_math.test.js` is the only suite that touches a live
endpoint: it read-only GETs `http://127.0.0.1:8080` and **skips cleanly when the
engine is down** (CI has no engine). On a dev box with a running engine it
asserts the full 11/15/7 shape. Treat a green-but-skipped run as unverified,
not as passing.

## 4. Python-side gates that cover the same surface (not in this dir)

`pytest tests/unit/test_alt_ui_*.py` → 134 tests, all offline:
token hygiene 24 · static/auth surface 15 · supply chain 40 · XSS hygiene 14 ·
standalone host 31 · runtime contract 10. The `docs/alt-ui.md` §6 claims and the
`docs/alt-ui-*.md` security records are enforced by those files, not by this dir.

## 5. How to run / how to add

```bash
# everything, the way CI runs it
shopt -s nullglob; for t in tests/js/*.test.js tests/js/*.test.mjs; do echo "==> $t"; node "$t" || echo "FAIL $t"; done

# one suite with per-test TAP output
node --test tests/js/pro_indicators.test.mjs

# counts, in one line
for f in tests/js/*.test.js tests/js/*.test.mjs; do node --test "$f" 2>&1 | grep -E "^ℹ (tests|pass|fail)" | tr '\n' ' ' | sed "s|^|$(basename $f) |"; echo; done
```

Adding a suite:
1. New file in `tests/js/`. **Use `.test.mjs` if it imports anything from
   `frontend/src`** (needed for ESM + TS type stripping); use `.test.js` only
   for `require`-style `Web/` tests. Either extension is collected by the
   workflow glob — anything else (e.g. `.mjs.test`) is silently never run.
2. Keep it offline and browserless: stub `global.fetch`, shim `window`/
   `document`, or use the resolve hook for `@/`. No npm install step exists in
   this workflow.
3. Assert on the *real shipped module*, not a copy; for view components, assert
   structurally by reading the source (props-only, no fetch) like the `SAFETY:`
   and static-scan tests already do.
4. Add a row to §2/§3 here with the file, test count and module — a suite that
   is not inventoried is a suite someone will later assume is dead.

## 6. Verified output (evidence)

```text
pro_api_client 32/32 · pro_feed 21/21 · pro_forensics 35/35 · pro_indicators 39/39
pro_ml_viz 43/43 · pro_ops_chrome 18/18 · pro_realtime 30/30 · pro_risk_viz 21/21
cc_design 9/9 · cc_state 6/6 · command_center_console 12/12 · command_center_spatial 14/14
command_center_timemachine 5/5 · command_center_ui 17/17 · forensic_console 1/1
forensic_incidents_dom 1/1 · test_market_radar_ui 1/1 · tv_widget_redesign 16/16
xauusd_indicator_math 31/31                                    total 352 pass / 0 fail
```
