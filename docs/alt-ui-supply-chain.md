# Alt Console — Supply-Chain & Data-Exposure Audit (SEC-3/3)

Scope: the Alternative React console under `frontend/` (TASK-ALT-UI, DEC-0002
"Node is build/dev only", BUG-047 no-CDN lineage, WEB-AUTH-P0 auth). This is the
written record of the audit; the enforced version of every claim is
`tests/unit/test_alt_ui_supply_chain.py` (40 checks, offline).

- Audit host: worktree `nse-ui-pro-ux` (gitdir: `NexusTradingForexBot/.git/worktrees/nse-ui-pro-ux`),
  branch `feat/alt-ui-pro-ux`, HEAD at close `5acf0c4c` (moved during audit — see §10)
- Date: 2026-09-13, 21:45–22:35 UTC (01:13–02:05 local). Node v24.18.0 / npm 11.6.2
- Auditor: Hermes SEC-3 lane (dependency gate, lockfile, dist hygiene, no-CDN, privacy)
- Lane constraint: READ-ONLY outside `tests/unit/test_alt_ui_supply_chain.py` +
  this doc. Sibling lanes were adding components and shipped a
  **dependency upgrade wave mid-audit** (01:52 local): React 18→19,
  Vite 6→8 (rolldown), TypeScript 5.6→7, plugin-react 4→6. Every number below
  is the post-upgrade tree unless a line says otherwise; §10 records the event.

---

## 1. Dependency allowlist / pin gate

`frontend/package.json` declares exactly **10** packages (5 runtime, 5
build/dev). The gate pins both the names AND the declared semver ranges: any
add, removal, or range drift fails `test_declared_deps_match_allowlist`.

| Package | Declared range | Lock-resolved | Role |
|---|---|---|---|
| `react` | `^19.2.8` | 19.2.8 | runtime |
| `react-dom` | `^19.2.8` | 19.2.8 | runtime |
| `react-router-dom` | `^7.18.3` | 7.18.3 | runtime |
| `@tanstack/react-query` | `^5.62.0` | 5.102.8 | runtime |
| `zustand` | `^5.0.2` | 5.0.15 | runtime |
| `typescript` | `~7.0.2` | 7.0.2 | build/typecheck |
| `vite` | `^8.2.2` | 8.2.2 | build (rolldown-backed) |
| `@vitejs/plugin-react` | `^6.1.1` | 6.1.1 | build |
| `@types/react` | `^19.2.18` | tree | types only |
| `@types/react-dom` | `^19.2.7` | tree | types only |

**Change process (binding).** A new dependency or a range edit must land in
`DEP_ALLOWLIST` in `tests/unit/test_alt_ui_supply_chain.py` **in the same commit
as** the `package.json`/lock change, with: (a) why the console needs it, (b)
who publishes it, (c) whether it carries a `bin` shim or install script (the
lockfile gate reports those). The pytest failure is the review trigger — there
is no other route to green. This process was exercised for real within minutes
of coming online; see §10.

Provenance facts verified: `private: true` (never publishable); no
`optionalDependencies`/`overrides`/`bundledDependencies` channels; no
`file:`/`git:`/`github:`/`link:` specs; scripts limited to
`dev|build|preview|typecheck` with **no** `preinstall`/`install`/`postinstall`/
`prepare` hooks; `build` runs `tsc -b &&` before `vite build` (type errors
cannot produce a dist).

## 2. Lockfile audit (`frontend/package-lock.json`)

- lockfileVersion 3, **76 package entries**, committed (`git ls-files` proves it).
- 100% of resolved entries come from `https://registry.npmjs.org/` with
  `sha512` integrity; zero link deps, zero entries missing integrity; zero
  deprecated entries; zero peer-only surprises.
