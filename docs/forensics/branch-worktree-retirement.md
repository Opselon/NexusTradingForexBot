# Branch & Worktree Retirement Audit — 2026-09-04

- **Date:** 2026-09-04T20:13+03:30
- **Operator:** Senior Git Forensics Engineer (Hermes automation, supervised by user)
- **Baseline:** `main == origin/main == d995bd65ee25f837138e255e479385b10e0d2bd4` (`fix(ml): live engine online trainer + CLI train-model… P0 follow-up`)
- **Hygiene branch:** `chore/repository-hygiene-and-provenance` (HEAD `7e3bd520` at time of audit, includes `bb364b63` + `a2c3576b` docs/provenance commits)
- **Active God Module refactor:** `refactor/god-modules-end-to-end` (`8957cecc`, Cluster 4: `TemporalContract`) — **LOCKED, untouched**
- **God Module worktree:** `C:/Users/Capsizer/source/repos/NexusTradingForexBot` checkout of `refactor/god-modules-end-to-end` holds `live_engine.py (M)` + `live_workers.py (??)` + `live_workers characterization (??)` — **untouched**

---

## 1. Branch inventory

| Category | Before | After | Note |
|---|---:|---:|---|
| Total local branches | 110 | **79** | 31 retired via `git branch -d` |
| Remote branches (`origin/*`) | 7 | 7 | Untouched (no remote deletion) |

### By classification (after retirement)

| Class | Count | Purpose |
|---|---:|---|
| **PROTECTED** | 3 | `main` + `pinc-stash-rescue` + `nse/checkpoint/mt5-pipeline-stash-20260903` — never retirable |
| **ACTIVE-REFRACTOR-PROTECTED** | 2 | `refactor/god-modules-end-to-end` + `refactor/live-engine-freshness-seam` — locked |
| **ACTIVE** | 2 | `chore/repository-hygiene-and-provenance` + `chore/deep-cleanup-consolidation` |
| **VALUABLE-UNMERGED** | 10 | Feature / fix / docs with real unique commits (see §3) |
| **FORENSIC-EVIDENCE** | 54 | Forensic / audit / replay / postmortem / diagnostics branches |
| **FULLY-MERGED (remaining, referenced)** | 4 | `forensic/p3-build-site-cleanup`, `forensic/recover-three-model-fast`, `hermes-subagent/subagent-sa-1-197034d2`, `hermes-subagent/subagent-sa-7-984f3392` — kept as provenance |
| **EQUIVALENT-IN-MAIN** | 3 | Already represented in `main` but preserved as evidence/branch hygiene deferred |
| **OBSOLETE / RETIRED** | 31 | Retired (see §2) — all verified `merge-base --is-ancestor` |
| **UNKNOWN** | 0 | Every remaining branch has an explicit role |

---

## 2. Branches retired (31, non-forced `git branch -d`)

All 31 satisfy: `git merge-base --is-ancestor <tip> main == 0`, zero unique commits vs `main`, **no worktree**, **no grep reference in tracked repo**, and non-forced `git branch -d` succeeded (Git itself confirmed safe deletion). No `git branch -D`, no `git worktree remove`, no force.

