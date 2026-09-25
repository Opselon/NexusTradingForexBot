# DASHBOARD ASSET FORENSICS — Phase 14 Completion

**Date:** 2026-08-17
**Verification basis:** live TestClient requests against `create_app()` + node syntax checks + DOM id contract scan.

---

## 1. Local asset references in `Web/index.html`

| Asset | Source type | Exists on disk | Served (HTTP) | Served by | Dependency | Failure observed | Fix |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `/tailwind.css` | `<link rel=stylesheet>` | ✅ 29,494 bytes | ✅ 200 | `server.py::serve_tailwind` | CSS (compiled locally) | CDN warning `cdn.tailwindcss.com should not be used in production` | Removed CDN `<script>`; compiled locally via `npx tailwindcss -i Web/tailwind_input.css -o Web/tailwind.css --minify`; theme config moved to `tailwind.config.js` |
| `vendor/fontawesome/all.min.css` | `<link rel=stylesheet>` | ✅ | ✅ 200 | `server.py::serve_fa_css` | CSS (local vendor) | external CDN dependency | Downloaded FontAwesome 6.4.0 locally under `Web/vendor/fontawesome/` |
| `vendor/webfonts/fa-*.woff2/ttf` | CSS `url(../webfonts/...)` | ✅ 8 files | ✅ 200 | `server.py::serve_fa_webfont` | Font files | CSS `url()` refs failed asset audit | Moved to `Web/vendor/webfonts/` (correct relative resolution from `/vendor/fontawesome/all.min.css`) + route added |
| `/styles.css` | `<link rel=stylesheet>` | ✅ 1,690 bytes | ✅ 200 | `server.py::serve_styles` | CSS | none | — |
| `/api_client.js` | `<script src>` (line 1483) | ✅ 3,894 bytes | ✅ 200 | `server.py::serve_api_client` | JS — defines `window.NX` | **GET /api_client.js 404 → `Uncaught ReferenceError: NX is not defined` at app.js:402** | Route added; file verified to define `window.NX` + `NX.api`; loads BEFORE app.js (script order verified) |
| `/app.js` | `<script src>` (line 1484) | ✅ 138,322 bytes | ✅ 200 | `server.py::serve_app` | JS — dashboard logic | none after api_client.js fix | — |

**CSS `url()` audit (FontAwesome):** all 8 `url(../webfonts/...)` references resolve to real files (verified by `tests/unit/test_frontend_assets_phase14.py::test_color_css_url_assets_exist` + path check). No `data:` or remote URLs.

**External references remaining in index.html:** NONE. `grep -n "cdn" Web/index.html` → 0 hits. Dashboard works offline/local.

---

## 2. Root cause chain for the NX error (ERROR 1)

```
index.html:1483  <script src="api_client.js">   ->  GET /api_client.js
web/server.py    (no route for /api_client.js)  ->  404
browser          api_client.js body = "Not Found" -> window.NX never defined
app.js:402       NX.api.get('/api/chart/history') -> Uncaught ReferenceError: NX is not defined
```

Fix: added `@app.get("/api_client.js")` → `FileResponse(WEB_DIR / "api_client.js")`. The module was NOT obsolete — 23 `NX.` call sites in app.js depend on it; `api_client.js` is the central API client (request correlation, safe error parsing, deduped GETs; see BUG-040 lineage).

## 3. webextension.js:26 TypeError (ERROR 4)

- **Not a repository file**: `ls Web/` contains only `index.html`, `app.js`, `api_client.js`, `styles.css`, `tailwind.css`, `tailwind_input.css`, `vendor/`.
- **Not referenced by index.html**: script-src audit shows zero reference.
- **Classification: EXTERNAL_BROWSER_EXTENSION noise.** No application code was modified to address it.

## 4. Script dependency audit (repository-wide)

| Script | Loaded by | Defined by | Order guarantee | Status |
| :--- | :--- | :--- | :--- | :--- |
| api_client.js | index.html:1483 | itself (`window.NX`) | loads first | ✅ served + verified |
| app.js | index.html:1484 | itself (`initApp`) | after NX | ✅ served |
| tailwind.css | index.html head | local build | n/a | ✅ |
| vendor FA css/webfonts | index.html head + css | local vendor | n/a | ✅ |

No duplicate script loading, no dynamic `import()`, no stale references.

## 5. DOM contract

`getElementById()` calls in app.js: **137**; ids defined in index.html: **209**. Missing: **0** (regression test `test_all_getelementbyid_refs_exist`). `initApp()` cannot null-deref on boot.

## 6. JS syntax validation

- `node --check Web/api_client.js` ✅
- `node --check Web/app.js` ✅

---

## 7. Verification (regression tests)

`tests/unit/test_frontend_assets_phase14.py` — **24 tests**:
- NX namespace: api_client served, defines window.NX, script order (api before app), no fake NX in app.js
- Tailwind: no cdn.tailwindcss.com, compiled css exists + served + contains theme colors
- Local assets: 8 parametrized 200s, no broken index.html refs, no stale scripts, FA webfont url() files exist
- DOM contract: all getElementById ids exist
- Chart history contract: response shape, no synthetic source, safe error payload

All green (`24 passed`).