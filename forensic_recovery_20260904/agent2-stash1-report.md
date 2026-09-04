# Forensic Deep Audit — stash@{1} (07b2ef5d)

**Agent:** agent2 — stash@{1}  
**Stash ref:** `07b2ef5d215407fb4e1b3559841ad295c373d817`  
**Message:** `On hermes-subagent/subagent-sa-2-4e6dc39d: hold-303-docs-site-three_model for main pickup`  
**Parent (stash@{1}^1):** `959d7d90` — `Nexus-Main: docs(status) v9.0.8 version drift — sync status.md snapshot to pyproject single-source (edd6694a bump missed this file)` (2026-09-04 08:23:37 +0330)  
**Stash created:** 2026-09-04 08:36:30 +0330  
**Main HEAD at audit:** `a5e2ccc4` (2026-09-04 14:54:10 +0330)  
**Parent behind main:** 15 commits (`git rev-list --count 959d7d90..HEAD = 15`) — the task prompt's "117 commits" figure corresponds to the older base `959d7d90` vs the snapshot diet at audit time after P3 rebase; actual measured gap is 15.

---

## 1) Stash Scope — Canonical Commands

### `git stash show stash@{1} --stat`

```
 339 files changed, 344 insertions(+), 3352 deletions(-)
```

All 339 files are under `site/`. Zero `src/`, zero `docs/`, zero root/config changes.

### `git diff stash@{1}^1..stash@{1} --stat` (identical to above)

```
 339 files changed, 344 insertions(+), 3352 deletions(-)
```

### `git diff stash@{1}^1..stash@{1} --name-only` (summary)

- `site/_site/**` — 338 files (all HTML artefacts: `404.html`, `site-meta.json`, `search-index.json`, plus every language tree `ar/`, `de/`, `es/`, `fa/`, `en` under `_site/`).
- `site/assets/search.js` — 1 file (deletion of trailing filler).
- No other paths.

### Breakdown by directory

| Path prefix | Files in stash |
|---|---|
| `src/` | **0** |
| `docs/` | **0** |
| `site/_site/` | **338** |
| `site/assets/` | **1** (`search.js`) |
| `site/` total | **339** |
| other | **0** |

All `site/_site/` entries show the same single-line delta: footer `rev d267fbd → rev 959d7d9` (and `site/_site/index.html` hero badge likewise). No content/structural change.

---

## 2) Parent vs Current Main — Revert Risk Assessment

### Commits between parent and main (15)

```
a5e2ccc4 ruff format heal on scripts/docs/build_site.py
694ee2b2 docs(forensics): reflect P3 completions (pushed 4261c3d2)
4261c3d2 docs(site): regenerate site/_site (v9.0.9 drift heal + docs-enhance wiring)
3d8dd752 forensic(P3): integrate build_site cleanup (FLAG removal + docs-enhance wiring)
54227f52 forensic(P3): wire docs-enhance assets in build_site.py shell + copy
ebee9b83 forensic(P3): remove 600-line FLAG_BUILD_INDEX dead tail from build_site.py
d3c59d46 docs(forensics): forensic recovery deliverables — stash triage + integration matrix
3179df9c forensic: integrate stash 70d-fast fix into main (BUG-106 complete)
6bb76497 forensic: use compute_70d_frame_fast for 70d variants in three_model (BUG-106 extension)
706269f1 fix release.yml CRLF corruption (backspace 0x08 → backslash+b)
62cfc512 fix(release): BUG-239 payload lacks build-info.json
b50f85a6 Nexus-Docs: cinematic JS v2 (2849 lines: drawer/theme/CmdK/TOC/parallax/counters/chart/terminal)
7150e3de Nexus-Docs: cinematic CSS v2 (OKLCH/mesh/glass/3D-tilt/timeline/docs-layout)
3cd6b6a7 nexus-coder: fix trainer parquet integer labels (0/1/2) — walk_forward_trainer accepts CLEAN dataset
9bb9f692 docs(status) v9.0.8 version drift — sync snapshot to pyproject single-source
```

### Critical fixes since parent

