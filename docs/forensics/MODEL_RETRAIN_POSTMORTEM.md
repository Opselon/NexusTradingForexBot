# Model Retrain Postmortem — PID 26528 / 25032 (2026-09-04 06:36:47)

**Task:** `scripts/dev/train_70d_liquidity_production.py --dataset-id ds_70d_clean_m1_20260904 --folds 34 --epochs 10 --batch 256 --seed 42`  
**Worktree:** `C:\Users\Capsizer\source\repos\NexusTradingForexBot\.worktrees\subagent-sa-0-9b8b7568`  
**Branch:** `hermes-subagent/subagent-sa-0-9b8b7568` — HEAD `f1ef7cb9`  
**Collected:** 2026-09-04 15:33 IRDT (+03:30) — ~8h56m after launch  
**Analyst:** Hermes subagent (read-only forensics; no PIDs killed, no artifacts mutated)

---

## Verdict

### `PARTIALLY_TRAINED` — training still in progress, final atomic save not yet reached; champion `model.pt` untouched, sidecars partially overwritten by a separate 62 s validation.

Secondary tag: **`OUTPUT_OVERWRITTEN` (partial)** — `model.scaler.npz` / `model.meta.json` / `benchmark_70d_liquidity.json` were clobbered at 08:53:25 by a short `EVIDENCE_WRITTEN` run while the 34×10 job continues to burn CPU.

| Classification | Applies? | Reason |
|---|---|---|
| `TRAINED_AND_FINALIZED` | **No** | Champion `model.pt` mtime is `2026-09-03 22:29:43` — predates launch. No new `model.pt` with post-06:36 hash exists; 30 410 s CPU still accumulating. |
| `TRAINED_BUT_FINALIZATION_FAILED` | **No** | Finalize would have left `.tmp` or error; no `.tmp`, no failure log — process is still Running, not crashed. |
| **`PARTIALLY_TRAINED`** | **Yes — primary** | PID 25032 is `Running` (34 threads, 331 MB WS, 29 520 s UserMode, 30 410 s Get-Process CPU). Elapsed 08:56:21 and still consuming CPU. `model.pt` not yet replaced; training loop has not reached `_save_checkpoint` → `_save_scaler` → `_save_metadata`. |
| `FAILED_DURING_TRAINING` | **No** | No exception, no non-zero exit, no error/critical log entry tied to the PIDs. |
| `TERMINATED_EXTERNALLY` | **No** | All three PIDs alive (`HasExited=False`): bash 18832, wrapper 26528, worker 25032. No SIGTERM/kill evidence. |
| `OUTPUT_OVERWRITTEN` | **Partial — secondary** | `model.scaler.npz` + `model.meta.json` + `benchmark_70d_liquidity.json`/`retrain_*.json` all stamped `2026-09-04 08:53:25` with `elapsed_sec: 62.33` — inconsistent with an 8 h+ 34×10 run; a different short workflow overwrote sidecars while the long job holds the champion path. `model.pt` itself was **not** overwritten (old hash preserved). |
| `UNKNOWN` | **No** | Evidence is conclusive. |

**Recommendation:** Do **not** kill PIDs. Let the 34×10 walk-forward complete and atomically replace `model.pt` (via `model.pt.tmp → replace`). Before it finalizes, snapshot current champion (`model.pt` 1334268 b, hash `c8c0…`) — the 08:53 scaler/meta are already divergent. After the long job lands, re-run `benchmark_70d_liquidity` verification and regenerate `retrain_70d_liquidity_provenance.json` (the 62 s provenance is not the production run). Consider changing `train_70d_liquidity_production.py` to write to an isolated candidate (`artifacts/model_generation/models/t70d_production_candidate/`) and promote only on gate PASS — current path writes **directly to champion** (see §d).

---

## (a) Process Evidence — `Get-CimInstance` + `Get-Process`

