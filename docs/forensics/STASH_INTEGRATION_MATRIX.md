# STASH_INTEGRATION_MATRIX — Forensic Baseline

> **Scope:** Read-only triage of all 6 stashes in `NexusTradingForexBot` against HEAD `b015db93` (task ref `a5e2ccc4`). No modifications made to repo. Patches dumped via `git stash show -p stash@{i} > /tmp/stash-i.patch`.

- **HEAD:** `b015db93` — `ui(NSE): make Admin Dashboard responsive on phones/tablets/monitors/ultrawide (CSS only)` — parent `a5e2ccc4`
- **Task reference point:** `a5e2ccc4` — `Nexus-Main: ruff format heal on scripts/docs/build_site.py`
- **Recent main delta:** `a5e2ccc4..b015db93` = 1 commit (`b015db93` touches `Web/index.html` +61, `Web/responsive.css` +238 (new), `src/nexus_scalp/web/server.py` +4). Disjoint from all stash file sets.
- **Worktree:** `hermes-subagent/subagent-sa-3-fa4df021` (isolated, read-only inspection)
- **Date:** 2026-09-04 15:07 +0330
- **Patch archive:** `/tmp/stash-{0..5}.patch` (verified below)

---

## 0. Patch Archive Verification

```
stash-0.patch: 0 lines,    0 bytes — empty
stash-1.patch: 7457 lines, 1.4M
stash-2.patch:   67 lines, 3.5K
stash-3.patch:13826 lines, 1.7M
stash-4.patch:13785 lines, 1.7M
stash-5.patch:  322 lines,  33K
```

Generated with:
```bash
for i in 0 1 2 3 4 5; do git stash show -p stash@{i} > /tmp/stash-$i.patch; done
```

---

## 1. Stash Refs & Bases (git stash list + parent provenance)

| # | stash ref | hash | date | message | base (`stash@{i}^1`) |
|---|-----------|------|------|---------|----------------------|
| 0 | `stash@{0}` | `b92cbd90` | 2026-09-04 08:38 +0330 | `On hermes-subagent/subagent-sa-1-6eccae4e: hold-304-site-dirty-for-main` | `16e86d70` Nexus-Docs: flagship pro site — glass hero + … |
| 1 | `stash@{1}` | `07b2ef5d` | 2026-09-04 08:36 +0330 | `On hermes-subagent/subagent-sa-2-4e6dc39d: hold-303-docs-site-three_model for main pickup` | `959d7d90` Nexus-Main: docs(status) v9.0.8 drift (one commit ahead of 16e86d70) |
| 2 | `stash@{2}` | `483fc0c3` | 2026-09-04 08:34 +0330 | `On hermes-subagent/subagent-sa-1-6eccae4e: hold-303-docs-site-three_model for main pickup` | `16e86d70` |
| 3 | `stash@{3}` | `e90013d5` | 2026-09-04 08:29 +0330 | `On main: hold-302` | `66555ea7` Nexus-Main: fix(critical) test_70d_model_31 lineage override |
| 4 | `stash@{4}` | `7ad11d9f` | 2026-09-04 08:24 +0330 | `On main: tmp-site` | `66555ea7` |
| 5 | `stash@{5}` | `d3cab5bc` | 2026-09-04 08:04 +0330 | `On hermes-subagent/subagent-sa-2-4e6dc39d: css-js-old` | `16e86d70` |

Bases vs HEAD distance:
- `16e86d70..a5e2ccc4` = 25 commits (all stashes on 16e86d70 are 25 commits behind task ref)
- `66555ea7..a5e2ccc4` = 15 commits (stash 3/4 base)
- `959d7d90..a5e2ccc4` = 4 commits (stash 1 base is 959d7d90 which itself is 1 commit ahead of 16e86d70, then 4 commits onward to a5e2ccc4)
- `a5e2ccc4..b015db93` = 1 commit (responsive drawer — no stash overlaps)

All stash bases are ancestors of HEAD. No stash is ahead of HEAD (no diverged unknown branch).

---

## 2. Integration Matrix