| Fix | Commit | Parent contains? | Blind-merge revert risk |
|---|---|---|---|
| **3cd6b6a7 — trainer parquet integer labels** (`walk_forward_trainer.py`: accept `int 0/1/2` alongside string labels) | `3cd6b6a7` | **NO** — `git merge-base --is-ancestor 3cd6b6a7 959d7d90` = false | **YES** if stash branch were merged as a branch (would fast-forward main back before 3cd6b6a7). But `git stash apply` touches **only `site/`**, so **no file-level revert** of `src/nexus_scalp/training/walk_forward_trainer.py` occurs. Verified: `git diff stash@{1}^1..stash@{1} -- src/` is empty. |
| **8ba90681 — era fix (CHECK-MDL-03)** (dimension contract validates against artifact's own `feature_schema_id`, not lagging `ACTIVE_SCHEMA_ID`) | `8ba90681` (2026-09-04 06:30:33) | **YES** — ancestor of `959d7d90` | None. Parent already contains it. Stash carries it implicitly. |
| P3 docs-enhance / FLAG removals, BUG-239/106, JS/CSS v2 | 4261c3d2 … b50f85a6 | NO | No revert risk from this stash, because it never touches `scripts/docs/build_site.py`, `src/`, or `site/src/` — only `site/_site/` and `site/assets/search.js`. The danger would be **overwriting the regenerated `_site/` on main with a stale build**, not a source revert. |

### Bottom line

- A **branch-style merge** (`git merge stash@{1}^1`) would revert `3cd6b6a7` (int-label fix). **Do not merge the parent branch.**
- A **`git stash apply`/`git stash show -p | git apply`** touches only `site/` and therefore **cannot revert `3cd6b6a7` or `8ba90681` at the file level** — zero `src/` deltas. The revert risk is confined to **stale site artefacts**, not source logic.

---

## 3) `site/_site` vs `src` Changes

### `site/_site/` — 338 files

- Nature: **regenerated HTML** — every file differs by exactly one token: the footer `rev` hash `d267fbd → 959d7d9`. No structural, content, or wiring change.
- One additional delta in `site/_site/index.html` hero badge: `rev d267fbd → rev 959d7d9`.
- `site/_site/assets/search.js` had a truncated copy (302 lines) in the stash vs 2849 lines on current main — the stash's `_site` copy reflects the stale `site/assets/search.js` state at that time (see below).

### `src/` — 0 files

No source changes in this stash. No `three_model` logic, no trainer, no contracts.

### `site/assets/search.js` — the only `site/` source change

- **Parent `959d7d90:site/assets/search.js`:** 1806 lines (already past the 1500-line FLAGSHIP filler that existed earlier).
- **Stash `site/assets/search.js`:** 302 lines — the stash's workdir had the filler truncated (`-1504` lines: `/* FLAGSHIP 9000 JS PREMIUM PACK */ void 0; /* flag-js-0000 … */`).
- **Current main `site/assets/search.js`:** 2849 lines (cinematic JS v2 from `b50f85a6`, with no FLAGSHIP filler — the stashed truncation is obsolete; main supersedes it).
- **Verdict on `search.js`:** Stash's `site/assets/search.js` is **stale** — main's 2849-line cinematic engine replaces both the 1806-line snapshot and the 302-line truncated stash. Applying the stash would **downgrade** `site/assets/search.js` from 2849 → 302 lines and break the docs site.

### Untracked/index stash (`stash@{1}^2`)

Empty — no staged or untracked changes beyond the workdir delta. Not a `--include-untracked` stash with hidden files.

---

## 4) Walk-Forward Trainer Regression Check

- **Stash delta on `src/nexus_scalp/training/walk_forward_trainer.py`:** none (`git diff stash@{1}^1..stash@{1} -- src/` is empty).
- **Main's fix (`3cd6b6a7`) is not in stash's parent** — but the stash does not overwrite it, so **no regression on `git stash apply`**.
- **However**, applying the stash via `git stash branch` or merging `959d7d90` would reconstruct a tree that lacks `3cd6b6a7`. File-level comparison `git diff HEAD stash@{1}^1 -- src/nexus_scalp/training/walk_forward_trainer.py` shows the missing `allowed`/`mapped` int-label handling:

  ```diff
  -        allowed = (set(self.label_map.keys()) | set(self.label_map.values()) | set(self.inverse_label_map.keys()))
  -        unknown_labels = sorted(set(raw_labels) - allowed)
  +        unknown_labels = sorted(set(raw_labels) - set(self.label_map.keys()))
  ...
  -        mapped: list[int] = []
  -        for lab in raw_labels:
  -            if isinstance(lab, int): mapped.append(int(lab))
  -            else: mapped.append(int(self.label_map[lab]))
  +        y = np.array([self.label_map[label] for label in raw_labels], ...)
  ```

  Without `3cd6b6a7`, training on CLEAN parquet with integer labels `0/1/2` raises `ValueError: Unknown labels detected in dataset: [0, 1, 2]` and fails.

- **Classification:** No **file-level** walk_forward_trainer regression from this stash's `site/`-only payload; but a **branch merge** of its parent would reintroduce BUG (int-label rejection). Flagged as merge-method-dependent.

---

## 5) Classification and Verdict

### Classification

**Ephemeral build artefact — superseded.**

- `hold-303-docs-site-three_model` title suggests docs/site + `three_model` work on branch `hermes-subagent/subagent-sa-2-4e6dc39d`, but the stashed payload contains **no `src/three_model` changes, no `docs/` changes, no `src/` changes at all** — only a stale `site/_site/` regeneration (`rev` bump) and a truncated `site/assets/search.js`. The `three_model` work (if any) was either committed before stashing, never reached workdir, or lives on that branch outside the stash.
- Both payloads are superseded: `site/_site/` was regenerated at `4261c3d2`/`694ee2b2` on main, and `site/assets/search.js` was replaced by the 2849-line cinematic JS v2 at `b50f85a6`. The stash's diff is therefore **pure drift**.

### Verdict: **REJECT**

| Criterion | Assessment |
|---|---|
| Contains unique `src/` logic not on main | **No** |
| Contains unique `docs/` content not on main | **No** |
| `site/assets/` delta is an improvement over main | **No — downgrade** (2849 → 302 lines) |
| `site/_site/` delta is fresher than main's `_site` | **No — stale** (rev 959d7d9 vs main's regenerated 4261c3d2/694ee2b2) |
| Safe to `git stash apply` | Technically safe for `src/` (no revert), but **actively harmful for `site/`** — would corrupt the docs site |
| Recommended action | **Drop after sign-off** (`git stash drop stash@{1}` only after coordinator confirms). Do not apply. If `three_model` work is suspected on `hermes-subagent/subagent-sa-2-4e6dc39d`, inspect that **branch** directly — not this stash. |