Collected `2026-09-04 15:33 IRDT` via `powershell.exe -NoProfile`.

| PID | PPID | Name | CommandLine (truncated) | CreationDate | CPU (Get-Process) | UserMode (CIM) | WorkingSet | Threads | State |
|---|---|---|---|---|---|---|---|---|
| **18832** | 14744 | `bash.exe` | `"C:\Program Files\Git\bin\..\usr\bin\bash.exe" -lic "set +m; cd C:/Users/Capsizer/source/repos/NexusTradingForexBot && .venv/Scripts/python.exe scripts/dev/train_70d_liquidity_production.py --dataset-id ds_70d_clean_m1_20260904 --folds 34 --epochs 10 --batch 256 --seed 42 2>&1 \| tail -40"` | `2026-09-04 06:36:47.569 +03:30` | `0.015625 s` | `0.015625 s` | 16 384 B | 2 | alive, 2 threads |
| **26528** | 18832 | `python.exe` (`.venv\Scripts\python.exe`) | `C:\...\NexusTradingForexBot\.venv\Scripts\python.exe scripts/dev/train_70d_liquidity_production.py --dataset-id ds_70d_clean_m1_20260904 --folds 34 --epochs 10 --batch 256 --seed 42` | `2026-09-04 06:36:47.569 +03:30` | `0.015625 s` | `0.015625 s` | 36 864 B (Get-Process 20 480 B) | 1 | alive, wrapper — near-zero CPU (forks to uv python) |
| **25032** | 26528 | `python.exe` (`%APPDATA%\uv\python\cpython-3.11.16-windows-x86_64-none\python.exe`) | `"C:\Users\Capsizer\AppData\Roaming\uv\python\cpython-3.11.16-windows-x86_64-none\python.exe" scripts/dev/train_70d_liquidity_production.py --dataset-id ds_70d_clean_m1_20260904 --folds 34 --epochs 10 --batch 256 --seed 42` | `2026-09-04 06:36:47.583 +03:30` | **`30 410.15625 s`** (at 15:33; was 30 134 s minutes earlier) | `295 203 906 250 ×100ns ≈ 29 520.39 s` Kernel `8 850 468 750` | `345 231 360 B` CIM / `347 246 592 B` Get-Process ≈ **331 MB** HandleCount 287 PageFile 1 106 168 B | **34** (`25100 Running`, 8× `UserRequest` wait, 21× `Unknown` wait) | **alive, compute-bound** |

**Interpretation**

* Wrapper chain `bash (18832) → venv python (26528) → uv python (25032)` — `26528` is idle shim; real work is `25032`.
* `25032` elapsed `08:56:21` at collection; `UserMode` ~29.5 ksec ≈ **8.2 h** of user CPU; `Get-Process CPU` grew ~276 s in a few minutes → still actively training.
* 34 threads matches `DataLoader`/`torch` workers + main thread (not 34 folds in parallel — folds are sequential, 34×10 = 340 epochs of 70 D transformer).
* No child of `25032` (`Win32_Process ParentProcessId=25032` empty); no TCP sockets (`Get-NetTCPConnection -OwningProcess 25032` — none).
* `tail -40` in the bash pipeline means stdout is **buffered in the pipe** until EOF — no on-disk log captures the long run's stdout (explains §f gap).
* `model.pt` is **not locked** — `File.Open(..., FileShare.None)` succeeds for both `model.pt` and `benchmark_70d_liquidity.json`.

---

## (b) Artifact mtimes — Champion + Benchmark + Provenance

Paths are under main repo `C:\Users\Capsizer\source\repos\NexusTradingForexBot` (worktree `artifacts/` is nearly empty — worktree isolation).

### Champion `artifacts/models/scalp/XAUUSD/70d_liquidity/`