- **Zero `scripts` blocks on any lock entry** (`test_no_scripts_blocks_on_any_lock_entry`).
- `hasInstallScript` — exactly 1 entry, pre-known and version-pinned:
  | pkg | ver | verdict |
  |---|---|---|
  | `fsevents` | 2.3.3 | ACCEPTED — `optional: true`, `os: [darwin]`; never installs on this Windows build host or on CI Linux. |
  The Vite 8 tree **retired the previous install-script vector**: `esbuild`
  (whose `postinstall` fetched/verified a native binary) is gone, replaced by
  rolldown's optional per-platform bindings (`@rolldown/binding-*`) which
  declare no install script. A new install-script package fails the gate.
- `bin` shims: 4 top-level (`vite`, `tsc`, `rolldown`, `nanoid`) — all
  build-time CLI shims confined to `node_modules/.bin`; none is served to the
  browser. `test_top_level_bin_hooks_are_the_known_set` fails on any *new* bin
  name (it fired on `rolldown` at 01:52 and was reviewed + accepted here with
  this rationale).
- Dev-tree reachability: every non-prod top-level package is reachable from
  vite / typescript / @vitejs/plugin-react (deps + optionalDeps traversal) —
  no orphan dev dependency in the build habitat (0 orphans on current tree).
- npm provenance/attestation per-package: **not enforceable offline** — §7.

## 3. npm audit (real run, captured)

Run on the upgraded tree at 01:57 local (22:57 UTC) from `frontend/`:

```
$ npm.cmd audit --omit=dev --json     (exit 0)
{
  "auditReportVersion": 2,
  "vulnerabilities": {},
  "metadata": {
    "vulnerabilities": { "info": 0, "low": 0, "moderate": 0, "high": 0, "critical": 0, "total": 0 },
    "dependencies": { "prod": 13, "dev": 63, "optional": 47, "peer": 0, "peerOptional": 0, "total": 75 }
  }
}
$ npm.cmd audit --json   (prod + dev)
{ "info": 0, "low": 0, "moderate": 0, "high": 0, "critical": 0, "total": 0 }   (same dep counts)
```

(The pre-upgrade tree was audited at 22:12 UTC: also 0 vulnerabilities,
prod 16 / dev 113 / total 128.)

**Triage table:**

| Sev | Package | Via | Decision |
|---|---|---|---|
| — | — | no advisories returned (prod and prod+dev, both trees) | nothing to triage; re-run required after any dependency change |

The audit reached the npm advisory endpoint successfully on this host (exit 0,
structured JSON). If it ever runs fully offline, npm returns a failure — the
correct response is to run it on a networked build host and paste the JSON,
never to assume 0.

## 4. dist hygiene — no source maps, no secrets

- `vite.config.ts`: `build.sourcemap: false` asserted by
  `test_vite_config_disables_sourcemaps` (also asserts no `sourcemap: true`/
  object form reappears, and that `base: "/alt/"` is intact).
- `dist/` scan (artifact built 21:43 UTC — **pre-upgrade**, React 18 / Vite 6;
  see staleness note): 3 files — `index.html` (481 B, sha256 `cb23ac2c5290…`),
  `assets/index-C89PXJS2.js` (314 234 B, `222364fd5c65…`),
  `assets/index-DNteOobH.css` (19 585 B, `7ed77b37d5ca…`).
  **0 `.map` files, 0 `sourceMappingURL` refs** (`test_no_sourcemap_files_in_dist`).
- Artifact-shape gate: dist may contain only `index.html` + hashed `.js/.css`
  under `assets/` — anything else (source, `.env`, `.db`, extra HTML) fails.
- Secret scan over `dist/assets/*` + `frontend/src` (59 `.ts*` files incl. the
  new sibling components) + `index.html` + `vite.config.ts`: patterns for
  `aws_access_key*`, `-----BEGIN … PRIVATE KEY-----`, `password=` literals,
  `secret=`, `token=`/bearer ≥20 chars, `NSE_WEB_AUTH_TOKEN=` values →
  **0 findings**. Second-pass high-entropy base64-shape heuristic (≥32 chars,
  `+ / =` present, entropy > 4.3) → **0 findings** (the 25 entropy > 4.0
  candidates in the bundle are React/Router camelCase identifiers, rejected by
  the shape requirement). Findings report `file:line:length` only — values are
  never echoed into test output or this doc.
