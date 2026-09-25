# Web/index.html

- **PURPOSE:** The NSE Control Center markup — 13 interactive tabs
  (monitoring, account, rules, config, news, research, factory,
  governance, health, incidents, liquidity, ai-analysis, debug) over a
  dark glassmorphism layout (Tailwind compiled LOCALLY — no runtime CDN;
  FontAwesome vendored — offline/deterministic).
- **ARCHITECTURE LAYER:** Web UI (view).
- **RESPONSIBILITY:** (a) static structure + id hooks consumed by app.js;
  (b) the `.tab-content` section topology — each tab is a TOP-LEVEL
  `<section id="tab-X">` sibling (the div/section balance fragility,
  BUG-068/BUG-120: an extra/missing `</div>` silently collapses the whole
  layout; a nested `.tab-content` section hides a whole tab at 0x0 with no
  console error — guarded by TestTabSectionNesting);
  (c) header: system-status badge, state-version indicator, health badge,
  obs strip (REST/SSE/version).
- **DEPENDENCIES:** tailwind.css (built), styles.css, app.js,
  api_client.js, vendor/fontawesome.
- **CONNECTS TO:** app.js (all interactivity), the API surface.
- **KEY CONCEPTS:** markup is LF (pure); NEVER re-indent wholesale (the
  file is hand-maintained with inconsistent indentation — mechanical
  reformatting risks div-balance breaks); every tab is a pure renderer of
  API data (never computes trading intelligence in JS).
- **EDGE CASES & PITFALLS:** after ANY edit, run the strict html.parser
  balance check + TestTabSectionNesting; missing `</section>` on one tab
  hides ANOTHER tab silently (BUG-120).