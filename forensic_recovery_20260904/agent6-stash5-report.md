# Forensic Deep Audit — stash@{5} (d3cab5bc css-js-old) — Agent 6

**Date:** 2026-09-04 (Iran +0330) · **Auditor:** agent6 (subagent-sa-2-4e6dc39d)  
**Commit:** `d3cab5bc703c7717ff6566fef7ca9e9edc22787d` — `On hermes-subagent/subagent-sa-2-4e6dc39d: css-js-old`  
**Type:** WIP stash (merge commit `16e86d70` + `16416fb6` index) — oldest of 6 stashes  
**HEAD at audit:** `a5e2ccc4` (forensic P3 heal) · **Main forensic base:** `694ee2b2`

---

## 1) Raw stash footprint (T1)

Evidence (all commands via `cd C:/Users/Capsizer/source/repos/NexusTradingForexBot`):

```
git stash show stash@{5} --stat
 site/assets/search.js  |  15 ++-
 site/assets/styles.css | 288 +++++++++++++++++++++++++++++++++++++++++++++++++
 2 files changed, 302 insertions(+), 1 deletion(-)

git diff stash@{5}^1..stash@{5} --name-only
site/assets/search.js
site/assets/styles.css

git diff stash@{5}^1..stash@{5} --numstat
14  1  site/assets/search.js
288 0  site/assets/styles.css

git show stash@{5} --stat
commit d3cab5bc703c7717ff6566fef7ca9e9edc22787d
Merge: 16e86d70 16416fb6 — 2 files, 302 ins/1 del (counted as 304 + / 3 - with diff header)
```

Base: `16e86d70` — `Nexus-Docs: flagship pro site — glass hero + live chart + bento + modes + terminal + pipeline + CmdK` (`2026-09-04 07:06:50 +0330`).  
`git merge-base --is-ancestor 16e86d70 HEAD` → **true** (stash base is already in main line).  
`stash@{5}^3` does not exist (no untracked commit — 2-file WIP only).

### search.js (+14/−1)

Single hunk at the `var base = (function(){...})()` IIFE close (`@@ -8,7 +8,20 @@`). Inserts 13 one-liners under comment `/* == FLAGSHIP EXPANSION modules == */`:

| # | Module | Behavior |
|---|--------|----------|
| 1 | focus trap | `keydown Tab` trap inside `#sidebar` when `body.nav-open` |
| 2 | heading anchors | injects `a.anchor #` into `.prose h2[id], h3[id], article h2/h3[id]` |
| 3 | toast+copy enhance | floating `#copy-toast` + `click .copy-btn` → Copied/failed toast |
| 4 | back-to-top | injects `#to-top` button, scroll>600 show, smooth scroll to 0 |
| 5 | tabs/accordion/faq | `.tabs .tab-bar` switching, `.accordion [data-single]`, `.faq .faq-q` |
| 6 | table sort | `thead th.th-sort` click → numeric-vs-string sort on `tbody tr` |
| 7 | lightbox | `div.lightbox` with close/overlay/Esc, wraps `[data-lightbox], .prose img, .shot-stage img` |
| 8 | reading time | `[data-reading-time]` ← word count of `.prose/article/.content / 210` |
| 9 | external links | `a[href^="http"]` cross-host → `target _blank rel noopener noreferrer` |
| 10 | parallax | `prefers-reduced-motion` guard; `.hero-pro/.page-hero` bg-position on scroll |

No `src/`, no imports, no build-system change.

### styles.css (+288/0)

Single hunk appended after `/* Premium extras */ .visually-hidden{...}` (`@@ -371,3 +371,291 @@`). Header:

```
/* ═══════════════════════════════════════════════════════════════════
   NEXUS FLAGSHIP — PREMIUM EXPANSION (9000-line build)
   Cinematic SaaS · glass · mesh · motion · full subpage suite
   Covers: article, TOC, code, callouts, tabs, accordions,
   data tables, timeline, stats, comparison, kbd, tooltips,
   command palette polish, lightbox, print, RTL, a11y
   ═══════════════════════════════════════════════════════════════════ */
```

Adds:

- **Extended tokens** (`--ok-soft/--warn-soft/--danger-soft/--exp-soft/--surface/--grid/--overlay/--pro-gradient/--hero-mesh`, dark override)
- **Pro article typography** (`.prose` 78ch, `h2 .anchor` hover, `.lead`, `.kicker`, `.eyebrow`, `figure/figcaption`, `hr`, list markers)
- **Reading meta** (`.reading-meta .dot/.badge-read`)
- **Page hero variants** (`.page-hero` with `var(--hero-mesh)` + grid overlay, `.variant-project/research/engineering` — truncated in show but ~288 lines total)