- ⚠ **Staleness:** the scanned bundle predates the 01:52 dependency upgrade;
  `frontend/dist` must be rebuilt by the orchestrator before release. All
  dist-dependent tests skip cleanly when dist is absent and re-audit whatever
  is there when present, so the gate must be re-run post-rebuild (its results
  then describe the shipped artifact).

## 5. No-CDN rule (BUG-047)

- `frontend/index.html` (source): zero `http(s)://` in any `src`/`href`; the
  only script is `/src/main.tsx` (root-relative).
- Built `dist/index.html`: `src="/alt/assets/…"` — root-relative, same-origin. ✓
- `frontend/src/**`: **zero** `https?://` occurrences even before comment
  stripping — no doc URLs, let alone code URLs, in the source tree (re-swept
  after the sibling component wave at 02:00).
  CSS: no `@import`/`url()` remote refs (count of `url(` in dist CSS: 0).
- Forbidden-host sweep (`cdn.tailwindcss.com`, unpkg, jsdelivr, cdnjs,
  Google Fonts/hosts, esm.sh): **0 hits** across src, index.html,
  vite.config.ts, package.json and dist.
- Built-bundle URL literals: every scheme-bearing URL is allowlisted **by host**
  and none is a network target:

  | Host | Count | Nature |
  |---|---|---|
  | `www.w3.org` | 11 | XML/SVG/XHTML/MathML/xlink namespace identifiers (React DOM table) — never fetched |
  | `localhost` | 2 | react-router URL-parsing probe base |
  | `reactjs.org` | 1 | React error-decoder link inside an error string |
  | `reactrouter.com` | 1 | doc link inside a router warning string |

  (`server://singlefetch/` in the bundle is a react-router origin *sentinel*,
  not a fetchable URL; regex fragments like `/\//g` are artifacts of a naive
  scan — the gate is scheme-aware, see
  `test_dist_url_literals_are_allowlisted_doc_uris`.)

## 6. Privacy — operator payloads never leave the authenticated origin

Threat model: an operator payload = canonical snapshot, positions/risk/ML data,
orders/mutations, or the WEB-AUTH-P0 token. Any of these reaching a
non-backend origin is a P0 leak.

Enforced (all offline, source + dist):

1. **All transport is root-relative.** `src/api/client.ts` (re-audited after
   the sibling hardening pass that added per-request abort budgets +
   requestId fidelity — token flow unchanged) calls `fetch(path)` verbatim with
   caller-supplied `/api/...` strings — no `baseUrl`, no absolute concat. Every
   api-layer literal starts with `/api` or `/health`; the browser resolves
   every request against the serving origin (FastAPI at `/alt/`), i.e. the
   *authenticated* origin.
2. **Route existence cross-check.** Every `/api/...` literal the UI dials is
   matched against routes declared in `src/nexus_scalp/web/server.py` — the UI
   cannot call an invented endpoint (classic exfil shape) without failing the
   gate. ✓ all routes exist on the current tree.
3. **Realtime is same-origin SSE.** `realtimeSocket.ts` builds
   `/api/ticks/stream` (token from sessionStorage appended as query) — asserted
   scheme-free after comment stripping; production uvicorn is `ws="none"` and
   the bundle contains **zero** `new WebSocket(` occurrences.
4. **No out-of-band sinks.** `sendBeacon`, `new XMLHttpRequest`, GA/gtag,
   Sentry, clipboard.write: 0 in src **and** dist. Cross-window
   `window/parent/contentWindow.postMessage(`: 0. The only `postMessage` in
   the bundle is React scheduler's *MessageChannel port* yield
   (`De.postMessage(null)` — local, no data, no origin crossing); the gate is
   scoped to the cross-window forms (documented in the test) so the
   false-positive bait doesn't get the check disabled.