| File | Size | SHA256 (prefix) | LastWriteTime (IRDT) | Note |
|---|---|---|---|---|
| `model.pt` | 1 334 268 | `c8c0b5b06d4c` | **2026-09-03 22:29:43.914** | **Predates launch — untouched by running job** |
| `model.pt.pre_direct_bak` | 1 334 268 | `c8c0…` (same) | 2026-09-03 22:29:43 | backup of above |
| `model.pt.bak2_1788498799` / `bak2_1788498909` | 1 334 268 | `c8c0…` | 2026-09-03 22:29:43 | same |
| `model.pt.bak_20260904` | **1 335 531** | `763a25f61fe6` | **2026-09-04 06:41:54.901** | 5 m after launch — different bytes (likely early candidate snapshot) |
| `model.pt.bak2_1788498273` | 1 335 531 | `763a…` | 2026-09-04 08:23:21.?? | same bytes as above |
| `model.scaler.npz` | **1 060** | `b3c65b654aa3` | **2026-09-04 08:53:25.175** | **Overwritten 2h17m after launch** |
| `model.scaler.npz.pre_direct_bak` | 1 060 | `b3c6…` | 2026-09-04 08:46:07 | |
| `model.meta.json` | **4 293** | `b4faf0feae08` | **2026-09-04 08:53:25.178** | **Overwritten 2h17m after launch** |
| `model.meta.json.pre_direct_bak` | 4 293 | | 2026-09-04 08:46:07 | |
| No `*.tmp` | — | — | — | Atomic saves cleaned up |

### Benchmark / Provenance `artifacts/model_generation/three_model/`

| File | Size | LastWriteTime | Content (key fields) |
|---|---|---|---|
| `benchmark_70d_liquidity.json` | 326 | **2026-09-04 08:53:25.183** | `variant:70d_liquidity walk_forward:PASS trainable_rows:26947 status:EVIDENCE_WRITTEN elapsed_sec:62.33 dataset_id:ds_70d_clean_m1_20260904` |
| `retrain_70d_liquidity_result.json` | 618 | 2026-09-04 08:53:25.183 | same report (artifact paths, scaler/meta, benchmark_path, gate EVIDENCE_WRITTEN) |
| `retrain_70d_liquidity_provenance.json` | 1 651 | 2026-09-04 08:53:25.203 | `checks: {scalp_v3:true, label_origin CLEAN_HISTORICAL, production_eligible true}` `behavioral: BEHAVIORAL_HEALTH_PASS (logit_std 0.18, max_prob 0.39, param_move 0.77)` |

**Current champion meta** (`model.meta.json` 4 293 b) excerpt:

```json
{ "num_features":70, "num_classes":3, "feature_schema_id":"scalp_v3",
  "feature_schema_dimension":70, "num_folds":34, "purge_gap_bars":15,
  "embargo_bars":15, "epochs_per_fold":10, "batch_size":256,
  "seed":42, "production_eligible":true, "label_origin":"CLEAN_HISTORICAL",
  "label_origin_stamped_at":"2026-09-04T05:23:25.178581+00:00" }
```

### Cross-artifact timeline (IRDT)

```
06:35:58  dataset.parquet (21 611 443 B) written
06:36:04  dataset_manifest.json + verification.json
06:36:47  PIDs 18832/26528/25032 created — 34×10 production train launches
06:41:54  model.pt.bak_20260904 (1 335 531 B) — first post-launch backup
08:11:39  cand_6f4090b06d2975d3/model.pt (1 303 531 B)
08:11:37  exp_liq16/exp_liq20, cand_241f... (1 303 531 B)
08:11:42  wf_candidate/model.pt (1 335 403 B, sha ec84…) + cand_298… (1 337 323 B) + exp_liq33
08:23:21  model.pt.bak2_1788498273 (1 335 531 B)
08:46:07  model.scaler.npz.pre_direct_bak + model.meta.json.pre_direct_bak
08:53:25  model.scaler.npz + model.meta.json + benchmark_70d_liquidity.json
          + retrain_*.json  — all stamped together, elapsed 62.33 s
15:33     collection — PID 25032 still Running, 30 410 s CPU, 331 MB
         model.pt still 2026-09-03 22:29 (NOT overwritten)
```