| Stash | Classification | Files (key) | Overlaps HEAD? | Overlaps Dirty `Web/*`? | Action |
|-------|---------------|-------------|----------------|-------------------------|--------|
| `stash@{0}` | **OBSOLETE** | *(none — empty patch)* | No | No | **Drop** — `git stash drop stash@{0}`. 0-byte patch, `git diff stash@{0}^1..stash@{0}` empty, `git stash show --stat` empty. Captured from `hermes-subagent/subagent-sa-1-6eccae4e` with no tracked changes (likely `stash -k` or untracked-only; `stash@{0}^3` missing). |
| `stash@{1}` | **OBSOLETE** | `site/_site/**` (338 files, ~2-line churn each) + `site/assets/search.js` (1504 deletions) — no `scripts/docs/build_site.py` diff; retains 600 `FLAG_BUILD_INDEX_*` at base | **Superseded** — `site/_site` regen already landed at `4261c3d2`; HEAD `build_site.py` has 0 FLAGs (P3 removed at `3d8dd752`). Applying would reintroduce deleted flagship helpers. | No | **Drop** — generated artifact. 1 non-site file in `--name-only` is effectively site-only. Do not apply. |
| `stash@{2}` | **CONFLICTING** *(duplicate parts + one dangerous line)* | `docs/project/status.md` (9.0.6→9.0.8), `scripts/docs/build_site.py` (+docs-enhance wiring 6 refs), `src/nexus_scalp/model_generation/three_model.py` (+1 dead import, +fast-path) | **Build_site wiring DUPLICATE** — HEAD already has 6 `docs-enhance` refs identical to stash. **Status OBSOLETE** — HEAD is 9.0.9 (pyproject `9.0.9`), stash 9.0.8 stale. **three_model CONFLICTING** — stash adds `from nexus_scalp.model_generation.schema_v2 import compute_70d_frame` at top-level (unused → `ruff F401`); HEAD cleanly imports `compute_70d_frame_fast` inline only. | No | **Do not bulk-apply.** If anything, salvage nothing — all useful hunks already on main. Cherry-pick would re-break lint. Drop or `DEFER` with manual dedup if audit demands. |
| `stash@{3}` | **DUPLICATE** | `docs/project/status.md` (9.0.6→9.0.8), `scripts/docs/build_site.py` (+enhance wiring, −615 FLAG lines), `site/_site/**` (~340 files) | **Fully landed** — exact diff already integrated as `3d8dd752 forensic(P3): integrate build_site cleanup (FLAG removal + docs-enhance wiring)` + `4261c3d2` regen + `a5e2ccc4` ruff heal. `grep -c FLAG_BUILD` HEAD=0 vs stash3 target 0 — identical. `grep -c docs-enhance` HEAD=6 vs stash3=6. | No | **Drop** — canonical P3 patch, now redundant. |
| `stash@{4}` | **DUPLICATE** | Same as stash 3 (build_site enhance+FLAG removal + site/_site regen; 615→0 FLAGs) | **Redundant with stash 3 / HEAD** — `git diff stash@{3} stash@{4} --stat` = 4 files only (`status.md`, `site/_site/project/status`, `search-index.json`, `three_model.py` trivial). Build_site payload byte-identical to stash 3. | No | **Drop** — `tmp-site` is a second snapshot of the same P3 payload. |
| `stash@{5}` | **OBSOLETE** | `site/assets/search.js` (+13 lines FLAGSHIP EXPANSION modules: focus-trap, anchors, toast, to-top, tabs/accordion/faq, table-sort, lightbox, reading-time, external-links, parallax), `site/assets/styles.css` (+288 lines FLAGSHIP PREMIUM expansion) | **Superseded/Skewed** — Base `16e86d70:styles.css` 373 lines → stash 661 lines → HEAD 3501 lines. HEAD styles is ~5× larger than stash5's target; `grep -c FLAGSHIP.*PREMIUM` HEAD=0 vs stash5=1 (comment header diverged). `search.js` HEAD 2849 lines vs stash5's +13 inline expansion at different offset (HEAD FLAGSHIP marker 0 vs stash5 1). Stash5 is an early interim flagship snapshot, not additive to current tree. | **No** — stash5 touches `site/assets/*`; dirty/responsive work touches `Web/responsive.css` (new 238-line file) + `Web/index.html` + `server.py`. Disjoint paths. | **Drop** (or `DEFER` to archive) — do not apply to HEAD; would regress styles. If design tokens needed, diff manually against HEAD 3501-line file, not blind-apply. |

Legend: **INTEGRATE** = clean apply; **DUPLICATE** = already on main; **OBSOLETE** = superseded/generated; **CONFLICTING** = overlaps requiring manual merge; **DANGEROUS** = reintroduces deleted/dead code; **DEFER** = needs human judgment.