5. **Token containment.** The auth token is read from `?token=`, immediately
   `history.replaceState`-scrubbed from the URL, and kept **only** in
   `sessionStorage['nse.altui.token']`. Gate asserts no `token` co-located with
   `localStorage`/`document.cookie`/`IndexedDB`, and that the scrub line exists.
6. **Web-storage prefs allowlist.** localStorage keys are exactly
   `nexus.ui.lang` (shared with legacy dashboard), `nse.altui.sidebar`,
   `nse.altui.dense` — visual prefs only (constants resolved and checked;
   the new Settings "clear prefs" action only *removes* keys behind the same
   `nse.altui.` prefix + lang key — no writes beyond the allowlist, no
   operator-state payloads serialised to storage).
7. **API-layer discipline is pinned by the sibling test, not duplicated.**
   `test_alt_ui_runtime_contract.py::test_no_scattered_fetch_outside_api_layer`
   is the raw-fetch-outside-`src/api` guard; this lane asserts the guard is
   still present (`test_runtime_contract_fetch_guard_still_present`) and adds
   the orthogonal absolute-URL/sink checks.

## 7. Facts, gaps, and not-enforceable-offline items

| Item | Status |
|---|---|
| `frontend/package-lock.json` committed | ✓ pinned by gate (git ls-files) |
| `frontend/dist` gitignored | ✓ root `.gitignore:297 (frontend/dist/)` **and** `frontend/.gitignore:2 (dist/)`; `git ls-files frontend/dist` empty |
| `node_modules` never committed | ✓ `frontend/.gitignore`; root deliberately has no blanket rule (DEC-0002 note) — gate asserts no tracked files under `frontend/node_modules` |
| npm provenance/attestation registry check | ✗ needs network; not gated. Compensating: lockfile + sha512 integrity + install-script/bin allowlists |
| `npm audit` as periodic CI job | recommended (clean runs captured §3); not gated offline |
| SRI hashes on bundle refs | N/A — no cross-origin refs exist; `/alt/` is served by the same FastAPI origin |
| dist rebuilt after the upgrade wave | ⚠ pending — see §4 staleness note; re-run this gate after rebuild |
| `import.meta.env.BASE_URL` (`main.tsx`) | Vite build-time constant → `/alt/`; dev-only `NSE_API_ORIGIN` proxy target never baked into build output (asserted) |

## 8. Findings

**No open P0/P1 findings in this lane.** Notes/observations:

1. *(Medium→Low, process)* Dependency upgrade wave (React 19 / Vite 8 / TS 7 /
   plugin-react 6) landed **mid-audit via sibling lane without an
   allowlist-consistent commit**. The gate caught it (see §10); reviewed and
   accepted: no new package names, one new accepted bin shim (`rolldown`), one
   *removed* install-script vector (esbuild). `DEP_ALLOWLIST` updated to match.
   Follow-up for the release owner: rebuild dist + re-run this gate + one more
   `npm audit` before shipping (both cheap).