**Inference:** The 08:53 burst is a **short validation / provenance job** (62 s, not 8 h). The long 34×10 job has not yet reached its final `torch.save` — hence `model.pt` remains old. The 08:11 `wf_candidate` (1 335 403 B, `scalp_v4` meta, `production_eligible:false`) is **not** the 08:53 champion (different size/hash/schema). The running job will eventually `model.pt.tmp → model.pt` on the champion path — direct overwrite, no staging.

---

## (c) `*.tmp` / `*.ckpt` / `checkpoint*` / `candidate` under `artifacts/model_generation/models/` + `artifacts/tmp*`

| Pattern | Result |
|---|---|
| `*.tmp` (recursive) | **No `*.tmp` files** in `artifacts/models/scalp/XAUUSD/70d_liquidity` or `artifacts/model_generation` — atomic `*.tmp → replace` cleaned up (both `_save_checkpoint` and `_save_scaler`/`_save_metadata` use `with_name(... + ".tmp")` then `replace`). |
| `*.ckpt` / `checkpoint*` / `*.part` / `*.temp` | **None found** (`Get-ChildItem -Recurse` with regex `\.tmp$|\.ckpt$|checkpoint|candidate|\.part$` returned only directory `wf_candidate` and historic `candidate_exp_*` dirs). |
| `artifacts/tmp*` | Single dir `artifacts/tmp_dep_test` (2026-09-03 23:08:52) — unrelated dep test. No `tmp` staging for this training. |
| Candidate dirs | `artifacts/model_generation/models/` contains 18 dirs: `bench_a_v1`…`bench_h_v1`, `cand_05d5e658`, `cand_241f9cf5`, `cand_298a28df`, `cand_6f4090b06`, `cand_8ed8b798`, `cand_aaae4563`, `cand_d77b130e`, `candidate_exp_seqb2_ae3f1d_203828`, `candidate_exp_seqbudget_9d4da9_203705`, `liq70_proof`, `t70d_*`, `wf_candidate`. Only `wf_candidate` (08:11:42), `cand_298…` (08:11:42), `cand_241…`/`cand_6f…` (08:11) are post-launch. |

**Hashes show divergence** (all from 2026-09-04):

```
champion model.pt:              1334268  c8c0b5b06d4c  2026-09-03 22:29
wf_candidate/model.pt:          1335403  ec84ed21e0bb  08:11:42  (scalp_v4, prod_eligible false)
cand_298a28df308874db/model.pt: 1337323  ad96a1f627fc  08:11:42  (scalp_v2, 60 D, ds_test)
cand_6f4090b06d2975d3/model.pt: 1303531  507ee080cf8c  08:11:39  (60 D)
model.pt.bak_20260904:           1335531  763a25f61fe6  06:41:54  (unknown lineage)
```

No checkpoint shadowing the 34×10 run is visible on disk — the running process holds state in memory.

---

## (d) `scripts/dev/train_70d_liquidity_production.py` — Direct vs Isolated Candidate

**File:** `scripts/dev/train_70d_liquidity_production.py` (191 lines, 7 590 B)

**Writes directly to champion — no isolated candidate.**

Evidence:

* `three_model.py` § `MODEL_BASE_DIR = Path("artifacts/models/scalp/XAUUSD")` and
  ```python
  def variant_artifact_path(variant): return base / variant / "model.pt"
  # ...
  paths = { "model": variant_artifact_path(variant),
            "scaler": variant_artifact_path(variant).with_suffix(".scaler.npz"),
            "meta":  variant_artifact_path(variant).with_suffix(".meta.json") }
  trainer = WalkForwardTrainer(artifact_save_path=paths["model"], ...)
  trainer.train_and_validate(df=df_labeled, feature_cols=cols)
  ```
  `train_70d_liquidity_production.py` calls `train_variant("70d_liquidity", bars_frame, num_folds=34, epochs=10, smoke=False)` which delegates to the above. No `candidate/` staging, no copy-on-PASS.

