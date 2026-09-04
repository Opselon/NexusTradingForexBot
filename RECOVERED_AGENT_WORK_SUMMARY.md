# RECOVERED AGENT WORK SUMMARY — 2026-09-04

> **Recovery window:** `706269f1` → `3179df9c`  
> **Original stashes:** 6 preserved (none popped/dropped)  
> **Snapshots:** `forensic_recovery_20260904/stash-{0..5}.patch` + tracked patches

---

## Integrated (1 change)

| # | Source | What | Why it was safe | Verification |
|---|---|---|---|---|
| **R-1** | `stash@{2}` + `stash@{3}` (duplicate) — `src/nexus_scalp/model_generation/three_model.py` | `build_feature_frame` for `70d_news`/`70d_liquidity`: `compute_70d_frame` → `compute_70d_frame_fast` (completes **BUG-106**; 50d was already fast) | `compute_70d_frame_fast` is documented *byte-identical* (`schema_v2_incremental.py:566`); O(n·window) vs O(n²) is perf-only; no contract/column/row change | Isolated 200-bar slow-vs-fast: `cols 80/80`, `rows 146=146`, `max |feat_0..4 diff| = 0.0`; `ruff` clean (F401 repaired), `py_compile` clean, `test_three_model_pipeline 5/5`, `test_70d_bug106_incremental_phase19 2/2` |

**Commits:**

* `6bb76497` — `forensic: use compute_70d_frame_fast for 70d variants in three_model (BUG-106 extension)` (on `forensic/recover-three-model-fast`)
* `3179df9c` — `forensic: integrate stash 70d-fast fix into main (BUG-106 complete)` — **merge to `main` (current HEAD)**

---

## Duplicate / Obsolete (3)

| # | Source | What | Why not integrated |
|---|---|---|---|
| **D-1** | `stash@{1}` (`07b2ef5d`, base `959d7d90`) | Entire `site/_site/` regen + `search.js` FLAG-JS removal | Stale base (117 commits behind, predates `3cd6b6a7` int-label fix, `8ba90681` era fix); `site/_site` is generated — rebuilding via `build_site.py` is canonical |
| **D-2** | `stash@{3}` `three_model.py` second copy | Same fast fix as `stash@{2}` | Deduped — already covered by R-1 |
| **D-3** | `stash@{0}` (`b92cbd90`) | Empty working stash | Nothing to integrate (0 tracked lines, no `^3`) |

---

## Deferred (3 — intentionally not merged this recovery)

| # | Source | What | Why deferred | Next step |
|---|---|---|---|---|
| **F-1** | `stash@{2}/@{3}/@{4}` | `scripts/docs/build_site.py` — 615-line `FLAG_BUILD_INDEX_0000…0599` dead-code tail after `raise SystemExit`, plus `docs-enhance.css/js` wiring (`shell()` template + `main()` copy loop) | Tail is **dead code** — `py_compile`/`ruff` already clean even without removal. Bundling it with a one-liner ML fix would widen scope; docs-enhance re-wire needs a single coherent PR (template + loop + assets + `build_site` end-to-end build test) | **P3 cleanup PR**: one commit removing `FLAG` tail + one commit re-wiring `docs-enhance` (if the 78K/55K assets are intended for prod docs), both with `ruff` + `python scripts/docs/build_site.py && ls site/_site` gate |
| **F-2** | `nse_qa_head_wt` dirty `tests/unit/test_release_system.py` (+119) | BUG-160 fail-before proofs: `test_verify_checksums_resolve_from_installed_layout` / `_detects_tamper` / `_resolve_from_ci_staged_release_root` | Tests are **sound** but depend on the `release.yml` pre-stage contract (`portable/` checksum/manifest subset embedded) and `ISCC unknown-flag skipifsourcedoesntexist` — not yet on `main`'s release branch | Integrate via the `BUG-160` branch stack (`release.yml` + `check_docs.py` + ISCC) — not as a drive-by to `main` |
| **F-3** | `stash@{5}` (`d3cab5bc`) | `site/assets/styles.css` + `search.js` cosmetic FLAGSHIP expansion | Contradicts `stash@{1}/@{3}` polarity and is superseded by later `b50f85a6/7150e3de` (3501+2849+1332) merges | No action — already subsumed |

---

## Rejected (2 — must not be integrated)