2. *(Low, sibling-lane WIP — reported, not fixed)* the combined run at close
   shows `test_alt_ui_runtime_contract.py::test_no_scattered_fetch_outside_api_layer`
   RED on `components/pro/OpsChrome.tsx` + `SlTpEditor.tsx` — the strings are
   inside **comments** ("No fetch() anywhere in this file"), and that sibling
   test (not this lane's file; READ-ONLY constraint) matches raw text. Either
   the sibling rewords the comments or its test strips comments; the
   underlying rule is not actually violated (no raw fetch calls exist —
   verified by grep).
3. *(Info, accepted)* `fsevents` postinstall remains the only install script
   in the graph; darwin-optional, inert here; explicit allowlist pin.
4. *(Info)* `?token=` in the URL is a known WEB-AUTH-P0 affordance (SSE needs
   it); client scrubs immediately, sessionStorage only. Leaked URL at the
   moment of paste remains operator hygiene — documented, out of frontend scope.
5. *(Info)* ranges like `^19.2.8` permit minor drift; resolution is lock-pinned.
   Intentional: range edits must pass §1's process.
6. *(Process)* `agents/taskboard.md` TASK-ALT-UI row mandates dist-gitignored +
   Node-build-only; this doc + gate make it mechanically enforced.

## 9. Automated gate

`tests/unit/test_alt_ui_supply_chain.py` — 40 tests, offline, repo venv only
(`NexusTradingForexBot/.venv`, Python 3.11.16, pytest 9.1.1). This lane's file
in isolation at close:

```
$ C:/Users/Capsizer/source/repos/NexusTradingForexBot/.venv/Scripts/python.exe -m pytest tests/unit/test_alt_ui_supply_chain.py -ra --tb=short
........................................                                 [100%]
40 passed in 3.50s
```

Cross-run with the sibling suite at close (22:34 UTC):

```
$ C:/Users/Capsizer/source/repos/NexusTradingForexBot/.venv/Scripts/python.exe -m pytest tests/unit/test_alt_ui_supply_chain.py tests/unit/test_alt_ui_runtime_contract.py -ra --tb=line
........................................Fsssss.....  ... [100%]
SKIPPED [1] tests\unit\test_alt_ui_runtime_contract.py:54: live-backend contract test (start a test backend and set ALT_UI_LIVE_BACKEND=1)
SKIPPED [1] tests\unit\test_alt_ui_runtime_contract.py:84: live-backend contract test (start a test backend and set ALT_UI_LIVE_BACKEND=1)
SKIPPED [1] tests\unit\test_alt_ui_runtime_contract.py:106: live-backend contract test (start a test backend and set ALT_UI_LIVE_BACKEND=1)
SKIPPED [1] tests\unit\test_alt_ui_runtime_contract.py:114: live-backend contract test (start a test backend and set ALT_UI_LIVE_BACKEND=1)
SKIPPED [1] tests\unit\test_alt_ui_runtime_contract.py:119: live-backend contract test (start a test backend and set ALT_UI_LIVE_BACKEND=1)
FAILED tests/unit/test_alt_ui_runtime_contract.py::TestUiSourceContract::test_no_scattered_fetch_outside_api_layer
1 failed, 44 passed, 5 skipped in 4.30s
```

Reading: **this lane's 40/40 pass**; the single RED is the *sibling's own*
comment-vs-regex false positive in their WIP files (§8.2), not a supply-chain
violation. 5 skips are the sibling suite's opt-in live-backend tests
(`ALT_UI_LIVE_BACKEND=1`). Negative controls (synthetic allowlist drift,
synthetic AWS key / password / bearer literals, foreign-URL host, cross-window
postMessage) all confirmed to FIRE on injected violations without ever
printing secret values.

## 10. Audit event log (what the gate caught while this audit was open)

- 21:43 UTC — orchestrator build produced the dist scanned in §4.
- 22:12 UTC — first clean `npm audit` on the React 18 / Vite 6 tree (128 pkgs).
- 22:32 UTC — sibling lane staged `package.json`/`package-lock.json` upgrade:
  `test_declared_deps_match_allowlist` **FAILED** (range drift on 7 packages)
  and `test_top_level_bin_hooks_are_the_known_set` **FAILED** (new `rolldown`
  bin). This is the designed behaviour: the upgrade was invisible to prose and
  instant to the gate.
- 22:33 UTC — gate allowlist updated with review (this doc §1–§2);
  `test_install_phase_scripts_are_the_known_set` re-verified the esbuild
  removal and the fsevents-only residue.
- 22:33–22:35 UTC — full re-run of all 40 gate checks green; fresh
  `npm audit` on the upgraded tree clean (§3).