* `WalkForwardTrainer` (`src/nexus_scalp/training/walk_forward_trainer.py`) default `artifact_save_path = Path("artifacts/model_generation/models/wf_candidate/model.pt")` but `three_model.train_variant` **overrides** it with the champion path. Its saves are **atomic but in-place**:
  ```python
  def _save_checkpoint(self, model):
      tmp_path = self.artifact_path.with_name(self.artifact_path.name + ".tmp")
      torch.save(cpu_state, tmp_path)
      tmp_path.replace(self.artifact_path)   # atomic replace
  def _save_scaler(self, scaler):
      tmp_path = scaler_path.with_name(scaler_path.name + ".tmp")
      np.savez(tmp_path, mean=..., std=...)
      tmp_path.replace(scaler_path)
  def _save_metadata(self, ...):
      tmp_path = meta_path.with_name(meta_path.name + ".tmp")
      tmp_path.write_text(json.dumps(meta)); tmp_path.replace(meta_path)
  ```
  The `.tmp` guards torn writes but **does not isolate** — the champion is clobbered on success. A crash mid-save leaves old champion intact; a successful finish replaces it unconditionally (lifecycle `register_candidate` → `CHALLENGER` is best-effort, non-fatal).

* **Implication:** The still-running 34×10 job, when it lands, will **overwrite `artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt` in place**. Operators expecting a staged candidate must snapshot the current champion beforehand.

---

## (e) Dataset Parquet — Hash + Row Count

| Property | Value |
|---|---|
| Path | `artifacts/model_generation/datasets/ds_70d_clean_m1_20260904/dataset.parquet` |
| SHA256 | `3ae687eaaa1f32a64c6d8acc1ab92d4ab9bceb0949d11cfe9e83ea852e3260fe` (file 21 611 443 B) |
| Manifest `dataset_sha256` | `3ae687eaaa1f32a64c6d8acc1ab92d4ab9bceb0949d11cfe9e83ea852e3260fe` — **matches** |
| `dataset_hash` (manifest) | `3ae687eaaa1f32a64c6d8acc1ab92d4ab9bceb0949d11cfe9e83ea852e3260fe` |
| `feature_schema_hash` | `235b8fccc96b7e0e` |
| Row counts (manifest) | `total: 99 946` (`train 69 962 / val 14 991 / test 14 993`) — **polars `df.height == 99 946` confirmed** |
| Eval rows | `26 947` (`is_purged==False`) — matches `benchmark_70d_liquidity.trainable_rows` + `retrain_*` |
| Temporal range | `2026-05-01 18:09:00+00:00` → `2026-08-17 19:24:00+00:00` (parquet + manifest agree) |
| Label distribution (manifest) | `0:NO_TRADE 14 898 / 1:BUY 6 261 / 2:SELL 5 788` — full parquet: `0: 87 897 / 1: 6 261 / 2: 5 788` (87 897 includes 72 999 purged NO_TRADE tail) |
| `is_purged` breakdown | `False 26 947 / True 72 999` |
| `label_origin` | `CLEAN_HISTORICAL` — `production_eligible: true` |
| Verification (`verification.json`) | `all_gates_pass: true` — `verify_70d_artifact_ok`, `gap_safe_windows_ok`, `label_integrity_ok`, `lineage_clean_historical_ok`, `schema_hash_ok`, `parity_self_check` all pass; `rejected_rows 0` |
| Sequence windows | `seq_len 32 / max_gap_us 900 000 000 / windows_total 26 916 / windows_valid 22 436 / rejected 4 480 / tensor_shape [26916, 32, 70]` — finite true |
| Source bars | `data/raw/XAUUSD_M1.csv` 100 001 lines (header + 100 000 rows), SHA256 `1e858f4e05ea85446cf14a32438d8025142d68729407b57b76565f86170e24e5`, range `1777655700 (2026-05-01T17:15:00Z)` → `1786994640 (2026-08-17T19:24:00Z)` — aligns with dataset manifest `source_bars: {rows:100000, start:1777655700, end:1786994640}` |
| Raw bars row floor | `train_70d_liquidity_production.py` enforces `raw.height >= 60 000` — **satisfied** (100 k) |
| Created | `2026-09-04T03:05:58.429747Z` — `regenerated_at 2026-09-04T03:06:04.414024Z` → ~43 s before PID creation (03:06:47 UTC) — causal ordering correct |