| # | Source | What | Why rejected |
|---|---|---|---|
| **X-1** | `pinc-stash-rescue` `0c90725b` (worktree `C:/tmp/pinc-stash-wt`) | Bulk deletions + 4 reverted safety gates: `ScalerBundle.is_ready` zero-std, `LiveEngine._rebind_live_temporal_contract` + gap invalidation, `CHECK-MDL-03` era fix (70D `scalp_v3`), `ARCH-SEQ-UNIFY` SSoT (`sequence.py` hard-coded literals) | Each revert re-introduces a **P0/P1** regression (silent scaler poison, trading on wrong `L`, false CRITICAL on healthy 70D champion, train/live/replay drift). Branch preserved for manual hunk recovery only. |
| **X-2** | `nse/checkpoint/mt5-pipeline-stash-20260903` + `stash@{2}` untracked `scratch/ns_*` / `_splice.py` / `.hermes/plans` | Noise / foreign-wip probes | `scratch/` probes are ephemeral; checkpoint branch is unrelated hold |

---

## Commit hashes (exact)

* **Integrated into `main`:** `6bb76497`, `3179df9c` (merge)
* **Intentionally NOT integrated (preserved):** stashes `b92cbd90`, `07b2ef5d`, `483fc0c3`, `e90013d5`, `7ad11d9f`, `d3cab5bc`; branches `pinc-stash-rescue@0c90725b`, `nse/checkpoint@a76b0a92`; dirty wts listed above

---

## Validation evidence (post-merge `main` @ `3179df9c`)

* `ruff check src/nexus_scalp/model_generation/three_model.py` → `All checks passed!`
* `py_compile src/.../three_model.py` → pass
* `pytest tests/unit/test_three_model_pipeline.py` → `5 passed`
* `pytest tests/unit/test_70d_bug106_incremental_phase19.py` → `2 passed`
* Isolated slow-vs-fast on 200 synthetic bars: byte-identical (`maxdiff 0.0`)
* `git stash list` unchanged — **6/6 stashes intact**
* `git status --short --branch` → `main` clean, synced to `3179df9c` (ahead of `origin/main@706269f1`)

---

## Remaining blockers (ranked)

| Priority | Item | Detail | Action |
|---|---|---|---|
| **P0** | `pinc-stash-rescue` must not be merged | Reverts 4 capital-safety gates | Keep worktree `C:/tmp/pinc-stash-wt` as read-only evidence; delete workflow should require explicit per-hunk triage |
| **P1** | No open P1 from stashes | All stashes audited; `main` remains at `BLOCKED FROM LIVE` baseline (governance gate, not stash-induced) | — |
| **P2** | BUG-160 release-verifier + ISCC gate | `nse_qa_head_wt` fail-before proofs depend on unreleased `release.yml` pre-stage + ISCC flag | Promote via dedicated `BUG-160` PR stack, not stash recovery |
| **P2** | `nse-relcert/scripts/cert/` untracked tooling | Related to BUG-160 cert pipeline | Promote with BUG-160 |
| **P3** | `scripts/docs/build_site.py` 600-line `FLAG` tail | Dead code after `raise SystemExit`, `ruff` clean but pollutes `git log -S` and size | Single cleanup PR with `ruff` + full `build_site` rebuild gate |
| **P3** | `docs-enhance.css/js` wiring | 78K/55K assets present but `shell()`/`main()` wiring absent after `d267fbd4 F841` removal | Coherent PR together with FLAG cleanup |
| **P3** | `scratch/rungate` prunable wts (3) | `git worktree list` reports 3 with missing backing paths | `git worktree prune` (safe — no refs) |
| **P3** | `hermes-subagent/*` stale-fork noise (90 branches) | `1177+ ahead` is an illusion from forking `16e86d70`/`d6c0c1a3` | Archive or retarget to fresh `main`; branch hygiene issue, not functional |

---

## Recommendation

**SAFE TO CONTINUE VALIDATION** — and **BLOCKED FROM LIVE** *by pre-existing governance* (not by this recovery).

The only production change integrated (R-1) is a perf-only, byte-identical extension of a previously-reviewed fast path, with isolated numerical proof and passing unit gates. No model, execution, persistence, or security contract was weakened; `main` remains buildable, lint-clean, and strictly narrower in scope than before (one fewer `slow` code path). The four high-risk reverts that would have permitted trading on degraded scaler/temporal state remain blocked in `pinc-stash-rescue` and were not merged.

> **Next actions:** `git push origin main` (to publish `3179df9c`), then file the P3 `build_site` cleanup PR and the P2 `BUG-160` release-verifier PR as separate, review-gated changes. Do **not** `git stash drop` or `git worktree prune` until stakeholders have reviewed this report.

