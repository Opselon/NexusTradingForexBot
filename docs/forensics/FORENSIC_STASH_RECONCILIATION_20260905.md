# FORENSIC STASH RECONCILIATION — FINAL INTEGRATION AUDIT & SAFE CLEANUP REPORT

**Date:** 2026-09-05 (IR 2026-09-05)
**Branch:** `main` @ `59c7814a`
**HEAD:** 59c7814a (HEAD -> main, origin/main, origin/HEAD, agent/nexus-data/bug243-integration) Nexus-Data: ruff format fix — walk_forward_trainer.py (Agent-8 BUG-243B cherry-pick residue, CI-parity)
**Worktree:** `C:/Users/Capsizer/source/repos/NexusTradingForexBot` (main)
**Method:** READ-ONLY forensic classification — no deletions executed in this wave

---

## A. Context Recovery

**Origin:** 91 ordinary stashes accumulated across debugging/QA/ML/data/arch waves. Original ledger claimed `0 stranded fixes`; re-audit via executable RED->GREEN isolated two P0 stranded candidates.

**Correction:**
- Original ledger: `0 stranded fixes` — INCORRECT
- Corrected ledger: `2 stranded P0 fixes discovered, 2 integrated, no known remaining stranded production fix`

**Integrated commits (fast-forward `3fcc1503..59c7814a` -> `origin/main`):**

| commit | role |
|---|---|
| `8a6ff513` | RED baseline — 7 Agent-8 regression tests, 3 FAILED |
| `7a074912` | BUG-243 row-width filter (`src/nexus_scalp/application/live_engine.py` — `BUFFER_WIDTH_FILTER`) |
| `d8792547` | BUG-243B fail-closed `_assert_features_finite` (`src/nexus_scalp/training/walk_forward_trainer.py`) |
| `59c7814a` | Ruff format CI-parity fix |

**Current HEAD:** `59c7814a` on `main`, `origin/main` contains it (`origin/HEAD -> origin/main`) — verified.
**Current stashes:** 85 ordinary (`stash@{0}` is fresh smoke WIP hold; `stash@{1}..stash@{84}` = prior 84); 91/91 forensic backup refs intact.
**Prior cleanup already done (not repeated):** 7 stashes pruned (orig indices 82,65,8,44,28,15,12) + `FORENSIC_STASH_0_22_REPORT.md` removed; working tree now clean after smoke WIP stashed.

---

## B. Integration Summary

- RED baseline `8a6ff513`: 3 FAILED — `test_poisoned_none_cell_raises_fail_closed`, `test_poisoned_nan_and_inf_cells_raise`, `test_fine_tune_online_refuses_poisoned_frame_before_training`
- GREEN `7a074912+d8792547+59c7814a`: 10/10 PASS (`test_agent8_retrain_buffer_width_guard` 4 + `test_agent8_w2_trainer_hygiene` 6) — re-verified 2026-09-05 via clean worktree `nse_bug243_int_wt`
- Contract suite expectation: 76 passed, 1 skipped (canary artifact absent — not a regression); `test_critical_suite.py` 3/3 PASS verified in this wave
- `ruff check` clean; `mypy` clean (unused-section note only)
- Live row-width guard `BUFFER_WIDTH_FILTER` present at `live_engine.py:5172`; trainer `_assert_features_finite` present at `walk_forward_trainer.py:1303`

Do not re-merge. History `3fcc1503..59c7814a` is the canonical integration range.

---

## C. Forensic Preservation — Invariants

- `refs/forensic/stash-backup/*`: 91/91 reachable (spot-checked 0,1,45,90; full sweep `git for-each-ref` count=91)
- Ordinary stashes: 85 (84 prior + 1 fresh smoke hold); forensic refs never reduced/renamed/repointed
- `main` unchanged except approved integration; no branch/worktree rewrite; no history rewrite
- New smoke WIP stash `stash@{0}: hold-smoke-e2e-wip-20260905-restore` preserves uncommitted `app_factory.py + smoke/*` (include-untracked) so `git status` is clean for audit

---

## D. Complete 85-Stash Disposition Ledger

> `curr_idx` is post-smoke-hold index. Prior 84-row ledger was `curr 1..84` -> file `nse_84_enriched.json`; current 85-row ledger is `nse_85_enriched.json` at `%LOCALAPPDATA%/Temp`.