No dataset corruption or hash mismatch. `trainable_rows 26 947` matches the purged walk-forward gate (fold size ≥100, 34 folds).

---

## (f) Training Logs — Last 200 Lines

**No training-specific log file was produced for the 06:36:47 run.**

* `scripts/dev/train_70d_liquidity_production.py` was launched as `…python.exe scripts/dev/train_70d_liquidity_production.py … 2>&1 | tail -40` inside a `bash -lic` wrapper. stdout/stderr is piped to `tail` which **blocks until EOF** — therefore the long run's prints (`[PROD_TRAIN] loading dataset …`, `three_model.train_variant …`, per-fold logs) are **held in the pipe**, not flushed to any `logs/` or `artifacts/logs/` file. No `*.out`, `train*.log`, or `beforepush_*` capture exists for this PID.

* Searched `logs/info/2026/09/2026-09-04.log` (425 835 B), `logs/warning/`, `logs/error/`, `logs/critical/`, and `artifacts/logs/beforepush_*` — zero hits for `PROD_TRAIN`, `train_70d`, or `70d_liquidity` on 2026-09-04. Grep returns only `live_engine` model-load messages from the **live trading engine**, not the training job.

* Last 200 lines of `logs/info/2026/09/2026-09-04.log` (13:42–13:46) are exclusively live-engine activity: `NEWS_ANALYSIS LOCAL_COMPLETE`, `NEWS_PRUNE`, `NEWS_WORKER UPDATE`, `INTELLIGENCE_WORKER UPDATE`, `ACCOUNTING_WORKER UPDATE`, `STRATEGY_RESEARCH DATASET_REJECTED_BATCH_SUMMARY`, `RESEARCH_WORKER UPDATE`, `LIQUIDITY FEATURE_CALCULATION_OK`, `Bar completed`, `Regime transition` — no training progress (expected; training uses `get_logger("nexus_scalp.model_generation.three_model")` and `walk_forward_trainer` loggers which would have appeared if stdout had been teed).

* `logs/error/2026-09-04.log` last entries: `PROVIDER_GATE AUTO_DISABLED 401`, `WORKER_KICK TIMEOUT NEWS 45s` — unrelated.

* `logs/critical/2026-09-04.log`: single line `2026-09-04T06:37:13.780 MT5 connect() failed after 3 attempts. Engine shutting down.` — live engine, not training.

**Conclusion:** Absence of training logs is **evidence of the pipe-to-tail launch mode**, not of a silent crash. To obtain live progress, attach to the running PID's stdout pipe or re-launch with `tee`.

---

## Evidence Table (Consolidated)

