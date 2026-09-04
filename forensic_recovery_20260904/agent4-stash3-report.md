# Forensic Deep Audit — stash@{3} (hold-302, e90013d5, base 66555ea7)

**Agent:** 4 of 6 · **Date:** 2026-09-04 · **Main HEAD at audit:** `694ee2b2` (via `a5e2ccc4` ruff heal)
· Read-only — no pop/drop.

---

## 1. `git diff stash@{3}^1..stash@{3} --stat` — Full Inventory

- **Stash identity:** `stash@{3}: On main: hold-302` · commit `e90013d5` · base `66555ea7` (`fix(critical) test_70d_model_31 lineage override`)
- **Headline count:** **342 files** · `1030 insertions(+), 3959 deletions(-)` (shortstat).
- **Task brief claimed 334 — actual is 342** (verified twice via `--stat` and `--shortstat`; `stash show` agrees).
- **Decomposition (by prefix):**
  - `site` — 339 (of which `site/_site` = 338)
  - `scripts/docs/build_site.py` — 1
  - `site/assets/search.js` — 1
  - `src/nexus_scalp/model_generation/three_model.py` — 1
  - `docs/project/status.md` — 1
  - Non-`site/_site` distinct files = **4** (docs, scripts, src, site/assets) — the only substantive patch; `site/_site` is regenerated output.
- **Raw numstat for the 4 substantive paths:**
  - `docs/project/status.md` · `2 / 2`
  - `scripts/docs/build_site.py` · `11 / 604`
  - `site/assets/search.js` · `0 / 1504`
  - `src/nexus_scalp/model_generation/three_model.py` · `4 / 1` (net +3 line change, -1/+4 hunks)

### 1a. `status.md`

```diff
-| Version | **9.0.6** … | …
-| Release | **Published** (v9.0.0 → v9.0.6 tags; …) |
+| Version | **9.0.8** … | …
+| Release | **Published** (v9.0.0 → v9.0.8 tags; …) |
```

Simple version bump 9.0.6 → 9.0.8 (the `pyproject.toml` single-source drift that `9bb9f692` also fixed). On current main this cell now reads **9.0.9** — superseded.

### 1b. `build_site.py` — 11+/604–

Two logically independent hunks collapsed in one commit:

1. **docs-enhance wiring (real feature, +11/-1):**
   - `shell()` template: inserts `<link … docs-enhance.css>` after `styles.css` and `<script … docs-enhance.js defer>` before `search.js`.
   - `main()` copy loop: adds `src_enhance_css / src_enhance_js` locals and expands `for src,dst in ((…),(…))` from 3 to 5 entries, guarded by `if src.exists():`.
2. **FLAG_BUILD_INDEX deletion (−604 including the 3 wiring lines counted net 11):**
   - Deletes the unreachable tail after `raise SystemExit(main())`: the `FLAGSHIP 9000 BUILD HELPERS` block with 600 `FLAG_BUILD_INDEX_0000…0599` constants (the diff shows ~593 shown plus the header — the earlier probe counted 615 via a different stat base; measured `0/604` here on the stash vs `0/602` on `ebee9b83` — delta is the 2 wiring additions counted in the same stat line; effective FLAG removal is ~602 lines).

### 1c. `site/assets/search.js` — 0/1504

- Deletes the `FLAGSHIP 9000 JS PREMIUM PACK` tail: `void 0; /* flag-js-0000 … 1499 */` dead noops appended after the real `})();` terminator. The real engine (pre-FLAG) was the 302-line `flag-js` era engine (the `1806`-line era when counted with the FLAG tail).
- This is **not** the same content as `main`'s current `site/assets/search.js`:
  - `main@694ee2b2` is the `b50f85a6` cinematic JS v2 — **2,849 lines** (`flag-js` → replaced, `nx-reserved-0000…0419` pattern now, 420 dummy `void 0` lines under a different flag system). `grep flag-js` = 0 on main; `grep nx-reserved` = 420. So the FLAG system in stash@{3} no longer exists on main — the FLAG deletion would not cleanly apply to the current file without adjustment.

