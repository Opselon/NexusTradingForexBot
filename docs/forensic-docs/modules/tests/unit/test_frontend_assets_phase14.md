# tests/unit/test_frontend_assets_phase14.py

- GUARDS: Frontend asset & boot contract (Phase 14) — regression guards for Nexus-owned browser failures: GET /api_client.js 404 → `Uncaught ReferenceError: NX is not defined`; tailwind CDN in production; broken local asset refs in index.html.
- KEY ASSERTIONS:
  - `TestNxNamespaceContract`: /api_client.js served, defines window.NX, script order NX before app, app uses NX (no fake namespace); `TestTailwindLocalBuild`: no CDN, compiled CSS exists + served + contains used colors; `TestLocalAssetsServed`: local assets 200, no broken/stale refs, webfont traversal 404, font responses never script; `TestDomContract`: every getElementById ref exists; `TestTabSectionNesting` (the tab-section topology guard): sections are siblings, nav targets have sibling sections; `TestChartHistoryContract` + `TestChartResyncContract` (48 asserts).
- PITFALLS IT ENCODES: `TestTabSectionNesting` guards the HTML tab-section topology — nav button targets must be SIBLING sections, never nested (broken nesting broke the incident tab); webfont endpoints must not serve HTML/script.
- NOTES: Reads real Web/ assets (no browser); pairs with test_playwright_e2e.py for the one real-browser smoke.