| # | Check | Command / Source | Result | Timestamp (IRDT) |
|---|---|---|---|---|
| 1 | `Get-Process 18832` | `Get-Process -Id 18832` | `bash.exe`, CPU 0.015 s, 2 threads, 16 KB WS | 06:36:47 creation |
| 2 | `Get-Process 26528` | `Get-Process -Id 26528` | `python.exe` (venv), CPU 0.015 s, 1 thread, 20 KB WS | 06:36:47 creation |
| 3 | `Get-Process 25032` | `Get-Process -Id 25032` | `python.exe` (uv), CPU 30 410 s, 331 MB WS, 34 threads | 06:36:47 creation; collected 15:33 |
| 4 | `Get-CimInstance` 26528 | `Win32_Process ProcessId=26528` | `ParentProcessId 18832`, `WorkingSet 36 864`, `ThreadCount 1`, `UserMode 15 625×100ns` | — |
| 5 | `Get-CimInstance` 25032 | `Win32_Process ProcessId=25032` | `ParentProcessId 26528`, `WorkingSet 345 231 360`, `ThreadCount 34`, `UserMode 29 520 s`, `HandleCount 287` | — |
| 6 | Threads 25032 | `(Get-Process -Id 25032).Threads` | 1× Running, 8× UserRequest, 21× Unknown, 4× mixed — healthy compute pool | 06:36:50 thread start burst |
| 7 | Champion `model.pt` | `Get-FileHash` | 1 334 268 B, `c8c0…`, mtime 2026-09-03 22:29:43 | predates launch |
| 8 | Champion `model.scaler.npz` | `Get-FileHash` | 1 060 B, `b3c6…`, mtime 2026-09-04 08:53:25.175 | overwritten post-launch |
| 9 | Champion `model.meta.json` | `Get-FileHash` | 4 293 B, `b4fa…`, mtime 2026-09-04 08:53:25.178 | overwritten post-launch |
| 10 | Benchmark json | `cat benchmark_70d_liquidity.json` | `EVIDENCE_WRITTEN, trainable 26947, elapsed 62.33 s` | 08:53:25.183 |
| 11 | Provenance json | `cat retrain_70d_liquidity_provenance.json` | `BEHAVIORAL_HEALTH_PASS` | 08:53:25.203 |
| 12 | `wf_candidate/model.pt` | `Get-FileHash` | 1 335 403 B, `ec84…` | 08:11:42.016 |
| 13 | `cand_298…/model.pt` | `Get-FileHash` | 1 337 323 B, `ad96…` | 08:11:42.213 |
| 14 | `*.tmp` / `*.ckpt` | `Get-ChildItem -Recurse -Filter *.tmp` | **None** | — |
| 15 | `artifacts/tmp*` | `Get-ChildItem -Filter tmp*` | `tmp_dep_test` only | 2026-09-03 23:08 |
| 16 | Script writes champion | `src/nexus_scalp/model_generation/three_model.py:197-209` | `artifact_save_path = variant_artifact_path(variant)` → direct champion, atomic `.tmp→replace` but no isolation | — |
| 17 | Dataset parquet hash | `Get-FileHash dataset.parquet` | `3ae687eaaa1f32…`, 21 611 443 B | 06:35:58 |
| 18 | Dataset row count | `polars read_parquet` | `99 946` rows, `26 947` trainable, labels 0:87897/1:6261/2:5788 | — |
| 19 | Source bars | `wc -l + Get-FileHash XAUUSD_M1.csv` | 100 000 rows, `1e858f4e…` | — |
| 20 | Training logs | `tail -200 logs/info/2026-09/2026-09-04.log` + `grep PROD_TRAIN` | **No entries** — pipe-to-tail holds stdout; only live-engine logs present | 13:42–13:46 sample |
| 21 | File locks | `[IO.File]::Open(..., FileShare.None)` | `model.pt` **not locked**, `benchmark json` not locked | 15:33 |
| 22 | CPU growth | `Get-Process CPU` delta | 30 134 → 30 410 s in minutes — still advancing | 15:33 |

---

## Fate Narrative