**Summary verdict: 0× INTEGRATE, 2× DUPLICATE (3,4), 3× OBSOLETE (0,1,5), 1× CONFLICTING (2). 0 overlaps with `Web/responsive.css` / `server.py`. Safe to drop all 6 after archiving patches.**

---

## 3. Per-Stash Evidence

### stash@{0} — hold-304-site-dirty-for-main — OBSOLETE (empty)

- `git stash show --stat stash@{0}` → *(empty)*
- `git stash show -p stash@{0} | wc -l` → 0
- Parents: `16e86d70` (`stash@{0}^1`) + `1e2dcb1b` (index). `git diff stash@{0}^1..stash@{0}` empty. `stash@{0}^3` missing (no untracked stash commit).
- Contains no tracked working-tree changes. Likely created with `--keep-index` on a clean worktree or via subagent scaffolding. No loss if dropped.

### stash@{1} — hold-303-docs-site-three_model for main pickup (959d7d90 base) — OBSOLETE

- `git diff stash@{1}^1..stash@{1} --stat` → 340 files: 339 `site/_site/**` (2–4 line churn each) + `site/assets/search.js` (1504 deletions). No `scripts/docs/build_site.py` change in this stash's tracked diff.
- `git show stash@{1}:scripts/docs/build_site.py | grep -c FLAG_BUILD` → 600 (old code, pre-P3). HEAD is 0.
- Base `959d7d90` is the `9.0.8` status drift commit one step ahead of `16e86d70`; the 4 commits `959d7d90..a5e2ccc4` already supersede any status/site work here.
- `site/_site/**` is generated output excluded by P3 regime — never integrate generated HTML.

### stash@{2} — hold-303-docs-site-three_model for main pickup (16e86d70 base) — CONFLICTING

- `git diff stash@{2}^1..stash@{2}` (67 lines):
  ```diff
  docs/project/status.md: 9.0.6 → 9.0.8
  scripts/docs/build_site.py: +<link docs-enhance.css> +<script docs-enhance.js> +src_enhance copy block (already in HEAD)
  src/nexus_scalp/model_generation/three_model.py: +from schema_v2 import compute_70d_frame (dead) + from schema_v2_incremental import compute_70d_frame_fast (already in HEAD at two call-sites)
  ```
- `git diff HEAD stash@{2} -- three_model.py` shows the only remaining delta vs HEAD is the dead `schema_v2` import (line 39). HEAD's `three_model.py` already uses `compute_70d_frame_fast` at lines 116–120 and 134–136 via local imports, with no top-level `schema_v2` import.
- `HEAD docs/project/status.md` → `9.0.9`; `stash@{2}:docs/project/status.md` → `9.0.8` → downgrade if applied.
- Verdict: build_site hunk duplicate, status hunk stale, three_model hunk introduces `F401` lint failure. No INTEGRATE value.

### stash@{3} — hold-302 — DUPLICATE

- `git diff stash@{3}^1..stash@{3} -- scripts/docs/build_site.py` → +docs-enhance wiring (identical to HEAD) plus deletion of the `FLAG_BUILD_INDEX_0000..600` block (615 lines) — the exact P3 cleanup.
- `git show stash@{3}:scripts/docs/build_site.py | grep -c FLAG_BUILD` → 0; HEAD also 0. `grep -c docs-enhance` both 6.
- This is the P3 forensic payload before it landed as `3d8dd752` + `4261c3d2` + `a5e2ccc4`. Now redundant.
- `site/_site` portion is the regeneration that landed at `4261c3d2`.

### stash@{4} — tmp-site — DUPLICATE

- Same base (`66555ea7`) and same `build_site.py` payload as stash 3 (`-615 FLAGs + enhance wiring`).
- `git diff stash@{3} stash@{4} --stat` → 4 files (`status.md`, `site/_site/project/status/index.html`, `search-index.json`, `three_model.py`). Build_site byte-identical.
- Redundant snapshot; drop.

### stash@{5} — css-js-old — OBSOLETE

- `git stash show -p stash@{5}` → 322 lines:
  - `site/assets/search.js` +13 lines: 9 IIFEs (focus trap, heading anchors, toast+copy, back-to-top, tabs/accordion/faq, table sort, lightbox, reading time, external links, parallax) inserted at line 8.
  - `site/assets/styles.css` +288 lines: `NEXUS FLAGSHIP — PREMIUM EXPANSION (9000-line build)` tokens, prose, hero, grid, etc.