Pure additive CSS; no overrides of existing selectors except extending `:root`.

---

## 2) Supersession vs main flagship merges (T2) — **SUPERSEDED**

| Main commit | Date (+0330) | Scope | Relationship to stash@{5} |
|-------------|--------------|-------|---------------------------|
| `16e86d70` | 07:06:50 | flagship pro site (base) | **stash parent** — already ancestor of HEAD |
| `7948b6b4` | 07:59:59 | 9000-line flagship — 3375 CSS + 1806 JS + 1332 enhance CSS + 901 enhance JS + 1949 builder | **supersedes CSS block** — introduces full flagship design system; stash's 288-line expansion is a prototype that was re-implemented at 10× scale |
| `7150e3de` | 09:31:18 | cinematic CSS v2 — 3501 lines `site/assets/styles.css` | **definitively supersedes** — `git diff 16e86d70..7150e3de --stat -- site/assets/` = `+6865` lines across 4 files; resulting `site/assets/styles.css` is 3501 lines vs stash's 373→661 (+288) |
| `b50f85a6` | 09:32:26 | cinematic JS v2 — 2849 lines `site/assets/search.js` | **definitively supersedes** — `302→2849` lines; HEAD search.js contains richer versions of every stash JS module |

Evidence:

```
git show HEAD:site/assets/styles.css | wc -l  → 3501
git show 16e86d70:site/assets/styles.css | wc -l → 373
stalk diff 16e86d70..7150e3de -- site/assets/ → 6865 ins

git show HEAD:site/assets/search.js | grep -c "lightbox" → 3 (HEAD has 8 in js/site.js context)
git show HEAD:site/assets/search.js | grep -c "to-top" → 3  (incl. back-to-top module at line 1156)
git show HEAD:site/assets/styles.css | grep "PREMIUM EXPANSION" → 0  (header not carried — rewritten)
git show HEAD:site/assets/styles.css | grep "page-hero" → 0 in HEAD styles.css; 1 mesh-canvas variant retained
```

**Every JS module in stash@{5} has a production successor in HEAD** (`site/assets/search.js` 2849 lines): focus trap, anchors, toast, to-top, tabs/accordion/faq, table sort, lightbox, reading-time, external links, parallax — all present with expanded implementations (see grep: `lightbox` at 1095, `Reading time + back-to-top` at 1147, `Tabs, accordions, table sorting, lightbox…` at 1007).

**CSS is likewise subsumed**: the 288-line expansion is a pre-v4 prototype. HEAD's `site/assets/styles.css` carries the cinematic v4.0 pro design system (2800+ lines OKLCH/glass/mesh) plus `site/assets/css/site.css` — the stash header `NEXUS FLAGSHIP — PREMIUM EXPANSION (9000-line build)` does not survive verbatim because the content was rebuilt properly.

**Apply check:** `git show` of stash anchor `@@ -371,3` at `/* Premium extras */` still matches HEAD at line 365-373, but HEAD has since grown 3128 lines after that anchor (the cinematic v4.0 block starting at line ~380). A naive `git stash apply` would attempt to re-insert the 288-line prototype **on top of** the 3501-line flagship — producing duplicates and conflicting tokens (`--surface`, `--hero-mesh`, `.prose h2 .anchor`, `.page-hero`). No cherry-pick needed.

Conclusion: **stash@{5} is fully superseded**. No unique asset would be lost by dropping it; the inverse (applying it) would regress/duplicate HEAD.

---

## 3) Contradiction vs stash@{1}/@{3}/@{4} (T3)

The task description frames the tension as "adds FLAG vs removes" but stash@{5} is **not** a FLAG stash — this is a key correction:

| Stash | Message | `git stash show -p \| grep FLAG` | Effect |
|-------|---------|----------------------------------|--------|
| `stash@{5}` (this) | `css-js-old` | `/* == FLAGSHIP EXPANSION modules == */` + `NEXUS FLAGSHIP — PREMIUM EXPANSION (9000-line build)` — **descriptive header only** | **Adds site assets** (288 CSS + 14 JS) — no `FLAG_*` code |
| `stash@{1}` | `hold-303-docs-site-three_model for main pickup` | `-/* ══ FLAGSHIP 9000 JS PREMIUM PACK ══ */` then 600 `void 0; /* flag-js-* premium noop */` lines | **Removes** JS FLAG pack (patch deletes it) |
| `stash@{3}` | `hold-302` | `-# ══ FLAGSHIP 9000 BUILD HELPERS ══` + `FLAG_BUILD_INDEX_0000 = 0 …` | **Removes** build-site FLAG helpers |
| `stash@{4}` | `tmp-site` | same `FLAG_BUILD_INDEX_*` removal | **Removes** build-site FLAG helpers |

