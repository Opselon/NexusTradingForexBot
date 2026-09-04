# FORENSIC RECOVERY REPORT — 2026-09-04T08:30+0330

> **Operator:** Nexus Main (recovery architect)  
> **Scope:** Every stash, worktree, branch, and uncommitted diff reachable from `main` @ `706269f1`  
> **Rule:** No stash popped/dropped/deleted. Snapshots exported before any mutation. `main` verified buildable after every batch.

---

## 1. True Repository State (collected 08:40)

| Signal | Value |
|---|---|
| Current branch | `main` |
| HEAD | `706269f1` — fix release.yml CRLF corruption (backspace fix) |
| origin/main | `706269f1` (in sync) |
| Working tree | clean (`git status --short` empty) |
| Staged | empty |
| Untracked (main wt) | none |
| Total local branches | ~95 (mostly `hermes-subagent/*`) |
| Worktrees | 16 live — 1 main + 1 `.worktrees/subagent-sa-0-8c5a9a11` + 14 detached temp/verify/cache wts + 1 `pinc-stash-rescue` |
| Prunable wts | 3 under `scratch/rungate/` (781097ee, ddcbc9b7, eebefab4) — no filesystem backing |
| Stashes | **6** — all created 2026-09-04 08:04–08:38 +0330 |
| Recent tags | `v9.0.9` @ `62cfc512`, `v9.0.8` @ `3cd6b6a7` |

### Stash inventory ( `git stash list --date=local` )

| # | Timestamp | Message | Base commit | Kind |
|---|---|---|---|---|
| `stash@{0}` | 08:38:25 | `On hermes-subagent/subagent-sa-1-6eccae4e: hold-304-site-dirty-for-main` | `16e86d70` (flagship pro site) | 2-parent merge stash, **no untracked** |
| `stash@{1}` | 08:36:30 | `On hermes-subagent/subagent-sa-2-4e6dc39d: hold-303-docs-site-three_model for main pickup` | `959d7d90` (status v9.0.8 drift) | site _site + asset revert |
| `stash@{2}` | 08:34:02 | `On hermes-subagent/subagent-sa-1-6eccae4e: hold-303-docs-site-three_model for main pickup` | `16e86d70` | 3 tracked files + 30 untracked probes |
| `stash@{3}` | 08:29:31 | `On main: hold-302` | `66555ea7` | docs + build_site 615-line cut + site regen |
| `stash@{4}` | 08:24:16 | `On main: tmp-site` | `66555ea7` | build_site 615-line cut + site regen |
| `stash@{5}` | 08:04:53 | `On hermes-subagent/subagent-sa-2-4e6dc39d: css-js-old` | `16e86d70` | `site/assets/styles.css` + `site/assets/search.js` only |

### Branch audit

* `hermes-subagent/*`: ~90. Most report `1177–1179 ahead` because they were forked from a stale commit (`16e86d70` / `d6c0c1a3` / `fbbac37d`) long before the current `main`. Effective delta vs `main` is tiny — verified via `git diff main..branch --stat | grep -v site/_site`. Only two carry non-site weight:
  * `hermes-subagent/subagent-sa-0-8c5a9a11` — docs premium pack (already merged to main via `b50f85a6` / `7150e3de` path; branch is now behind/ahead noise)
  * `hermes-subagent/subagent-sa-2-4e6dc39d` — 2 commits ahead of its base, docs merge + flagship status; no ML/execution change beyond site
* `pinc-stash-rescue` (also worktree `C:/tmp/pinc-stash-wt`): 1 merge commit `0c90725b` over `797481f7`. Diff vs `main` deletes `installer/install.ps1` bulk, docs, scripts/dev, and **reverts** 4 capital-safety fixes — see §4. Classified **REJECT**.
* `nse/checkpoint/mt5-pipeline-stash-20260903`: `a76b0a92` — foreign-wip stash checkpoint over `9ac6425c`; bulk deletions, not for main.

### Dirty worktrees (non-main)

| Worktree | Dirty file | Nature |
|---|---|---|
| `C:/Users/.../nse_bug223_failsbefore` | CRLF warning only on `test_audit_db_default_isolation_bug223.py`; no content diff | noise |
| `C:/Users/.../nse_qa_head_wt` | `tests/unit/test_release_system.py` +119 lines — BUG-160 fail-before proofs (`verify_checksums_resolve_from_installed_layout`, tamper, staged root) | test-only, deferred |
| `C:/Users/.../nse-relcert/scripts/cert/` | untracked `scripts/cert/` | pending BUG-160 tooling — not for main until review |

### Agent processes

No live Hermes subagent processes detected at audit time. All `hermes-subagent/*` branches are quiescent commits; no PIDs holding the worktrees.

---

## 2. Stash Forensics (per-stash, patch-level)

All patches exported to `forensic_recovery_20260904/stash-{0..5}.patch` (tracked) and `stash-{i}-tracked.patch`.