### What would be lost by rejecting

Nothing. All content is either superseded (`_site` regeneration, cinematic JS) or absent (`three_model` not in stash). The `rev 959d7d9` footer is older than main's `d267fbd →` latest rev.

### Safe recovery path (if docs-site archaeology is needed)

```bash
# Inspect only — never apply blindly
cd C:/Users/Capsizer/source/repos/NexusTradingForexBot
git diff stash@{1}^1..stash@{1} --stat          # 339 site-only files
git show stash@{1}:site/assets/search.js | wc -l  # 302 (stale)
# To check the branch for real three_model work:
git log --oneline hermes-subagent/subagent-sa-2-4e6dc39d -- src/ -- docs/ | head -20
```

---

## Raw Evidence (copy-paste)

```bash
cd C:/Users/Capsizer/source/repos/NexusTradingForexBot
git stash show stash@{1} --stat                    # 339 files, 344+/3352-
git diff stash@{1}^1..stash@{1} --stat             # identical
git diff stash@{1}^1..stash@{1} --name-only | wc -l # 339
git diff stash@{1}^1..stash@{1} --stat -- src/     # (empty)
git diff stash@{1}^1..stash@{1} --stat -- docs/    # (empty)
git rev-list --count 959d7d90..HEAD                # 15
git merge-base --is-ancestor 8ba90681 959d7d90 && echo YES || echo NO  # YES
git merge-base --is-ancestor 3cd6b6a7 959d7d90 && echo YES || echo NO  # NO
git show 959d7d90:site/assets/search.js | wc -l    # 1806
git show stash@{1}:site/assets/search.js | wc -l  # 302
wc -l site/assets/search.js                        # 2849 (main HEAD cinematic JS v2)
```

---

*Report by agent2 — stash@{1}. Read-only audit. Stash not popped/dropped. Site-only stale artefact — REJECT.*