### 1d. `site/_site` — 338 files, ~`3/1` each (4–6 line churn)

Every generated HTML shows the same pattern:

- `<link … docs-enhance.css>` insertion in `<head>`.
- `<script … docs-enhance.js defer>` insertion before `search.js`.
- `rev d267fbd → 66555ea` footer bump + `rev` chip bump (the base commit's rev reflected in each page).
- `search-index.json` / `site-meta.json` bumps.

This is **pure build output** — regeneratable by `python scripts/docs/build_site.py`. Tracked on main only because the repo historically commits `site/_site` (346 tracked files today) despite `.gitignore` listing `site/_build|public|cache` (not `_site` itself).

### 1e. `three_model.py` — 4+/1–

```diff
-    return compute_70d_frame(bars_frame, min_bars=min_bars, news_frame=news_frame)
+    from nexus_scalp.model_generation.schema_v2_incremental import compute_70d_frame_fast
+
+    return compute_70d_frame_fast(bars_frame, news_frame=news_frame)
```

Drops the `min_bars` passthrough (fast builder ignores it / internal window). This is the **70d-variant arm only** (the `else` branch); the `50d_main` arm was unchanged in this stash.

---

## 2. Compare `stash@{2}` vs `stash@{3}` `three_model.py` Patch — Byte-Identical?

**Verdict: YES — byte-identical duplication.**

Evidence:

- `git diff stash@{2}^1..stash@{2} -- three_model.py` SHA-256: `330114bb5b73c6c71fdde93df1a207363617943244ca9871b0e6e6e54a01bc61`
- `git diff stash@{3}^1..stash@{3} -- three_model.py` SHA-256: identical hash; `diff` exit 0.
- Both patches: `index d4779ccb..09ba4df0`, same 4-line hunk, same dropped `min_bars`, same bare `from … import compute_70d_frame_fast` inside the branch.

Context of `stash@{2}` full diff: `stash@{2}` (`hold-303-docs-site-three_model`) touched **3 files** (`status.md 4/4, build_site.py 12/1, three_model.py 4/1`) — a lightweight hold that also carried the same docs-enhance wiring (offset by one line vs stash@{3}'s count due to the FLAG tail being counted differently) plus the same fast-fix. So **stash@{2} and stash@{3} redundantly preserved the same BUG-106 fast-fix**.

The main integration path recovered this fix once via `6bb76497` → `3179df9c` (see §5). Stash@{3} does not add a second distinct fix.

---

## 3. FLAG Removal Already Done on Main via `ebee9b83` — Duplicate?

**Verdict: DUPLICATE (functionally) — with a nuance on `search.js`.**

### 3a. `build_site.py` FLAG tail

- **Main commit `ebee9b83`:** `forensic(P3): remove 600-line FLAG_BUILD_INDEX dead tail` · `0/602` · `1949 → 1347 lines` (message says 1347; working tree now 1356 due to the later `54227f52` wiring + `a5e2ccc4` format heal).
- **Stash@{3} FLAG tail:** `0/604` gross (−604) but that stat includes the +11 wiring lines; effective FLAG deletion is the same ~600 `FLAG_BUILD_INDEX_*` lines starting from the same base index `c8320277` through `FLAG_BUILD_INDEX_0205+` (the 7948b6b4 flagship injection). **Byte-for-byte the same dead tail** — `grep FLAG_BUILD_INDEX` on main today = 0; applying stash@{3}'s deletion to main would be a no-op / already-applied hunk. The `ebee9b83` commit message even calls this out explicitly: "re-introduced by stashed holds (stash@{3}/@{4})".
- **Conclusion:** stash@{3}'s `build_site.py` FLAG removal is **DUPLICATE** of `ebee9b83` — do not re-apply.

### 3b. `site/assets/search.js` FLAG tail

- **Stash@{3}:** deleted `1504` lines of `flag-js-0000…1499` dead tail (the `1806` era).
- **Main current `search.js`:** **2,849-line** cinematic engine (`b50f85a6`), **`flag-js` count = 0**, `nx-reserved` = 420.
- The old `flag-js` FLAG tail **does not exist on main anymore** — it was implicitly removed when `b50f85a6` / `24fda7e5` replaced the engine (and possibly healed during P3 regen). The current engine has its own `nx-reserved` placeholder system (420 lines) that is **not dead code of the same kind** — it is intentionally retained as reserved slots stitched by the cinematic subagent (not unreachable after `SystemExit`).
- **Conclusion for `search.js`:** stash@{3}'s 1504-line deletion is **REJECT — stale/obsolete**. It targets a file version that no longer exists on main; re-applying the hunk would fail to apply (or would delete the wrong tail). Any future `search.js` cleanup should be evaluated against the current `nx-reserved` system, not this 1806-era `flag-js` payload. If a re-audit deems `nx-reserved` deletable, that is a **separate decision** — not recoverable from this stash's hunk.

---

## 4. docs-enhance Wiring Already Integrated via `54227f52` — Duplicate?

**Verdict: DUPLICATE — byte-identical wiring, already on main.**

- **`54227f52` on main:** `forensic(P3): wire docs-enhance assets in build_site.py shell + copy` · `11/1` · adds the same 6 wiring lines:
  - `<link … docs-enhance.css>`
  - `<script … docs-enhance.js defer>`
  - `src_enhance_css = SITE_DIR / "assets" / "docs-enhance.css"`
  - `src_enhance_js  = SITE_DIR / "assets" / "docs-enhance.js"`
  - `(src_enhance_css, "docs-enhance.css"),`
  - `(src_enhance_js, "docs-enhance.js"),`
- **Stash@{3} wiring lines:** `grep docs-enhance | asset_href.*enhance` output is **identical** — same 6 lines, same quoting, same placement.
- **Current main verification:** `grep -n docs-enhance scripts/docs/build_site.py` = 6 hits (lines 764, 806, 1185, 1186, 1191, 1192) — wired.
- **Merge integration:** `54227f52` was merged into main via `3d8dd752` (`forensic(P3): integrate build_site cleanup`) alongside `ebee9b83`, then `4261c3d2` regenerated `site/_site` with the wiring.
- **Conclusion:** stash@{3}'s docs-enhance wiring is **DUPLICATE** of `54227f52` — do not re-apply. Verified end-to-end (commit message claims `build_site.py --out` + `check_docs.py DOCS_HEALTH PASS` — re-verified wiring presence on current HEAD).

---

## 5. Per-Component Verdict (INTEGRATE / DEFER / REJECT / DUPLICATE)

| # | Component | Files / Lines | Verdict | Rationale |
|---|-----------|---------------|---------|-----------|
| **A** | `docs/project/status.md` v9.0.8 bump | `2/2` | **REJECT** (superseded) | Main already at **9.0.9** (`pyproject.toml` + `status.md:19`). Re-applying 9.0.8 would be a **version regression**. No evidence value — keep current 9.0.9. |
| **B1** | `build_site.py` docs-enhance wiring | `+11/−1` (within `11/604`) | **DUPLICATE** | Byte-identical to `54227f52` (6 wiring lines). Main already wired (6 hits on `694ee2b2`). End-to-end verified via `4261c3d2` regen. |
| **B2** | `build_site.py` FLAG_BUILD_INDEX deletion (600-line dead tail) | `~0/602` | **DUPLICATE** | Same unreachable tail after `SystemExit`, same base `c8320277`. Already removed by `ebee9b83` (`1949→1347`, now `1356` with wiring). `grep FLAG_BUILD_INDEX` = 0 on main. |
| **C** | `site/assets/search.js` FLAG deletion (1504 lines) | `0/1504` | **REJECT** (stale/obsolete) | Targets the **1806-era `flag-js` engine** (base `66555ea7` era). Main now runs the **2,849-line cinematic JS v2** (`b50f85a6`) where `flag-js = 0` and the `nx-reserved` 420-line system is a **different artifact**. Hunk would not cleanly apply; any `nx-reserved` cleanup is a new decision, not recoverable from this stale hunk. |
| **D** | `src/…/three_model.py` 70d fast-fix (`compute_70d_frame_fast`) | `4/1` | **DUPLICATE** | Byte-identical to stash@{2}'s fix (SHA-256 `330114bb…`). Already integrated via `6bb76497` → merge `3179df9c` (BUG-106 extension; ruff + `test_three_model_pipeline 5/5`). Current main has **both** arms on fast (50d + 70d) and even removed the dead top-level `compute_70d_frame` import (so main is a strict superset of this stash's hunk). |
| **E** | `site/_site` regeneration (338 files) | `~1014/338` (~3/1 each) | **REJECT** (generated output) | Build artifact, not source. Main already regenerated via `4261c3d2` (`v9.0.8→9.0.9`, wiring injected, `rev d267fbd→…`). Re-applying a `66555ea`-era snapshot would **regress** rev/ version metadata. Never cherry-pick `site/_site` into source history — regenerate instead. |

### Overall stash verdict: **NO INTEGRATION REQUIRED**

- All **source-bearing** changes (B1, B2, D) are **DUPLICATE** — already on main via the audited forensic P3 path (`ebee9b83` + `54227f52` → `3d8dd752` → `4261c3d2` + `6bb76497` → `3179df9c`).
- All **non-source** changes (A, C, E) are **REJECT** (superseded / stale / generated).
- **No REJECTED-but-wanted content was missed:** no unique logic, no SiF docs, no Prompt-Coach wiring, no DSM CLI work in this stash — those live in stash@{0}/@{5} and newer work (hold-302 predates them).
- Stash@{3} correctly remains **intact as a forensic snapshot** — do not drop.

---

## 6. Cross-References & Evidence

- **Counts:** `342 files, 1030+/3959−` (verified via `git diff … --stat/--shortstat` and `stash show`; 334 in the task brief is off by 8 — likely counted pre-rebase).
- **three_model byte identity:** `diff` exit 0, SHA-256 `330114bb…` both stashes — see §2 tmp patches.
- **FLAG removal proof:** `grep FLAG_BUILD_INDEX` on `HEAD` = 0; `ebee9b83` diff starts at same base `c8320277` through tail.
- **docs-enhance proof:** `grep docs-enhance` on `HEAD` = 6 hits; `54227f52` diff 6 wiring lines identical to stash@{3} subset.
- **search.js staleness proof:** `HEAD site/assets/search.js` = 2849 lines, `flag-js` 0, `nx-reserved` 420; stash@{3} base/search counts 1806→302 lines.
- **Ancestry:** `66555ea7` (stash base) is ancestor of `694ee2b2` (HEAD); stash@{3} is an **older hold** that predates `b50f85a6` cinematic engine — explaining why its `search.js` FLAG target no longer exists.
- **Relevant commits on main that subsume this stash:**
  - `ebee9b83` FLAG removal (build_site)
  - `54227f52` docs-enhance wiring (build_site)
  - `3d8dd752` merge of those two (integrate build_site cleanup)
  - `4261c3d2` site regen (heals wiring + v9.0.9 drift)
  - `6bb76497` + `3179df9c` three_model fast-fix (BUG-106)

---

## 7. Risks If Mis-Handled

- Re-applying **A** (9.0.8) would silently regress the published version stamp — breaks the single-source invariant and DOCS_HEALTH.
- Blindly applying **C** or **E** would overwrite the cinematic engine / regenerated site with an older snapshot — risks losing the 2,849-line engine and 9.0.9 metadata.
- The only **latent risk** is if someone mistakes `nx-reserved` for dead code to delete — that requires its own audit (not this stash's authority).

---

*Read-only audit. Stash@{3} left intact. Report saved to `forensic_recovery_20260904/agent4-stash3-report.md`.*