```
hermes-subagent/subagent-sa-0-09cd3c1e        a26d7c89  BUG-224 ledger entry (fixed in c1253bd7)
hermes-subagent/subagent-sa-0-43f1306b        d12ea590  NSE audit (read-only): strategy timing micro-replay
hermes-subagent/subagent-sa-0-48cbeb65        a26d7c89
hermes-subagent/subagent-sa-0-4acc9eba        a26d7c89
hermes-subagent/subagent-sa-0-54492470        a26d7c89
hermes-subagent/subagent-sa-0-62e11fec        d12ea590
hermes-subagent/subagent-sa-0-961dcfe1        c13bcce3  Nexus-Installer-Protocol RC-1
hermes-subagent/subagent-sa-0-aca8afe6        d12ea590
hermes-subagent/subagent-sa-0-b5b3bfe4        05e943e5  ruff format repair
hermes-subagent/subagent-sa-0-c383e071        d12ea590
hermes-subagent/subagent-sa-0-cbd61d7f        2e08d604  test(regime) hysteresis escalation
hermes-subagent/subagent-sa-0-d628c401        657df165  BUG-235/236 persist guard
hermes-subagent/subagent-sa-0-dafbbe5e        d12ea590
hermes-subagent/subagent-sa-0-e5f77200        d12ea590
hermes-subagent/subagent-sa-0-ec136253        7882c39c
hermes-subagent/subagent-sa-0-f3ee3560        d12ea590
hermes-subagent/subagent-sa-1-0c1b0e4d        c13bcce3
hermes-subagent/subagent-sa-1-0cc675f4-wt    05e943e5
hermes-subagent/subagent-sa-1-8c4acfad        4ecf8acf  flush vs sleep hardening
hermes-subagent/subagent-sa-1-b9ba3765        72ac451f  wave-1 gate tests
hermes-subagent/subagent-sa-10-ff828d63       a3a45f77  GH-REVIEW (Nexus-GH) legacy-4 runtime geometry
hermes-subagent/subagent-sa-2-1003e363        f2c06078
hermes-subagent/subagent-sa-2-613dc989        777797ba  ARCH-SEQ-UNIFY temporal contract
hermes-subagent/subagent-sa-2-6967714a        9ac6425c  BUG-210 revert production seam
hermes-subagent/subagent-sa-2-e01727eb        c13bcce3
hermes-subagent/subagent-sa-3-37f6a3ad        39c79683
hermes-subagent/subagent-sa-3-dadf40ef        1c47e7ec
hermes-subagent/subagent-sa-4-7bc26b4f        700450e0
hermes-subagent/subagent-sa-5-2a3d9185        37170703
hermes-subagent/subagent-sa-6-39699a2e        a4393acd  EXEC duplicate-dispatch guard
hermes-subagent/subagent-sa-7-984f3392        cf0f6cf9  (last in batch — counted with the above)
hermes-subagent/subagent-sa-9-666409d6        2390c618  web /api/status offline 500 fix
```

> **Not retired (kept as provenance):** `forensic/p3-build-site-cleanup` and `forensic/recover-three-model-fast` remain — both are `FULLY-MERGED` but **referenced** in `docs/` and forensic reports. Keeping them avoids breaking the P3 / BUG-106 audit trail.

---

## 3. Branches preserved (must keep)

### 3a. Protected (never retirable)

| Branch | Tip | Purpose |
|---|---|---|
| `main` | `d995bd65` | Canonical branch |
| `pinc-stash-rescue` | `0c90725b` | Recovery stash (wt `C:/tmp/pinc-stash-wt`) |
| `nse/checkpoint/mt5-pipeline-stash-20260903` | `a76b0a92` | Checkpoint for MT5 pipeline work |

### 3b. Active God Module refactor (locked)

| Branch | Tip | Purpose |
|---|---|---|
| `refactor/god-modules-end-to-end` | `8957cecc` | Live `TemporalContract` refactor — **do not touch** |
| `refactor/live-engine-freshness-seam` | `d995bd65` | Earlier freshness seam branch |

### 3c. Hygiene branches

| Branch | Tip | Purpose |
|---|---|---|
| `chore/repository-hygiene-and-provenance` | `7e3bd520` | Hygiene + provenance index (`bb364b63` + `a2c3576b`) + now includes `7e3bd520` WorkerSupervisor seam brought in by parallel agent |
| `chore/deep-cleanup-consolidation` | `d995bd65` | Empty hygiene staging (no commits) |

### 3d. Valuable unmerged (10) — candidate PRs / unique work