- Size lineage: `16e86d70:styles.css` 373 lines → `stash@{5}` 661 lines → `HEAD` 3501 lines. HEAD is not derived from stash5; it's a later, larger flagship build. `HEAD grep FLAGSHIP.*PREMIUM` = 0, so stash5's exact block not present in HEAD (diverged).
- `site/assets/*` vs `Web/responsive.css`: disjoint namespaces. Stash5 predates `Web/responsive.css` (new file at `b015db93`). No file-path overlap.
- Applying stash5 to HEAD would create merge conflict / duplicate style rules and reintroduce inline JS that HEAD's `site/assets/search.js` (2849 lines) already handles differently.

---

## 4. Overlap with Recent Main (`a5e2ccc4..HEAD`) & Dirty Worktree

- **Recent main:** `a5e2ccc4..b015db93` = 1 commit. File set: `Web/index.html`, `Web/responsive.css` (new, 238 lines), `src/nexus_scalp/web/server.py` (+4 lines drawer wiring). No stash touches `Web/*` or `server.py` (all stashes touch `site/_site/*`, `site/assets/*`, `scripts/docs/build_site.py`, `docs/project/status.md`, `three_model.py`). **Zero path overlap. Zero content overlap.**
- **Dirty worktree (task statement):** `Web/index.html`, `Web/responsive.css`, `server.py` responsive drawer. In the current worktree (`hermes-subagent/subagent-sa-3-fa4df021`) `git status --short` is clean (only untracked `forensic_recovery_20260904/*` from prior agents). In main worktree (`--git-dir` check) likewise no dirty `Web/*` — because the responsive work already landed as committed `b015db93` (vs the task's point-in-time dirty snapshot at `a5e2ccc4`). Treat `b015db93` as the integration target for responsive work.
- **Responsive vs stash5:** stash5's `site/assets/styles.css` is the docs site stylesheet; `Web/responsive.css` is the trading dashboard stylesheet. Different products, different directories. `server.py` drawer is server-side include wiring, not present in any stash.
- **Conclusion:** No stash requires three-way merge with responsive work. No `Web/responsive.css` + `server.py` revert risk.

---

## 5. Recommended Actions (safe-drop sequence)

```bash
# Archive already done: /tmp/stash-{0..5}.patch (keep or copy to forensic_recovery_20260904/)
cp /tmp/stash-*.patch forensic_recovery_20260904/  # if retention required

# Drop order: leaves no useful delta (all payloads already on HEAD or superseded)
git stash drop stash@{5}  # css-js-old — obsolete, keep patch if design archaeology needed
git stash drop stash@{4}  # tmp-site — duplicate of P3
git stash drop stash@{3}  # hold-302 — duplicate of P3
git stash drop stash@{2}  # hold-303 — conflicting/duplicate; dead import would break ruff
git stash drop stash@{1}  # hold-303 site/_site churn — generated artifact
git stash drop stash@{0}  # hold-304 — empty
# Drops from highest index first to avoid re-indexing confusion.
```

If conservatism is required, retain `/tmp/stash-5.patch` only (flagship CSS/JS archaeology) and drop the rest unconditionally.

---

## 6. How to Reproduce

```bash
git log --oneline -5                    # confirm HEAD b015db93 / a5e2ccc4
git stash list                          # 6 entries
for i in 0 1 2 3 4 5; do git stash show --stat stash@{i}; done
for i in 0 1 2 3 4 5; do git stash show -p stash@{i} > /tmp/stash-$i.patch; done
for i in 0 1 2 3 4 5; do echo "stash@{$i} base=$(git rev-parse stash@{$i}^1)"; done
git log --oneline a5e2ccc4..b015db93     # 1 commit, Web/* only
git show b015db93 --stat                # Web/responsive.css + server.py
grep -c FLAG_BUILD scripts/docs/build_site.py   # 0 on HEAD
grep -c docs-enhance scripts/docs/build_site.py # 6 on HEAD
wc -l site/assets/styles.css            # 3501 on HEAD vs 661 in stash5
```

---

*Generated read-only. No repo mutations performed. Author: forensic subagent sa-3-fa4df021.*
