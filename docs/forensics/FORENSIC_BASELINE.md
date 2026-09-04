# FORENSIC BASELINE — 2026-09-04T15:40+03:30 (Iran)

> **Operator:** Nexus Main (recovery architect) — read-only baseline
> **Rule:** No stash pop/drop, no reset/clean, no retrain kill, no model promotion

## 1. Snapshot

| Signal | Value |
|---|---|
| Timestamp (local) | 2026-09-04 15:40 +0330 |
| Timestamp (UTC) | 2026-09-04T12:10:00Z |
| CWD | `C:/Users/Capsizer/source/repos/NexusTradingForexBot` |
| HEAD | `979624fcaaebe9a8653df811091f6aa4045bc27a` — `docs(forensics): 6 independent stash audits (one agent per stash)` |
| origin/main | `979624fc` (in sync) |
| origin/HEAD | `979624fc` |
| Branch | `main...origin/main` (up to date) |
| Working tree (main wt) | **Dirty untracked only**: `CONTRACT_AUDIT_REPORT.md`, `SECURITY_AUDIT_SEC_CAPITAL_DATA_BROKER.md`, `STASH_INTEGRATION_MATRIX.new.md`, `FORENSIC_BASELINE.md` (this file) — staged empty |
| Staged | empty |
| Recent commits (5) | `979624fc` (forensic stash audits) → `b015db93` (responsive drawer `Web/*`) → `a5e2ccc4` (ruff heal) → `694ee2b2` (P3 forensics) → `4261c3d2` (site regen) → `3d8dd752` (merge P3 cleanup) |

## 2. Stash Inventory (preserved — 0 dropped)

| # | hash | date | message | base (`^1`) | tree delta |
|---|---|---|---|---|---|
| 0 | `b92cbd90` | 08:38 +0330 | hold-304-site-dirty-for-main (SA-1/6eccae4e) | `16e86d70` flagship | **0 B / 0 lines — EMPTY** (WIP==base==index `15969c2b`) |
| 1 | `07b2ef5d` | 08:36 | hold-303 (SA-2/4e6dc39d) | `959d7d90` status 9.0.8 | 1.4 MB — 340-file `site/_site/**` churn |
| 2 | `483fc0c3` | 08:34 | hold-303 (SA-1/6eccae4e) | `16e86d70` | 3.5 KB — 3 files + 30 untracked probes |
| 3 | `e90013d5` | 08:29 | hold-302 (main) | `66555ea7` | 1.7 MB — FLAG removal + docs-enhance + site regen |
| 4 | `7ad11d9f` | 08:24 | tmp-site (main) | `66555ea7` | 1.7 MB — same as stash 3 minus status |
| 5 | `d3cab5bc` | 08:04 | css-js-old (SA-2) | `16e86d70` | 33 KB — `site/assets/styles.css`+`search.js` |

All 6 exported to `forensic_recovery_20260904/stash-{0..5}.patch` (tracked). No `stash@{i}^3` untracked commit — `git stash show -p` verified.

## 3. Branch Inventory

| Branch | HEAD | Δ vs main | Nature |
|---|---|---|---|
| `main` | `979624fc` | 0 | canonical |
| `forensic/p3-build-site-cleanup` | `54227f52` | merged via `3d8dd752` | FLAG removal — INTEGRATED |
| `forensic/recover-three-model-fast` | `6bb76497` | merged via `3179df9c→4261c3d2` + healed | `compute_70d_frame_fast` — INTEGRATED (now ancestor via merge) |
| `hermes-subagent/*` | ~90 branches | mostly stale fork of `16e86d70`/`d6c0c1a3` (`1177 ahead` illusion) | quiescent — only `subagent-sa-0-8c5a9a11` was docs premium pack (now merged) |
| `pinc-stash-rescue` | `0c90725b` | merge over `797481f7` | **REJECT — reverts 4 safety gates** (see §7) |

## 4. Worktree Inventory

| # | Path | HEAD | Branch | State |
|---|---|---|---|---|
| 0 | `C:/Users/.../NexusTradingForexBot` | `979624fc` | `main` | active |
| 1 | `C:/.../Temp/nse-pr62` | `83a4a856` | (detached) | temp |
| 2 | `C:/tmp/pinc-stash-wt` | `0c90725b` | `pinc-stash-rescue` | **evidence — must not delete** |
| 3–15 | `C:/.../Temp/agent5-inv`, `nexus-main-supervisor/verify_worktree`, `nexus-relcert`, `nqa_wt_bug154`, `nse-merge90`, `nse-pr90`, `nse_bug223_failsbefore`, `nse_pr_resolve/pr47`, `nse_pr_resolve/pr72`, `nse_pre212_wt`, `nse_qa_head_wt`, `nse_security_work/pr62`, `nse_wt` | various | detached | review/cached |
| 16 | `.worktrees/subagent-sa-0-8c5a9a11` | `15d97ca2` | `hermes-subagent/...-8c5a9a11` | docs pack worktree |
| 17 | `.worktrees/subagent-sa-1-b0a24c30` | `dcdc229f` | `hermes-subagent/...-b0a24c30` | **model forensics worktree** (33-pt dump) |
| 18 | `.worktrees/subagent-sa-2-628685f3` | `d3d8e11e` | `hermes-subagent/...-628685f3` | **contract audit worktree** |
| 19 | `.worktrees/subagent-sa-3-fa4df021` | `9c6a2370` | `hermes-subagent/...-fa4df021` | **stash triage + security worktree** |
| 20–22 | `scratch/rungate/781097ee`, `ddcbc9b7`, `eebefab4` | — | (detached) | **prunable** — no backing dir |

## 5. Process Inventory (trading-critical)