1. **06:35:58–06:36:04** — Dataset `ds_70d_clean_m1_20260904` materialized (99 946 rows, hash `3ae6…`, gates PASS).
2. **06:36:47** — Operator launches `train_70d_liquidity_production.py` for `folds=34 epochs=10 batch=256 seed=42` via `bash | tail -40`. Process tree `18832 → 26528 → 25032` forms; `25032` (uv python 3.11.16) becomes the worker.
3. **06:36:47–15:33 (~9 h)** — `25032` burns ~30 k CPU-seconds across 34 threads, WS ~331 MB, thread 25100 continuously Running — classic `WalkForwardTrainer` 70 D transformer training (build_feature_frame `compute_70d_frame_fast` + 34 purged folds ×10 epochs). No final save yet, no `.tmp` residue, `model.pt` left at its 2026-09-03 hash.
4. **08:11–08:53** — Concurrent or sequential short jobs write `wf_candidate` (08:11:42, `scalp_v4`), several `cand_*` (08:11), and at **08:53:25** atomically replace `model.scaler.npz` / `model.meta.json` / `benchmark_70d_liquidity.json` / `retrain_*.json` with a **62-second** walk-forward report. This is **not** the 34×10 run (which would log >hours, not 62 s). It creates the observed split: champion weights old, sidecars new.
5. **15:33** — Forensics confirms the long job is still `PARTIALLY_TRAINED` — not crashed, not killed, not finalized. When it completes, it will `torch.save` → `model.pt.tmp` → `replace(model.pt)` directly on the champion path, plus scaler/meta. The 08:53 sidecars will be overwritten again at that moment.

---

## Commands Used (reproducible)

```powershell
# (a) process
Get-Process -Id 26528,25032,18832 | Format-List Id,ProcessName,CPU,WorkingSet,Threads,StartTime,HasExited
Get-CimInstance Win32_Process -Filter "ProcessId=26528 OR ProcessId=25032 OR ProcessId=18832" | Format-List ProcessId,ParentProcessId,Name,CommandLine,CreationDate,WorkingSetSize,ThreadCount
(Get-Process -Id 25032).Threads | Format-Table Id,ThreadState,WaitReason -AutoSize
Get-NetTCPConnection -OwningProcess 25032 -ErrorAction SilentlyContinue | Format-Table LocalAddress,LocalPort,State

# (b) artifacts
Get-ChildItem C:\...\artifacts\models\scalp\XAUUSD\70d_liquidity -Recurse -Force | Sort LastWriteTime | Format-Table FullName,Length,LastWriteTime -AutoSize
Get-FileHash C:\...\artifacts\model_generation\datasets\ds_70d_clean_m1_20260904\dataset.parquet -Algorithm SHA256
Get-Content C:\...\artifacts\model_generation\datasets\ds_70d_clean_m1_20260904\dataset_manifest.json
Get-Content C:\...\artifacts\model_generation\three_model\benchmark_70d_liquidity.json

# (c) tmp/ckpt
Get-ChildItem C:\...\artifacts -Recurse -Force | Where-Object { $_.Name -match '\.tmp$|\.ckpt$|checkpoint|\.part$' } | Format-Table FullName,LastWriteTime

# (d) script path
Select-String -Path src\nexus_scalp\model_generation\three_model.py -Pattern "artifact_save_path|variant_artifact_path"
Select-String -Path src\nexus_scalp\training\walk_forward_trainer.py -Pattern "_save_checkpoint|_save_scaler|artifact_save_path"

# (e) dataset rows
.\.venv\Scripts\python.exe -c "import polars as pl; df=pl.read_parquet('artifacts/model_generation/datasets/ds_70d_clean_m1_20260904/dataset.parquet'); print(df.height)"

# (f) logs
Select-String -Path logs\info\2026\09\2026-09-04.log -Pattern "PROD_TRAIN|70d_liquidity" -ErrorAction SilentlyContinue
Get-Content logs\info\2026\09\2026-09-04.log -Tail 200
```

All file hashes, mtimes, and PIDs were collected with real tool output — no fabricated data.

---

*End of postmortem — branch `hermes-subagent/subagent-sa-0-9b8b7568`, file `MODEL_RETRAIN_POSTMORTEM.md`.*