| Stash | Parents | Tracked diff stat | Overlap | Generated artifacts? | Conflicts with `main`? | Completeness | Provenance |
|---|---|---|---|---|---|---|---|
| `stash@{0}` | `16e86d70` + index `1e2dcb1b` | **0 lines** — `git diff stash@{0}^1..stash@{0}` empty | — | no | n/a | empty working stash (index had no content) | hermes SA-1 hold-304, appears to be a clean-stash of an already-clean wt |
| `stash@{1}` | `959d7d90` | 339 files, `+344 −3,352` — almost entirely `site/_site/*.html` (rev footer `d267fbd → 959d7d9`) + `site/assets/search.js` **deletion** of 1,504-line `FLAGSHIP 9000 JS PREMIUM PACK` (1500× `void 0; // flag-js-####`) | overlaps stash@{3}, stash@{4} site footer updates | **yes** — `site/_site/` is generated | parent is 117 Commits behind `main` (`959d7d90` lacks `3cd6b6a7` int-label fix, `8ba90681` era fix, `62cfc51` BUG-239, `706269f1` release.yml fix) | partial, stale | SA-2 docs-site hold — stale base makes it a regression if applied |
| `stash@{2}` | `16e86d70` | **3 files, `+16 −4`** — `docs/project/status.md` (6→8), `scripts/docs/build_site.py` (+docs-enhance wiring), `src/nexus_scalp/model_generation/three_model.py` (+`compute_70d_frame_fast` for 70d) ; **plus 30 untracked**: `.hermes/plans/...`, `_splice.py`, `scratch/ns_*` probes, `scratch/ci760/runtime_gate.json`, etc. | three_model overlaps stash@{3} identically; docs-enhance/build_site overlaps stash@{3}/stash@{4} | site/_site not in tracked diff; untracked are scratch/probe — not to commit | parent `16e86d70` is 8 commits behind `main`; tracked patch applies cleanly except status bump now stale (`main` already at 9.0.9) | **valid but mixed** — one clean ML perf fix, one contested docs wiring, one obsolete status bump, plus scratch noise | SA-1 hold-303 |
| `stash@{3}` | `66555ea7` | 334 files, `+~6349 −11715` (filtered non-site: `docs/project/status.md`, `scripts/docs/build_site.py` (615-line `FLAG_BUILD_INDEX_0000…0140` deletion + docs-enhance wiring), `site/assets/search.js` FLAG-JS deletion) + site `_site` regen + `src/nexus_scalp/model_generation/three_model.py` same fast fix | three_model identical to stash@{2}; build_site FLAG removal overlaps stash@{4}; site footer overlaps stash@{1}/stash@{2} | `site/_site` generated; `FLAG_BUILD_INDEX` tail after `raise SystemExit` is dead code | parent `66555ea7` is behind `main` (missing `62cfc51`, `706269f1`); three_model patch still applies; FLAG removal applies cleanly but needs separate review | valid subset | main hold-302 |
| `stash@{4}` | `66555ea7` | `site/assets/search.js` FLAG removal + `scripts/docs/build_site.py` 615-line FLAG deletion + site `_site` regen — **no `src/` change** | build_site/site overlaps stash@{3}; site/assets overlaps stash@{1} | site `_site` + dead FLAG code | same parent as stash@{3} | docs-only | main tmp-site |
| `stash@{5}` | `16e86d70` | 2 files `+302 −1` — `site/assets/styles.css` (+288 FLAGSHIP expansion) + `site/assets/search.js` (+14 module inlines) | site assets overlap stashes 1/3/4 with opposite polarity (adds vs removes) | asset files are source for site build, but content is FLAGSHIP cosmetic | no `src/` | docs-only, superseded by later flagship merges on `main` | SA-2 css-js-old (oldest) |

### Key overlap map

```
stash@{2} three_model == stash@{3} three_model   (byte-identical patch)
stash@{1}/@{3}/@{4} build_site FLAG removal      (same 615-line cut, parent-dependent)
stash@{1}/@{3}/@{4} site/_site footer            (same rev bump, different base rev)
stash@{5} styles/search                          (+FLAG) vs stash@{1}/@{3} (-FLAG)  — CONTRADICTION
```

No stash contains a production ML artifact, key, or secret.

---

## 3. `pinc-stash-rescue` — High-Risk Revert Analysis

`git diff main..pinc-stash-rescue --stat` shows deletions across `installer/`, `docs/`, `scripts/dev/`, `site/`, plus **4 reverted safety gates**:

| Revert | File | What was removed | Why it must stay blocked |
|---|---|---|---|
| `ScalerBundle.is_ready` | `src/nexus_scalp/application/live_engine.py:159` | Zero/negative/non-finite `std` check — reverts to `mean is not None and std is not None` | Silent ÷0 → `±inf → nan_to_num → -1.0` poison, no-silent-fallback violated |
| `LiveEngine._rebind_live_temporal_contract` | `live_engine.py:440-560` | 120-line temporal-contract rebind + gap invalidation | Trades on wrong `L`/`max_gap_us`, sequence/tensor mismatch, degrades `BLOCKED`→`AVAILABLE` |
| `CHECK-MDL-03` era fix | `src/nexus_scalp/forensics/checks_features.py:372` | Artifact-own `feature_schema_id` dimension truth → reverts to lagging `ACTIVE_SCHEMA_ID` | Every canonical 70D `70d_liquidity/scalp_v3` deployment flagged CRITICAL false positive |
| `ARCH-SEQ-UNIFY` SSoT | `src/nexus_scalp/model_generation/sequence.py` | Re-exports from `temporal_contract` → hard-coded literals (`L=32`, `70`) | Breaks single-source-of-truth; drift between train/live/replay |