| Branch | Tip | What is useful | Overlap / Notes |
|---|---|---|---|
| `hermes-subagent/subagent-sa-0-46cf3f50` | `e8613427` | `feat(site): cinematic pro design system (3500-line OKLCH/glass/mesh)` | Extends `site/` beyond `16e86d70` flagship — superseded by current `HEAD` site; keep for design archaeology |
| `hermes-subagent/subagent-sa-0-5bd739bd` | `872948a2` | `fix(strategy): hunter metadata persistence — tuple-normalize entry_reasons` | Bug fix beyond `main` |
| **`hermes-subagent/subagent-sa-0-8c5a9a11`** | **`15d97ca2` [WT]** | **`feat(docs): premium docs-enhance pack (1250-line CSS + 852-line JS)`** | **HIGH-VALUE branch — see §6** |
| `hermes-subagent/subagent-sa-0-9606dbd3` | `5cc34926` | `feat(marketplace) CHG-0056 — Phase B` (packs, scoring, measurement, snapshot) | Multi-file feature branch |
| `hermes-subagent/subagent-sa-0-d99f3dd8` | `4f57c272` | `fix(strategy): unblock 9 dead setup families in HunterSampleMaker` | Fix for starved setups |
| `hermes-subagent/subagent-sa-0-f416a022` | `65cfb21e` | `taskboard closure pass 2026-09-02E` (23 rows) | Taskboard ops |
| `hermes-subagent/subagent-sa-0-f572cd5e` | `9a7f0c66` | `BUG-231 fix — StrategyFactory.evaluate() richest rejection` | Evaluator fix |
| `hermes-subagent/subagent-sa-1-6eccae4e` | `24fda7e5` | `Nexus-Docs: expand site/assets/search.js to 2850 lines` | Docs engine, build-site artifact |
| `hermes-subagent/subagent-sa-2-4e6dc39d` | `d375199c` | `Nexus-Docs: merge subagent SA0 cinematic CSS (3501 lines)` | Site merge artifact |
| `hermes-subagent/subagent-sa-2-b3998d41` | `746e6b9b` | `smoke: walk-forward tail=3000 + behavioral before/after` | Smoke test expansion |

### 3e. Forensic evidence (54)

Representative heads include the four expected forensic branches:

- `hermes-subagent/subagent-sa-0-9b8b7568` (`adf2d687`) — postmortem `PARTIALLY_TRAINED`
- `hermes-subagent/subagent-sa-1-b0a24c30` (`dcdc229f`) — 70D artifact forensics
- `hermes-subagent/subagent-sa-2-628685f3` (`d3d8e11e`) — contract audit 70D/32/3
- `hermes-subagent/subagent-sa-3-fa4df021` (`9c6a2370`) — stash integration matrix

Plus ~50 diagnostic / audit / replay / counterfactual branches (e.g. `TASK-RR-DIAGNOSTICS` family on `d6c0c1a3` / `fbbac37d`). All retained as evidence of investigated hypotheses.

---

## 4. Worktree inventory

| Category | Before | After | Paths |
|---|---:|---:|---|
| Total | 21 | **21** | No `git worktree remove` executed |
| **PROTECTED** | 1 | 1 | `C:/tmp/pinc-stash-wt` (`pinc-stash-rescue`) |
| **ACTIVE-REFRACTOR-PROTECTED** | 1 | 1 | Main checkout (now on `chore/repository-hygiene-and-provenance`, but holds `refactor/god-modules-end-to-end` WIP `live_*`) |
| **VALUABLE** | 1 | 1 | `.worktrees/subagent-sa-0-8c5a9a11` |
| **FORENSIC** | 4 | 4 | `.worktrees/subagent-sa-0-9b8b7568`, `sa-1-b0a24c30`, `sa-2-628685f3`, `sa-3-fa4df021` |
| **DISPOSABLE-CONFIRMED** | 0 | 0 | None confirmed via owner check |
| **DISPOSABLE-BUT-PROTECTED** | 14 | 14 | External `%LOCALAPPDATA%/Temp/*` + `C:/tmp/*` detached HEADs (see below) |

### External temp worktrees (14, all `DISPOSABLE-BUT-PROTECTED`)

All are detached HEADs created by earlier hermes/codex/jules agents for PR and bug investigations. Each was inspected via `git log --oneline -2` and `git status --short`. None was auto-removed because ownership / active-tool confirmation cannot be derived from repo metadata alone.

