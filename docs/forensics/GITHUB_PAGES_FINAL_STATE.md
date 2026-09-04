# GITHUB PAGES FINAL STATE — 2026-09-04

## 1. Is `site/_site` intentionally tracked?

**Yes — intentionally tracked.** `git ls-files site/_site | wc -l` is 100s of committed HTML files (`site/_site/.nojekyll`, `site/_site/404.html`, `site/_site/ar/**`, etc.). `.gitignore` explicitly lists only `site/_build/`, `site/public/`, `site/cache/` (Nexus-Docs) — **not** `site/_site`. The workflow `site/_site` is a deliberate GitHub Pages artifact kept in-tree for PR preview alongside CI-built deploy.

## 2. What is the canonical source?

`site/**` (multilingual Markdown under `site/content` + `site/assets/styles.css`/`search.js`/`docs-enhance.*`/`pics`) plus `docs/**` (IA + project docs) rendered by `scripts/docs/build_site.py` via the `shell()` + `main()` + `homepage_html()` pipeline. Single-source language catalog + nav config determine output.

## 3. What command generates it?

Official build mechanism (`.github/workflows/docs.yml` — `validate` + `deploy` jobs):

```bash
python scripts/docs/fetch_releases.py
python scripts/docs/build_site.py          # → site/_site (334 pages, 5 langs)
python scripts/docs/check_docs.py          # doctor: links/anchors/RTL/drift/assets
python scripts/docs/check_translations.py  # site/content/<lang> parity
# deploy: actions/configure-pages + upload-pages-artifact path: site/_site → deploy-pages
```

Local regeneration (used in this disposition): `python scripts/docs/build_site.py [--out <tmp>]` then `python scripts/docs/check_docs.py`.

## 4. Was it regenerated after the final source changes?

**Yes.** P3 `forensic/p3-build-site-cleanup` changed `scripts/docs/build_site.py` (FLAG tail removal `ebee9b83` 1949→1347 + docs-enhance wiring `54227f52`). Site was regenerated from that source:

* `site/_site` committed as `4261c3d2` `docs(site): regenerate site/_site (v9.0.9 drift heal + docs-enhance wiring)` — `334` pages, `5` langs, `v9.0.9` brand badge/rev, `docs-enhance.css/js` wired in every page.
* Subsequent `a5e2ccc4`/`979624fc` are md-only (ruff/format + forensic reports) and do not change site sources, so `_site` at `29a8ebb9` remains current.

## 5. Does docs health pass?

**DOCS_HEALTH = PASS.** Latest `python scripts/docs/check_docs.py` before disposition: `PASS` (links ✓, translations ✓, RTL+switcher ✓, SEO ✓, perf budget ✓, mermaid ✓, site build ✓, assets ✓, secrets clean, version drift `single source v9.0.9`). `ruff check` + `ruff format --check` + `mypy` on `scripts/docs/` clean. `py_compile build_site.py` clean. Import `build_feature_frame` ok. Temp `--out C:/tmp/site_test` emits `docs-enhance.css/js` correctly. Prior to `4261c3d2`, `check_docs` was `FAIL` on `9.0.8` vs `9.0.9` drift — healed.

## 6. Is the version consistent?

`pyproject.toml` `version = "9.0.9"` is single source. `_site` brand badge now `v9.0.9` (was `v9.0.8` before P3 regen), `site-meta.json` drift healed. `check_docs` drift gate passes.

## 7. Are docs-enhance assets correctly wired?

**Yes.** Assets exist (`site/assets/docs-enhance.css` 77K, `site/assets/docs-enhance.js` 55K). `scripts/docs/build_site.py` now wires both: `shell()` injects `<link rel='stylesheet' href='{asset_href(rel, "docs-enhance.css")}'` + `<script src='{asset_href(rel, "docs-enhance.js")}' defer>` (verified in `_site` HTML, 2 hits per page), `main()` copy loop now emits them via `if src.exists()` guarded `shutil.copyfile`. Verified in both `site/_site/assets/` and `C:/tmp/site_test/assets/`. Guard ensures missing assets degrade gracefully.

## 8. Is stale FLAG/search content gone where appropriate?

On `main`: `grep -c FLAG_BUILD_INDEX` in `scripts/docs/build_site.py` = `0` (tail unreachable after `SystemExit` removed). `grep -c "flag-js-"` in `site/assets/search.js` = `0` (FLAGSHIP filler era removed; current cinematic JS v2 `b50f85a6` uses `nx-pad-*` bridge stubs, `grep -c flag-js` `0`, `grep -c nx-pad` `420`). Stash `@{1}/@{3}/@{4}` would have resurrected stale `flag-js` (would truncate `2849→302`); stash `@{5}` prototype CSS/JS superseded by flagship `7150e3de`/`b50f85a6`/`7948b6b4` (`+6865` `site/assets` vs `16e86d70`). All 4 stale FLAG/search hunks were rejected per 6-agent audit.

## 9. Are generated artifacts consistent with current `main`?

**Yes.** Rebuild from current `main` reproduces committed `site/_site` deterministically (modulo CRLF normalization). No old model code reintroduced. No `live_engine.py`/`sequence.py` delta (model path is `three_model.py` `compute_70d_frame_fast` already integrated at `3179df9c`, byte-identical per `schema_v2_incremental` docstring).

## 10. Is any Pages deployment change still pending?

**No deployment config change pending.** `docs.yml` already builds fresh in both `validate` and `deploy` jobs, uploads `site/_site` as `actions/upload-pages-artifact` path `site/_site`, and runs `live_smoke.py` post-deploy. The committed `_site` tracks the deployed output for preview but CI does not depend solely on committed output. No `.gitignore` change required during this cleanup (the task explicitly forbids a large Pages architecture change here).