**Verdict:** Branch is an *inverted* stash (hard `git stash push --include-untracked` + selective commit attempt). It captures a valid WIP moment but, merged as-is, would **re-introduce 4 P0/P1 regressions**. **Do not merge. No cherry-pick without per-hunk triage.** Preserved as `pinc-stash-rescue` for manual hunk recovery if needed.

---

## 4. Special Deep Audit Areas — Result

* **Model/ML pipeline:** Only real ML code in stashes is `three_model.py` fast-path (see §5). No scaler, tensor shape, dataset hash, label, champion, `torch.load`, or retrain change in any stash requires action. `pinc` reverts above are the only pipeline risk — blocked.
* **Live trading/execution:** No execution, sizing, risk-limit, or order-routing change in any stash. `pinc`'s gap/tensor revert would have permitted trading on degraded model — blocked.
* **Persistence/state:** No SQLite/Postgres, flush, or candidate/champion write in any stash. No migration. Test `nse_bug223_failsbefore` shows only CRLF noise. No corruption risk.
* **Security:** No model-swap endpoint, `pickle`/`torch.load`, API auth, or file-load change in stashes. No path traversal.
* **Data/broker:** No spread, OHLCV, or state-machine change in stashes.

---

## 5. The Only Verified Production Fix — `three_model.py` 70d Fast Path

| Signal | Detail |
|---|---|
| Source | `stash@{2}` and `stash@{3}` identically: `build_feature_frame(..., variant!=50d_main) → compute_70d_frame → compute_70d_frame_fast` |
| Semantics | `compute_70d_frame_fast` docstring: *"byte-identical to `compute_70d_frame`, same contract, same columns, same values — O(n·window) vs O(n²)"* |
| Verification (isolated) | 200 synthetic M1 bars → slow vs fast: `cols equal 80/80`, `rows 146/146` each, `max │feat_0 diff│ = 0.0`, `max │feat_0..4 diff│ = 0.0` |
| Lint | Raw patch left dead `from schema_v2 import compute_70d_frame` → `ruff F401` — repaired by removing dead import; final `ruff check` clean |
| Tests | `test_three_model_pipeline 5/5`, `test_70d_bug106_incremental_phase19 2/2`, `test_70d_incremental` slow suite (opt-in) — all green; `py_compile` clean |
| Risk | **LOW** — perf only, preserves OOS parity, no new branch |

---

## 6. What Was NOT Recovered and Why

* `stash@{0}` empty — nothing to recover (duplicate of a clean stash).
* `stash@{1}` — stale base + site-only + would revert `3cd6b6a7` int-label fix if forced; rejected as obsolete/duplicate.
* `stash@{2}` status bump (6→8) — superseded (`main` now at `9.0.9`). Not applied.
* `stash@{2}` docs-enhance wiring + `stash@{2}/@{3}` 615-line FLAG tail — **deferred P3** (see blockers). `main` currently has 600 `FLAG_BUILD_INDEX` lines after `raise SystemExit` — dead code, `ruff` clean, `py_compile` clean, but intentionally not cleaned in this recovery batch to keep scope minimal. Wiring for `docs-enhance.css/js` was removed at `d267fbd4` to fix `F841` (unused locals) — the variables *were* used in the prior `shell()` template but the late revert removed the template refs. Re-wiring needs a single coherent PR (template + copy loop + assets) with a `ruff`/`build_site` end-to-end build test — not a drive-by stash pop.
* `stash@{2}` untracked `scratch/ns_*` probes, `.hermes/plans`, `_splice.py` — rejected (tracked `scratch/` probes are per-memory rule noise; `scratch/` is tracked but probes are ephemeral).
* `stash@{4}` / `stash@{5}` — docs-only, overlapping and contradictory (add vs remove FLAGSHIP), no production code — deferred.
* `pinc-stash-rescue` — rejected per §3.
* `nse_qa_head_wt` BUG-160 +119 — sound fail-before proofs; deferred to `BUG-160` branch (needs `release.yml` pre-stage + ISCC contract first — see that branch's logic).

---

## Appendices

* Forensic snapshots: `forensic_recovery_20260904/stash-{0..5}.patch`, `stash-{i}-tracked.patch`, `stash-{i}-parent.txt`
* Post-merge verification: `ruff check` clean on `three_model.py`; `pytest test_three_model_pipeline -q` 5 passed
* No stash was popped/dropped. `pinc-stash-rescue` worktree left intact at `C:/tmp/pinc-stash-wt`.