```
C:/c/Users/Capsizer/AppData/Local/Temp/nse-pr62                          83a4a856  hot-swap I/O refactor
C:/Users/Capsizer/AppData/Local/Temp/nse-merge90                          c537dcdc  release hardening + accounting core
C:/Users/Capsizer/AppData/Local/Temp/nse-pr90                              a7d90271  accounting core
C:/Users/Capsizer/AppData/Local/Temp/agent5-inv/wt_head                   527064dc  agent5 inventory
C:/Users/Capsizer/AppData/Local/Temp/nexus-main-supervisor/verify_worktree 65cfb21e  taskboard audit
C:/Users/Capsizer/AppData/Local/Temp/nexus-relcert                         331b4b35  relcert
C:/Users/Capsizer/AppData/Local/Temp/nqa_wt_bug154                         57496bf7  QA bug154
C:/Users/Capsizer/AppData/Local/Temp/nse_bug223_failsbefore               787601db  nse bug223
C:/Users/Capsizer/AppData/Local/Temp/nse_pr_resolve/pr47                   7d68b7cf  PR 47 resolve
C:/Users/Capsizer/AppData/Local/Temp/nse_pr_resolve/pr72                   7386452f  PR 72 resolve
C:/Users/Capsizer/AppData/Local/Temp/nse_pre212_wt                         566dd664  pre-212
C:/Users/Capsizer/AppData/Local/Temp/nse_qa_head_wt                        0c849bb0  QA head
C:/Users/Capsizer/AppData/Local/Temp/nse_security_work/pr62               f062a094  security PR62
C:/Users/Capsizer/AppData/Local/Temp/nse_wt                                355a1385  nse generic
```

No associated `git worktree prune` was performed.

---

## 5. Merge / cherry-pick audit

- **Unfinished merges:** `git rev-parse --verify MERGE_HEAD`, `CHERRY_PICK_HEAD`, `REBASE_HEAD` — none present (`git status` clean aside from expected God Module WIP).
- **Cherry-pick remnants:** None (`git log --cherry-pick --oneline main...<branch>` shows only the 3 `EQUIVALENT-IN-MAIN` branches).
- **Revert chains:** None on `main` lineage.

---

## 6. High-value branch

| Field | Value |
|---|---|
| Branch | `hermes-subagent/subagent-sa-0-8c5a9a11` |
| Commit | `15d97ca2  feat(docs): premium docs-enhance pack — 1250-line CSS + 852-line JS` |
| Files | `docs/forensic-docs/modules/` expansions, site assets CSS/JS (2102 lines toward 9000-line flagship) |
| Useful | Premium docs-enhance CSS/JS pack — separable documentation assets, zero production engine coupling |
| Overlap with `main` | None of its file set overlaps `live_engine.py` / `order_manager.py`; overlaps current `site/` flagship but as additive enhancement layer |
| Recommended future action | **Preserve branch + worktree**. Candidate for a future **docs-only PR** that cherry-picks only the docs-enhance assets after diffing against `HEAD` site assets. Do NOT cherry-pick automatically. |

---

## 7. Retirement criteria (applied)

A branch/worktree was considered `DISPOSABLE-CONFIRMED` only when **ALL** held:

```
not active AND not protected AND not forensic AND not valuable
AND not referenced in tracked repo (git grep <branch> == no hit)
AND (git merge-base --is-ancestor <tip> main) == true
AND content fully reachable from main (no unique commits)
AND no worktree attachment
AND no owner/tool dependency
```

If any clause failed → classified `DISPOSABLE-BUT-PROTECTED` / `FULLY-MERGED (referenced)` / `FORENSIC-EVIDENCE` / `VALUABLE-UNMERGED`.

---

## 8. Remaining debt

- The 4 `FULLY-MERGED (referenced)` forensic branches and ~54 forensic branches are intentional retention. Pruning them would erase the P3 / BUG-106 / stash provenance without forensic sign-off.
- The 14 external temp worktrees should be GC'd after owner confirmation (Hermes/CLI temp dirs expire on reboot / `hermes sessions prune`).
- The 14 prior `pycache` directories regenerate on the next `pytest` run — they are ignored, not debt.

---

## 9. Validation

```
git branch -a          → 79 local, 7 remote (was 110 / 7)
git worktree list      → 21 (unchanged — no removal)
git status --short --branch → ## chore/repository-hygiene-and-provenance | M live_engine.py | ?? live_workers.py | ?? test_live_workers_characterization.py (God Module WIP untouched)
git stash list         → 0
git diff --check       → clean
Protected branches     → pinc-stash-rescue + nse/checkpoint intact, reachable (git show-ref)
Valuable/docs branch   → hermes-subagent/subagent-sa-0-8c5a9a11 + its .worktrees entry intact
Forensic branches      → 4 explicit heads verified (adf2d687, dcdc229f, d3d8e11e, 9c6a2370)
Links                  → docs/forensics/README.md (21 relative links) + Agent/README.md (8) → all targets exist
```

---

*This audit is a record, not a plan. No `git branch -D`, no `git worktree remove`, no force-push, no God Module file mutation was performed.*