| PID | PPID | Since | Command | Action |
|---|---|---|---|---|
| **26528** | 18832 | 2026-09-04 06:36:47 | `...\.venv\Scripts\python.exe scripts/dev/train_70d_liquidity_production.py --dataset-id ds_70d_clean_m1_20260904 --folds 34 --epochs 10 --batch 256 --seed 42` | **ACTIVE 34×10 retrain — DO NOT KILL/RESTART** |
| **25032** | 26528 | 06:36:47 | `...\uv\cpython-3.11.16\python.exe` same args | child worker — alive |
| 2520/2880/17460/etc | — | — | `hermes_cli serve` (3 profiles) | desktop/router — unrelated |

Both retrain PIDs confirmed via `Get-CimInstance Win32_Process` @ 15:06 + 15:14. No duplicate retrain.

## 6. Artifact Inventory (hashes — machine-verifiable)

| Artifact | Size | SHA256 (full) | Tensor / meta | Verdict |
|---|---|---|---|---|
| `artifacts/model_generation/datasets/ds_70d_clean_m1_20260904/dataset.parquet` | 21.6 MB | `3ae687eaaa1f32a64c6d8acc1ab92d4ab9bceb0949d11cfe9e83ea852e3260fe` | `99,946 rows × 101 cols` / `WINDOWS [26916,32,70] valid 22436` | **PASS — canonical, hash matches task** |
| `.../dataset_manifest.json` | — | `dataset_hash 3ae68…` / `feature_schema_hash 235b8fccc96b7e0e` | `feature_schema_id scalp_v3 / label_schema triple_barrier_3class_v1 / class_count 3 / seq_len 32 / production_eligible true` | PASS |
| `artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt` | 1,334,268 | `c8c0b5b06d4c094dc04c9e8ff45cbfffc6f3fb396d42e3df46449068b1dbfd2b` | **`input [128,70] / classifier [4,32] / 331,492 params` scaler `mean/std [70]`** | **P0 INCOHERENCE — meta claims 3, tensor 4** |
| `.../model.meta.json` | 4,293 B | hash `235b8fccc96b7e0e` | `num_features 70 / num_classes 3 / model_head_classes 3 / scalp_v3 / seq_len 32 / dataset_id null` | P0 (dataset_id null, tensor wrong) |
| `.../model.pt.bak_20260904` | 1,335,531 | `763a25f61fe6b7d35da79fc3d2432b5fce59fdd2fc9237e816de20ec79e88d98` | `classifier [4,32]` | P0 |
| `.../model.pt.bak2_1788498799` | 1,334,268 | `c8c0b5b0…` (identical to live) | `classifier [4,32]` | P0 (meta rewritten 2051 B → 4293 B without retraining) |
| `artifacts/models/scalp/XAUUSD/70d_news/model.pt` | 1,335,531 | `2b98f333…` | `classifier [4,32]` meta 4 | P0 if treated as 70D 3-class |
| `artifacts/model_generation/models/wf_candidate/model.pt` | 1,335,403 | `ec84ed21…` | `classifier [3,32] / 331,459 params` meta `scalp_v4 70×3 / production_eligible false` | PASS geometry / FAIL governance |
| `artifacts/model_generation/models/t70d_seq_v1/v2/model.pt` | ~954 KB | — | `head [3,64] TCN_ATTENTION_V1 / 236,803 params / scaler 70D` | PASS (TCN family, not ScalpNet v3) |

For full 33-artifact dump see `artifacts/forensics/model_artifact_forensics_20260904.md` + `.json`.

## 7. Known Revert Risk (BLOCKED)

`pinc-stash-rescue@0c90725b` would revert 4 gates — **must not merge**:

| Gate | File | Effect of revert |
|---|---|---|
| `ScalerBundle.is_ready` | `live_engine.py:159` | zero-std → inf→-1.0 scaler poison |
| `LiveEngine._rebind_live_temporal_contract` | `live_engine.py:440-560` | trading on wrong L/gap |
| `CHECK-MDL-03` era fix | `forensics/checks_features.py:372` | 70D champion false CRITICAL |
| `ARCH-SEQ-UNIFY` SSoT | `sequence.py` | train/live/replay literal drift |

## 8. Existing Blockers (before any new change)

- **P0 — ARTIFACT CONTRACT INCOHERENCE**: 70d_liquidity live tensor 4 vs meta 3 (plus 8 siblings). **BLOCKED FROM LIVE, BLOCKED FROM MERGE (Batch B/C).**
- **P1 — Deserialization**: 9 `torch.load` without `weights_only=True` (RCE before validation).
- **P1 — Hot-swap path traversal + unauthenticated `model_hot_swap` + CORS `*`**
- **P2 — BUG-160 / BUG-239 scaffolding deferred** (release staging, ISCC).

## 9. Commands Executed for This Baseline

```
git status --short --branch; git stash list; git worktree list; git log --oneline -5
git fsck --no-reflogs --unreachable; git reflog --all | head -50
Get-CimInstance Win32_Process (py 26528/25032)
sha256sum dataset.parquet; python -c "torch.load(...weights_only=True) ... classifier.weight.shape"
polars read_parquet (99,946 × 101)
grep -rn FEATURE_DIM|CANONICAL_SEQ_LEN|CANONICAL_CLASS_COUNT src/ --include=*.py
grep -rn torch.load src/ --include=*.py
```

## 10. Preservation

- 0 stashes dropped/popped
- 0 branches deleted
- 0 retrain processes killed
- Unreachable objects retained (`git fsck --no-reflogs --unreachable` — 2 commits `e90013d5`/`ca02591c` kept)
- `forensic_recovery_20260904/stash-{0..5}.patch` retained

*Baseline frozen at HEAD 979624fc before any new integration.*