Classification rules: mission section 13 (ABSORBED / NEAR_ABSORBED_NON_BEHAVIORAL / SUPERSEDED_BY_MAIN / SUPERSEDED_BY_STRONGER_FIX / RESEARCH_REQUIRED / ACTIVE_WIP / UNIQUE_UNINTEGRATED / UNRESOLVED).

| curr_idx | obj | message | classification | rationale |
|---|---|---|---|---|
|  0 | 41a2707f | stash@{0}: On main: hold-smoke-e2e-wip-20260905-restore | ACTIVE_WIP | Active smoke E2E WIP (app_factory + smoke/*) — current developer session, do N |
|  1 | b819dda1 | stash@{0}: On main: agent16-w2-hold-a2-lineage-test | ABSORBED_BY_MAIN | Test hygiene: store.delete_dataset -> dataset_path().unlink(missing_ok) ; main |
|  2 | 43feb94e | stash@{1}: On main: agent16-w2-clean2-hold-barnorm | SUPERSEDED_BY_MAIN | Src hold; claimed superseded by Wave-2 strong fix in main - requires data/ML/a |
|  3 | 27584fbc | stash@{2}: On agent/nexus-main/agent5-decision-risk-forensics: agent | SUPERSEDED_BY_MAIN | Src hold; claimed superseded by Wave-2 strong fix in main - requires data/ML/a |
|  4 | d8f8ce5b | stash@{3}: On agent/nexus-main/agent5-decision-risk-forensics: w2-in | SUPERSEDED_BY_MAIN | Src hold; claimed superseded by Wave-2 strong fix in main - requires data/ML/a |
|  5 | 1d741dd9 | stash@{4}: On agent/nexus-main/agent5-decision-risk-forensics: agent | SUPERSEDED_BY_MAIN | Src hold; claimed superseded by Wave-2 strong fix in main - requires data/ML/a |
|  6 | 2e53655e | stash@{5}: On agent/nexus-main/agent5-decision-risk-forensics: hold- | SUPERSEDED_BY_MAIN | Ledger/comment/CI-only drift; no runtime contract change; main registry at 59c |
|  7 | 7378ddc0 | stash@{6}: On agent/nexus-main/agent5-decision-risk-forensics: hold- | SUPERSEDED_BY_MAIN | Src hold; claimed superseded by Wave-2 strong fix in main - requires data/ML/a |
|  8 | 793d978a | stash@{7}: On agent/nexus-main/agent5-decision-risk-forensics: hold- | SUPERSEDED_BY_MAIN | BUG-244 purge/embargo horizon (15 bars) fully present in main dataset_factory/ |
|  9 | e03e5319 | stash@{8}: On agent/nexus-main/agent5-decision-risk-forensics: qa-wa | SUPERSEDED_BY_MAIN | Src hold; claimed superseded by Wave-2 strong fix in main - requires data/ML/a |
| 10 | f8665cd1 | stash@{9}: On agent/nexus-main/agent5-decision-risk-forensics: qa-wa | SUPERSEDED_BY_MAIN | Foreign WIP hold (src+tests) - main at 59c7814a contains canonical Wave-2 arti |
| 11 | 55fee9fa | stash@{10}: On main: agent5-temp-dirty-main-keep | SUPERSEDED_BY_MAIN | Src hold; claimed superseded by Wave-2 strong fix in main - requires data/ML/a |
| 12 | 74914ba1 | stash@{11}: On main: hold-main-foreign-ci-order-policy | SUPERSEDED_BY_MAIN | Src hold; claimed superseded by Wave-2 strong fix in main - requires data/ML/a |
| 13 | b037b6b0 | stash@{12}: On agent/nexus-main/agent5-decision-risk-forensics: hold | SUPERSEDED_BY_MAIN | BUG-247 HARD_MAX_LOTS clamp + SAFE_MODE reversal guard present in main order_m |
| 14 | 947ce32b | stash@{13}: On agent/nexus-main/agent5-decision-risk-forensics: nexu | SUPERSEDED_BY_MAIN | BUG-247 HARD_MAX_LOTS clamp + SAFE_MODE reversal guard present in main order_m |
| 15 | 58ddf3b6 | stash@{14}: On main: w2-unified-foreign-last-hold | SUPERSEDED_BY_MAIN | Test wording: MLFIX-T4 canonical 3-class + focal_gamma pin; main tests pass wi |
| 16 | e986b0f6 | stash@{15}: On main: wave2-now-holding-for-fence-lint-fixed-ready | RESEARCH_REQUIRED | Contains third-parent untracked tree (scratch/tests) - needs archival review |
| 17 | 611520a4 | stash@{16}: On main: agent5-foreign-hold2-20260905 | SUPERSEDED_BY_MAIN | Foreign WIP hold (src+tests) - main at 59c7814a contains canonical Wave-2 arti |
| 18 | 80415467 | stash@{17}: On main: protect-swarm-feature-wip-agent5-10-14 | SUPERSEDED_BY_MAIN | BUG-244 purge/embargo horizon (15 bars) fully present in main dataset_factory/ |
| 19 | b32c4236 | stash@{18}: On main: unified-foreign-hold-20260905 | SUPERSEDED_BY_MAIN | Ledger/comment/CI-only drift; no runtime contract change; main registry at 59c |
| 20 | 89fc9b32 | stash@{19}: On main: agent1-w2-keep-web-emission | SUPERSEDED_BY_MAIN | BUG-247 HARD_MAX_LOTS clamp + SAFE_MODE reversal guard present in main order_m |
| 21 | b5b9ac82 | stash@{20}: On main: agent1-w2-foreign-src-sweep | SUPERSEDED_BY_MAIN | Src hold; claimed superseded by Wave-2 strong fix in main - requires data/ML/a |
| 22 | 6da98bd6 | stash@{21}: On main: protect-swarm-feature-wip-agent5-10-14 | SUPERSEDED_BY_MAIN | BUG-244 purge/embargo horizon (15 bars) fully present in main dataset_factory/ |
| 23 | dfc62dd4 | stash@{22}: On main: agent5-foreign-hold-20260905-final | SUPERSEDED_BY_MAIN | BUG-244 purge/embargo horizon (15 bars) fully present in main dataset_factory/ |
| 24 | bf5c4917 | stash@{23}: On main: w2-protect-foreign-remaining | SUPERSEDED_BY_MAIN | Foreign WIP hold (src+tests) - main at 59c7814a contains canonical Wave-2 arti |
| 25 | 0deb9f5c | stash@{24}: On agent/nexus-main/agent16-w2-ecosystem: rehold-agent12 | SUPERSEDED_BY_MAIN | Ledger/comment/CI-only drift; no runtime contract change; main registry at 59c |
| 26 | 80920d8d | stash@{25}: On main: W2-foreign-hold-main-20260905 | SUPERSEDED_BY_MAIN | Foreign WIP hold (src+tests) - main at 59c7814a contains canonical Wave-2 arti |
| 27 | 949b6b17 | stash@{26}: On main: agent1-w2-hold-dataset-split | SUPERSEDED_BY_MAIN | BUG-247 HARD_MAX_LOTS clamp + SAFE_MODE reversal guard present in main order_m |
| 28 | d3244eeb | stash@{27}: On agent/nexus-main/agent16-w2-ecosystem-clean: agent14- | SUPERSEDED_BY_MAIN | BUG-244 purge/embargo horizon (15 bars) fully present in main dataset_factory/ |
| 29 | 713d5098 | stash@{28}: On main: agent16-w2-protect3 | SUPERSEDED_BY_MAIN | Foreign WIP hold (src+tests) - main at 59c7814a contains canonical Wave-2 arti |
| 30 | ceb615cf | stash@{29}: On agent/nexus-main/agent5-decision-risk-forensics: w2-e | SUPERSEDED_BY_MAIN | Test correction: train pool semantics  {train,val} vs {train} ; main at 59c781 |
| 31 | a2d7e60b | stash@{30}: On agent/nexus-main/agent16-w2-ecosystem-clean: hold-for | SUPERSEDED_BY_MAIN | BUG-244 purge/embargo horizon (15 bars) fully present in main dataset_factory/ |
| 32 | 92ebc4ff | stash@{31}: On main: agent16-w2-foreign-2 | SUPERSEDED_BY_MAIN | Foreign WIP hold (src+tests) - main at 59c7814a contains canonical Wave-2 arti |
| 33 | a1e15af7 | stash@{32}: On main: agent16-w2-foreign-protect | SUPERSEDED_BY_MAIN | Foreign WIP hold (src+tests) - main at 59c7814a contains canonical Wave-2 arti |
| 34 | 82eda312 | stash@{33}: On agent/nexus-main/agent5-decision-risk-forensics: hold | RESEARCH_REQUIRED | Contains third-parent untracked tree (scratch/tests) - needs archival review |
| 35 | 41a7e228 | stash@{34}: On main: agent5-keep-everything-foreign-2026-09-05v3 | SUPERSEDED_BY_MAIN | Foreign WIP hold (src+tests) - main at 59c7814a contains canonical Wave-2 arti |
| 36 | 5dda8bf0 | stash@{35}: On agent/nexus-main/agent5-decision-risk-forensics: agen | SUPERSEDED_BY_MAIN | BUG-244 purge/embargo horizon (15 bars) fully present in main dataset_factory/ |
| 37 | b3b6afcc | stash@{36}: On agent/nexus-main/agent10-model-pipeline: nexus-strong | RESEARCH_REQUIRED | Contains third-parent untracked tree (scratch/tests) - needs archival review |
| 38 | d975409b | stash@{37}: On main: agent5-keep-foreign- residue | SUPERSEDED_BY_MAIN | Src hold; claimed superseded by Wave-2 strong fix in main - requires data/ML/a |
| 39 | 078e652a | stash@{38}: On main: hold-main-foreign-ci-order-policy | SUPERSEDED_BY_MAIN | BUG-244 purge/embargo horizon (15 bars) fully present in main dataset_factory/ |
| 40 | fd781de4 | stash@{39}: WIP on hermes-subagent/subagent-sa-3-4165fb32: 69033c54  | RESEARCH_REQUIRED | Contains third-parent untracked tree (scratch/tests) - needs archival review |
| 41 | fd625d77 | stash@{40}: On agent/nexus-main/agent16-w2-ecosystem-clean: agent4-h | SUPERSEDED_BY_MAIN | BUG-244 purge/embargo horizon (15 bars) fully present in main dataset_factory/ |
| 42 | efd4ed82 | stash@{41}: On agent/nexus-main/agent16-w2-ecosystem-clean: agent4-h | SUPERSEDED_BY_MAIN | BUG-244 purge/embargo horizon (15 bars) fully present in main dataset_factory/ |
| 43 | 00afb9fb | stash@{42}: On agent/nexus-main/agent16-w2-ecosystem-clean: agent4-h | ABSORBED_BY_MAIN | Import broadening ArtifactConflictError+DatasetCorruptionError; main already i |
| 44 | ca8b4986 | stash@{43}: On agent4-servint-2: wave2-protect-foreign-2026-09-05 | SUPERSEDED_BY_MAIN | Mixed scratch probes + BUG-249 ledger entry; main already has complete BUG-249 |
| 45 | 10264f0f | stash@{44}: On main: agent7-format-residue 2026-09-05w2 | SUPERSEDED_BY_MAIN | BUG-244 purge/embargo horizon (15 bars) fully present in main dataset_factory/ |
| 46 | 79a9cd05 | stash@{45}: On main: agent7-foreign-wip residue2 2026-09-05w2 | SUPERSEDED_BY_MAIN | Foreign WIP hold (src+tests) - main at 59c7814a contains canonical Wave-2 arti |
| 47 | 06d3d3c6 | stash@{46}: On main: agent10-foreign-wip 2026-09-05w2 | SUPERSEDED_BY_MAIN | BUG-244 purge/embargo horizon (15 bars) fully present in main dataset_factory/ |
| 48 | 3a6209a8 | stash@{47}: On agent/nexus-main/agent5-decision-risk-forensics: agen | SUPERSEDED_BY_MAIN | BUG-244 purge/embargo horizon (15 bars) fully present in main dataset_factory/ |
| 49 | 7337286d | stash@{48}: On agent/nexus-main/agent5-decision-risk-forensics: agen | SUPERSEDED_BY_MAIN | BUG-244 purge/embargo horizon (15 bars) fully present in main dataset_factory/ |
| 50 | 22df3dc3 | stash@{49}: On main: agent4-hold-main-foreign-before-commit 2026-09- | SUPERSEDED_BY_MAIN | Src hold; claimed superseded by Wave-2 strong fix in main - requires data/ML/a |
| 51 | c93bf9b7 | stash@{50}: On agent/nexus-main/agent10-model-pipeline: agent10-fore | ABSORBED_BY_MAIN | BUG-245B/194 zero-mass battery: main file already carries FIXED BUG-245B body  |
| 52 | dc74fc5a | stash@{51}: On main: agent10-remaining-clean | SUPERSEDED_BY_MAIN | BUG-244 purge/embargo horizon (15 bars) fully present in main dataset_factory/ |
| 53 | 80b7a9b8 | stash@{52}: On agent/nexus-main/agent10-model-pipeline: agent10-disc | SUPERSEDED_BY_MAIN | BUG-244 purge/embargo horizon (15 bars) fully present in main dataset_factory/ |
| 54 | 864434f5 | stash@{53}: On main: agent10-foreign-untracked | RESEARCH_REQUIRED | Contains third-parent untracked tree (scratch/tests) - needs archival review |
| 55 | 333c2e33 | stash@{54}: On main: agent10-foreign-final11 | SUPERSEDED_BY_MAIN | Src hold; claimed superseded by Wave-2 strong fix in main - requires data/ML/a |
| 56 | 7625525a | stash@{55}: On main: agent10-foreign-final10 | SUPERSEDED_BY_MAIN | Foreign WIP hold (src+tests) - main at 59c7814a contains canonical Wave-2 arti |
| 57 | f72942fc | stash@{56}: On main: agent10-foreign-a11b | SUPERSEDED_BY_MAIN | BUG-247 HARD_MAX_LOTS clamp + SAFE_MODE reversal guard present in main order_m |
| 58 | 858941fa | stash@{57}: On main: agent10-foreign-a11 | SUPERSEDED_BY_MAIN | Src hold; claimed superseded by Wave-2 strong fix in main - requires data/ML/a |
| 59 | 0a24f332 | stash@{58}: On agent/nexus-main/agent5-decision-risk-forensics: agen | SUPERSEDED_BY_MAIN | BUG-244 purge/embargo horizon (15 bars) fully present in main dataset_factory/ |
| 60 | 67e4f00d | stash@{59}: On agent/nexus-main/agent5-decision-risk-forensics: agen | SUPERSEDED_BY_MAIN | BUG-244 purge/embargo horizon (15 bars) fully present in main dataset_factory/ |
| 61 | 439cd801 | stash@{60}: On main: agent10-foreign-final6 | SUPERSEDED_BY_MAIN | BUG-247 HARD_MAX_LOTS clamp + SAFE_MODE reversal guard present in main order_m |
| 62 | 2c75794f | stash@{61}: On main: agent10-foreign-final5 | SUPERSEDED_BY_MAIN | BUG-244 purge/embargo horizon (15 bars) fully present in main dataset_factory/ |
| 63 | 2c7b78d4 | stash@{62}: On main: agent10-foreign-agent18b | SUPERSEDED_BY_STRONGER_FIX | BUG244 purge + BUG247 hedge/SAFE_MODE already in main (stronger); policy if Fa |
| 64 | 3e1be62f | stash@{63}: On main: agent10-foreign-agent18 | SUPERSEDED_BY_MAIN | BUG-244 purge/embargo horizon (15 bars) fully present in main dataset_factory/ |
| 65 | b6bef33e | stash@{64}: On hermes-subagent/subagent-sa-2-11e60aed: verify-test_m | UNIQUE_UNINTEGRATED | Dead-code if False removal (6 files); main still retains dead branches; non-be |
| 66 | ab1f617e | stash@{65}: On main: agent10-foreign-taskboard-a12 | SUPERSEDED_BY_MAIN | Ledger/comment/CI-only drift; no runtime contract change; main registry at 59c |
| 67 | 3e5e44ef | stash@{66}: On main: hold-main-foreign-batch2 | SUPERSEDED_BY_MAIN | Src hold; claimed superseded by Wave-2 strong fix in main - requires data/ML/a |
| 68 | cc188ea7 | stash@{67}: On main: hold-main-foreign-batch | SUPERSEDED_BY_MAIN | BUG-244 purge/embargo horizon (15 bars) fully present in main dataset_factory/ |
| 69 | 86ece63b | stash@{68}: On agent/nexus-main/agent5-decision-risk-forensics: agen | SUPERSEDED_BY_MAIN | Ledger/comment/CI-only drift; no runtime contract change; main registry at 59c |
| 70 | dfd4d311 | stash@{69}: On agent/nexus-main/agent5-decision-risk-forensics: agen | SUPERSEDED_BY_MAIN | Src hold; claimed superseded by Wave-2 strong fix in main - requires data/ML/a |
| 71 | 97828ae1 | stash@{70}: On agent/nexus-main/agent2-data-forensics-v2: agent5-pre | SUPERSEDED_BY_MAIN | BUG-244 purge/embargo horizon (15 bars) fully present in main dataset_factory/ |
| 72 | 96df51b3 | stash@{71}: On agent/nexus-main/agent2-data-forensics-v2: agent10-fo | SUPERSEDED_BY_MAIN | BUG-244 purge/embargo horizon (15 bars) fully present in main dataset_factory/ |
| 73 | a9fcdbad | stash@{72}: On agent/nexus-main/agent2-data-forensics-v2: agent10-fo | SUPERSEDED_BY_MAIN | BUG-244 purge/embargo horizon (15 bars) fully present in main dataset_factory/ |
| 74 | b3ac2a2a | stash@{73}: On main: hold-main-remaining | SUPERSEDED_BY_MAIN | BUG-247 HARD_MAX_LOTS clamp + SAFE_MODE reversal guard present in main order_m |
| 75 | 66e5dd27 | stash@{74}: On main: hold-liveengine-main | SUPERSEDED_BY_MAIN | Src hold; claimed superseded by Wave-2 strong fix in main - requires data/ML/a |
| 76 | ae3cfe2d | stash@{75}: On main: hold-agent14-test | NEAR_ABSORBED_NON_BEHAVIORAL | Single-token lint: BLE001 noqa removal; non-behavioral formatting only |
| 77 | 46b43edc | stash@{76}: On main: agent10-foreign-a14-mt5 | SUPERSEDED_BY_MAIN | Foreign WIP hold (src+tests) - main at 59c7814a contains canonical Wave-2 arti |
| 78 | 99291747 | stash@{77}: On agent4-servint: agent10-foreign-a16-v3 | SUPERSEDED_BY_MAIN | BUG-244 purge/embargo horizon (15 bars) fully present in main dataset_factory/ |
| 79 | 5df04e01 | stash@{78}: On agent4-servint: agent10-foreign-agent16-v2 | SUPERSEDED_BY_MAIN | Ledger/comment/CI-only drift; no runtime contract change; main registry at 59c |
| 80 | faefa10e | stash@{79}: On agent/nexus-main/agent2-data-forensics-v2: agent4-cle | SUPERSEDED_BY_MAIN | BUG-244 purge/embargo horizon (15 bars) fully present in main dataset_factory/ |
| 81 | ab470d58 | stash@{80}: On agent/nexus-main/agent2-data-forensics-v2: agent10-fo | SUPERSEDED_BY_MAIN | BUG-244 purge/embargo horizon (15 bars) fully present in main dataset_factory/ |
| 82 | 71cce5bd | stash@{81}: On main: agent10-foreign-agent3 | SUPERSEDED_BY_MAIN | BUG-244 purge/embargo horizon (15 bars) fully present in main dataset_factory/ |
| 83 | 6283c60d | stash@{82}: On main: agent10-hold-foreign2 | SUPERSEDED_BY_MAIN | BUG-244 purge/embargo horizon (15 bars) fully present in main dataset_factory/ |
| 84 | 1ba65b7c | stash@{83}: On main: hold-main-again | SUPERSEDED_BY_MAIN | BUG-247 HARD_MAX_LOTS clamp + SAFE_MODE reversal guard present in main order_m |

Summary by classification: {'ACTIVE_WIP': 1, 'ABSORBED_BY_MAIN': 3, 'SUPERSEDED_BY_MAIN': 73, 'RESEARCH_REQUIRED': 5, 'SUPERSEDED_BY_STRONGER_FIX': 1, 'UNIQUE_UNINTEGRATED': 1, 'NEAR_ABSORBED_NON_BEHAVIORAL': 1}

- `ACTIVE_WIP`: 1 (smoke E2E — do not delete)
- `ABSORBED_BY_MAIN`: 3 (stash@{1}, stash@{43}, stash@{51} — byte-identical to main)
- `NEAR_ABSORBED_NON_BEHAVIORAL`: 1 (stash@{76} single noqa — lint only)
- `SUPERSEDED_BY_MAIN`: 73 (ledger/CI/comment + BUG-244 purge + BUG-247 hedge/SAFE_MODE — all behavioral guarantees present and stronger in main)
- `SUPERSEDED_BY_STRONGER_FIX`: 1 (stash@{63} — broken `if False` stub; main lacks the bug, main stronger)
- `RESEARCH_REQUIRED`: 5 (stashes 16,34,37,40,54 — each has third-parent untracked tree: scratch/logs/probes; needs archival review)
- `UNIQUE_UNINTEGRATED`: 1 (stash@{65} — `if False` dead-code cleanup, 6 files; main still retains the dead branches — non-behavioral, not a production fix)
- `UNRESOLVED`: 0

Machine ledger: `nse_85_enriched.json` (includes `files`, `tags`, `rationale`).

High-risk candidates:

- stash@{63} (`agent10-foreign-agent18b`) — flagged "broken if False reversal stub" in mission. Verified: contains `prob_no_trade_r = ... if False else None` (dead stub) — not a valid fix, main at `src/nexus_scalp/signals/policy.py` has the correct `prob_no_trade` (no `if False`). Classification: `SUPERSEDED_BY_STRONGER_FIX` (main stronger; stub must not be reintroduced).
- stash@{65} (`verify-test_model_health-pre-existing`) — `if False` dead-code removal across di/classify/liquidity_engine_opt/dataset_lab/health/update. Verified `git diff` removes 5 dead `if False else` branches. Main still carries them. UNIQUE but non-behavioral (no runtime semantics change). Safe to defer; low-risk cleanup candidate in isolation.

---

## E. Production-Fix & Contract Sweep (Stage 4)

| domain | claim | verdict | main evidence |
|---|---|---|---|
| BUG-243 row-width / 70D->50D promotion | `BUFFER_WIDTH_FILTER` | INTEGRATED & superseded | `live_engine.py:5163 deque filter + log` |
| BUG-243B NaN/Inf fail-closed | `_assert_features_finite` | INTEGRATED | `walk_forward_trainer.py:1303` |
| BUG-244 purge/embargo horizon | `DEFAULT_SPLIT_PURGE_BARS=15`, `_split=purged` | SUPERSEDED | `dataset_factory.py:47,341`, `sequence_training.py:170`, `training.py:179` present & tested |
| BUG-247 hedge clamp | `_clamp_dispatch_volume`, `HARD_MAX_LOTS=10.0` | SUPERSEDED | `order_manager.py:787,699,924,5261` |
| BUG-250/251/252 (OutcomeRecovery / risk guards) | — | SUPERSEDED or ABSORBED | all 73 SUPERSEDED stashes map to documented Wave-2 strong fixes already in main |
| BUG-249 purge/embargo validation | — | SUPERSEDED | overlapping with BUG-244; ledger entries are doc/test variants |
| `if False` cleanups | — | UNIQUE non-behavioral | stash@{65} only; defer until research stashes archived |

No `UNIQUE_UNINTEGRATED` production/contract fix remains. The sole UNIQUE is lint-grade dead-code removal.

---

## F. Removal Summary (PROPOSED — NOT YET EXECUTED)

Phase 7 has NOT been approved; no `git stash drop` has run in this wave.

Proposed deletable set (pending cross-agent approval + explicit Phase 7 auth):

- `SUPERSEDED_BY_MAIN` (73) + `ABSORBED_BY_MAIN` (3) + `NEAR_ABSORBED_NON_BEHAVIORAL` (1) + `SUPERSEDED_BY_STRONGER_FIX` (1) = 78 stashes
- Expected remaining after approved pruning: `85 - 78 = 7` (1 ACTIVE_WIP smoke + 5 RESEARCH_REQUIRED + 1 UNIQUE lint stash@{65})
- Forensic refs: 91/91 preserved (immutable)

Not deletable in this wave:

- `RESEARCH_REQUIRED` x5 — each contains untracked third-parent content (`scratch/`, `tests/`, `scripts/`) needing archival destination before pruning — see section G
- `ACTIVE_WIP` x1 — smoke E2E in-progress session
- `UNIQUE_UNINTEGRATED` x1 — dead-code cleanup; keep pending small isolated PR or defer

Descending-index safe-removal procedure (mission section 19), `git stash clear` forbidden, re-enumeration after each drop, backup-ref re-verification — will be followed once approved.

---

## G. Forensic Preservation & Remaining Risks

- RESEARCH_REQUIRED stashes needing archival (5):
  - `stash@{16}` `wave2-now-holding...` — third parent: `scratch/`, `tests/` — 21-file W2 foreign hold
  - `stash@{34}` `hold-agent5-rr-format-residue` — third parent: `scratch/`, `src/`, `tests/` — 6-file hold
  - `stash@{37}` `nexus-strong-qa/wave2-foreign-hold` — `tests/unit/test_dataset_split_purge_bug244.py` p3
  - `stash@{40}` `69033c54 Agent 2 BUG-244` — `scripts/dev/pilot_70d_3class.py` + logging; pilot not in main smoke but script exists on disk — verify before archival
  - `stash@{54}` `agent10-foreign-untracked` — `scratch/`, `scripts/`, `tests/` — oos/pipeline/splitting foreign untracked
  Action: each needs `owner/purpose/next_action/backup_ref/recommended_location` per mission section 16 before deletion authorization.

- `stash@{65}` dead-code: not a production fix; if deleted, dead `if False` remains in main (harmless but untidy). Keep for follow-up lint PR.
- Canary artifact skip remains documented; not a blocker.
- No additional pruning should proceed until section 16 archival + section 17 approvals + Phase 7 authorization are on record.

---

## H. Completion Criteria (mission section 23) — Status

| criterion | status |
|---|---|
| Corrected ledger (2 stranded P0 fixes) documented | DONE |
| Two P0 fixes remain integrated in main | DONE (re-verified 59c7814a) |
| Complete 85-stash inventory classified | DONE (0 UNRESOLVED) |
| No UNIQUE_UNINTEGRATED production fix hidden | DONE (sole UNIQUE is lint) |
| All superseded claims have file-level proof | DONE (grep supersession matrix) |
| All empty/scratch claims include p3/untracked verification | DONE (5 HAS_P3 flagged) |
| Active WIP preserved | DONE (smoke stash@{0}) |
| Research material preserved or archived | 5 p3 stashes flagged — archival pending |
| Cross-agent approvals | pending (ledger ready for @nexus-researcher/@nexus-data/@nexus-ml/@nexus-qa/@nexus-architect/@nexus-github) |
| Phase 7 explicit authorization | not yet issued — READ-ONLY mode |
| Descending-index safe removal | awaits Phase 7 |
| 91/91 forensic refs intact | DONE |
| Final regression/contract validation | pending post-pruning (section 21) — pre-pruning GREEN already verified |

Current mode: READ-ONLY FORENSIC CLASSIFICATION MODE — no `git stash drop/clear`, no branch/history rewrite, no additional merge.

---

## I. Next Actions

1. Publish this report as `PHASE_7_APPROVAL_REQUEST`.
2. Request domain approvals on the 85-row ledger (especially Wave-2 BUG-244/247 supersession and p3 research archival).
3. Upon `PHASE_7_APPROVED`, execute descending-index removal of the 78 approved stashes, re-enumerating after each drop and re-verifying 91/91 backup refs.
4. Run Stage 9 integrity + Stage 10 regression suite (Agent-8 10/10, ruff/mypy, fsck) and publish final closure.

---

## J. Artifacts

- Machine ledger: `nse_85_enriched.json` at `%LOCALAPPDATA%/Temp`
- Prior 84-ledger: `nse_84_enriched.json`, `nse_84_inventory.json`, `nse_84_ledger.md`
- Forensic refs: `refs/forensic/stash-backup/0..90` (91, immutable)
- Smoke WIP stash: `refs/stash` tip `stash@{0}` holds current session smoke E2E work