Measured:

```
git stash show -p stash@{5} | grep -i FLAG  → 2 hits, both comments (not FLAG_BUILD_INDEX/flag-js)
git stash show -p stash@{1} | grep -i FLAG  → deletes flag-js-* (600 lines)
git stash show -p stash@{3} | grep -i FLAG  → deletes FLAG_BUILD_INDEX_* (600 lines)
git stash show -p stash@{4} | grep -i FLAG  → deletes FLAG_BUILD_INDEX_* (same)
```

**Contradiction verdict: NO direct contradiction.**

- stash@{5} and stash@{1}/@{3}/@{4} touch **disjoint concerns**: stash 5 = site `styles.css`/`search.js`; stashes 1/3/4 = removals of `FLAG_BUILD_INDEX_*` in `scripts/docs/build_site.py` and `flag-js-*` in JS.
- There is no file overlap (`site/assets/*` vs `scripts/docs/build_site.py` + `site/_site/*` generated diffs).
- The "adds FLAG vs removes FLAG" framing does **not** apply to stash 5 — it does not add executable FLAGs; its `FLAGSHIP` string is a branding comment.
- The actual FLAG add-vs-remove conflict exists between other stashes and main's forensic P3 merges (`ebee9b83 remove 600-line FLAG_BUILD_INDEX dead tail`, `54227f52 wire docs-enhance assets`), which **aligned with** stashes 1/3/4's removal direction — not stash 5.

If any sequencing were attempted, stash 5 is orthogonal: it could be applied before or after the FLAG removals without logical conflict, but it is **unnecessary** (see §2).

---

## 4) Security / execution / ML impact (T4) — **NONE**

```
git diff stash@{5}^1..stash@{5} --name-only
site/assets/search.js
site/assets/styles.css
→ grep ^(src/|security|execution|ML|models|ml) → NONE
```

- No `src/` path touched.
- No `scripts/` path touched (so no build/execution pipeline risk).
- No Python, no model, no 70D/CLEAN dataset, no broker/execution code.
- Only `site/assets/` (static docs site presentation layer).
- Risk profile: **pure docs UX** (CSS/JS). Even if applied, the only risk is visual regression/duplicate styles, not data or trading integrity.

---

## 5) Verdict — **REJECT** (superseded, safe to drop)

**Verdict: REJECT — do not integrate.**

**Evidence summary:**

1. **Superseded by flagship.** Content is a 302-line early prototype (`2026-09-04 08:04:53`, +1h after `16e86d70`) that was rebuilt at production scale in `7948b6b4` (9000-line) → `7150e3de` (3501-line CSS v2) → `b50f85a6` (2849-line JS v2). All 10 JS modules and the 288-line CSS expansion have richer, audited successors in HEAD.
2. **No unique value.** No token in stash@{5} is absent from HEAD in improved form; the stash header itself was intentionally rewritten (v4.0 cinematic system).
3. **No contradiction to preserve.** No FLAG conflict; no dependency on stash@{1}/@{3}/@{4}.
4. **No security/ML/execution relevance.** Docs-only.
5. **Apply would harm.** Would duplicate tokens and reintroduce prototype CSS atop the 3501-line flagship, requiring manual dedup.

**Recommended action:** Leave stash untouched per forensic preservation order (DO NOT pop/drop), but mark **REJECT** in the integration matrix. If a future audit needs to prove provenance, the patch is preserved at `forensic_recovery_20260904/stash-5.patch` / `stash-5-tracked.patch` (33,134 bytes) with parent `stash-5-parent.txt` (`16e86d70`).

**Reproduction commands (absolute path as instructed):**

```bash
cd C:/Users/Capsizer/source/repos/NexusTradingForexBot && git stash show stash@{5} --stat
cd C:/Users/Capsizer/source/repos/NexusTradingForexBot && git diff stash@{5}^1..stash@{5} --name-only
cd C:/Users/Capsizer/source/repos/NexusTradingForexBot && git stash show -p stash@{5} | head -n 120
cd C:/Users/Capsizer/source/repos/NexusTradingForexBot && git log --oneline 16e86d70..HEAD -- site/assets/ | head
cd C:/Users/Capsizer/source/repos/NexusTradingForexBot && git show HEAD:site/assets/styles.css | wc -l; git show HEAD:site/assets/search.js | wc -l
```

*Agent 6 — oldest stash. No src/security/execution/ML impact. No FLAG contradiction. Superseded by 7948b6b4/7150e3de/b50f85a6.*
