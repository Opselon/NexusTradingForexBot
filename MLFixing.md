# MLFixing.md — ML Repair Program State & Future Path

> **Purpose:** single handoff document for every agent working the ML-repair lane
> (untrained champion, train/live feature parity, retrain/promotion path).
> Written 2026-09-03 by Nexus-Main from verified evidence at HEAD `7882c39c` + live probes.
> **Contract:** read `agents/skill.md` + `agents/bugs.md` first; commit-per-step with
> `<AGENT>: <imperative>` labels; grep `^## BUG-` before claiming a number (parallel
> sessions take numbers fast). Repo python = `.venv/Scripts/python.exe` (always `-m`).
> Quality gate before push = `beforePush.sh` (ruff, ruff format, mypy, CRITICAL suite).

---

## 1. PROGRAM STATE AT A GLANCE

| Lane | Status | Owner evidence |
| :--- | :--- | :--- |
| BUG-225 untrained champion (P0) | DETECTION LANDED (commit `3f5f9db7`), runtime repair PARTIALLY moot — see §3 fresh facts | `agents/bugs.md` BUG-225 row, `tests/unit/test_bug225_untrained_champion_canary.py` |
| BUG-228 zero-improvement gate misfire | FIXED (52615bf7) | bugs.md BUG-228 row; live log 2026-09-03T20:44 shows the honest path |
| Train/live parity — news family (BUG-190, BUG-197B, BUG-217) | FIXED producer-side | bugs.md rows; CHG-0052 |
| Train/live parity — HTF window asymmetry (feat_41/42) | **PROBE-PROVEN, NOT REGISTERED, NOT FIXED** | `scratch/mlpower_parity_*` (MLPWR-06 lane) — see §4 |
| Serving-bundle identity (which artifact actually serves) | **UNRESOLVED CONFLICT** — see §3.3 | /health vs `configs/base.yaml` |
| Champion retrain from CLEAN data + governed promotion | **NOT DONE** — the actual repair | §5 path |
| model.meta.json provenance gap (no run_id/dataset_id/commit) | OPEN | skill ref ml-pipeline-audit-bug183-20260901.md |

---

## 2. BACKGROUND — WHY THE MODEL IS "BROKEN" (BUG-225 chain, proven)

1. `WalkForwardTrainer.__init__` pins the process-global torch RNG (`_set_seed(42)`),
   so every fresh-weights mint (cold-start bootstrap, force_fresh,
   `_reinitialize_collapsed_model`) produced THE SAME byte-identical
   `ScalpNet(70,4)` init.
2. The engine persisted that fresh init as champion and re-served it on every boot;
   all structural gates pass on it (70==70 width, 4 classes, registry fingerprint match)
   because the corruption is SEMANTIC, not structural.
3. Confidence gate: 0.40 base (+0.10 range penalty, +0.10 survival) vs achievable
   max ~0.335 on a fresh init => mathematically unreachable => permanent NO_TRADE
   ("Model Confidence (~0.33) < Effective Threshold (0.40–0.60)").
   This dominated the TASK-TDF NO_TRADE-98% funnel (NOT threshold tuning;
   guardian freeze only 21.7% of evals; reason-code misattribution separately fixed
   by BUG-229, WARMUP seam by BUG-230).
4. Online fine-tunes kept getting quality-gate-REJECTED against the degenerate
   baseline (labels from paper fills), and the rejected result was persisted anyway
   ("ASYNC RETRAIN SUCCESS" + atomic save after accepted=False) — self-perpetuating.

Detection (landed): `model_lifecycle/integrity.detect_untrained_fresh_init(path, dim)`
byte-compares the checkpoint to the canonical seed-42 fresh init;
forensic gate `CHECK-MDL-02 check_model_semantic_health()` wired into the deploy-gate
Model group. `test_real_champion_artifact_is_trained` is the runtime-facing invariant
(registered in `tests/critical_suite.txt`).

## 3. FRESH FACTS — verified 2026-09-03 ~21:00 IRST on this worktree (re-verify before trusting)

### 3.1 The 70d_liquidity artifact is NO LONGER the fresh init
- `artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt`
  sha256-16 `a4b95406088ed618`, mtime 2026-09-03 20:44.
- `detect_untrained_fresh_init(..., 70)` → `(False, 'DIVERGES_AT:input_projection.weight')`.
- The canary suite is 7/7 GREEN today — including `test_real_champion_artifact_is_trained`
  which was INTENTIONALLY RED when BUG-225 landed. Today's log shows repeated
  `[MODEL] CHAMPION VERIFIED hash=a4b95406088ed618` (matches file hash).
- **CAUTION (do not skip):** "diverges from fresh init" ≠ "well trained". The current
  weights are the product of online fine-tunes whose baseline was the degenerate init
  and whose labels came from paper fills (BUG-225 residual risk (b)). Real repair is
  still a CLEAN-dataset retrain + governed promotion (§5). The canary only proves
  "not the canonical fresh init".
- 20:44 log sequence: `ASYNC RETRAIN START buffer_size=300` →
  `Online fine-tune produced no improvement over baseline; keeping baseline weights`
  (BUG-228 honest path, no false QUALITY GATE REJECTION) → `ASYNC RETRAIN SUCCESS`.
  model.pt mtime updated 20:44 — the engine persists on every retrain window.

### 3.2 The 50D artifacts ARE STILL fresh inits
- `artifacts/models/scalp/XAUUSD/v1.0.0/model.pt` → `(True, 'BYTE_EQUAL_TO_FRESH_INIT')`
  sha256-16 `0872ae0b85b3c74b`.
- `artifacts/models/scalp/EURUSD/v1.0.0/model.pt` → same verdict.
- If any boot path serves these, it serves untrained noise. Decide: retire or retrain.

### 3.3 UNRESOLVED: which bundle does a boot actually serve?
- `configs/base.yaml`: `model_artifact_path: artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt`.
- But the live API on `127.0.0.1:8099` reported (2026-09-03 ~21:00):
  MODEL check = `...\XAUUSD\v1.0.0\model.pt (31 tensors)` and
  `FEATURE_SCHEMA scalp_v1 / 50D (legacy 50D) (serving bundle: model.pt)`,
  `engine_running: false`, mode PAPER.
- **Interpretation:** an API/boot path still resolves the LEGACY 50D artifact
  (fresh init!) while the config declares the 70D path. Until this is reconciled,
  every "is the champion fixed?" answer is boot-mode dependent.
  FIRST ACTION for the next agent: boot (with user OK — engine lifecycle is
  user-owned) or read the boot code path and pin WHICH file the champion loader
  opens per mode; verify `CHAMPION VERIFIED hash == sha256(file)` at boot.
  Port drifts — ground truth is `curl /health` + `/api/status` on the live port.

## 4. NEW FINDING — NEXUS-MLPOWER lane 06: TRAIN vs LIVE HTF window asymmetry
   (probe-proven, NOT yet a BUG row, NOT committed — scratch/ only)

**Symptom:** full-vector TRAIN-vs-LIVE parity corpus is bit-exact on 68/70 features
(`PARITY VERDICT: MISMATCH`, max_delta 3.0) — mismatches ONLY at
idx 41 `htf_h1_momentum` (train 0.0 / live 3.0) and idx 42 `htf_m30_structure`
(train 0.0 / live 1.0). Name registry + schema hash `235b8fccc96b7e0e` identical.

**Root cause (caller-level, static + dynamic evidence):**
- `ScalpFeatureEngine.compute_from_bars` slices the LAST 55 bars for base features
  (`scalp_features.py:16 tail_bars = completed_bars[-55:]`) but aggregates HTF from
  the FULL `completed_bars` list passed in (`:231-234` aggregate_bars 15/30/60/240).
- TRAIN builder: `model_generation/schema_v2.compute_70d_frame:74`
  `window = all_bars[max(0, i - 54) : i + 1]` → ALWAYS 55 bars → H1 bucket count ≤1
  → h1_momentum structurally 0.0 in EVERY training row.
- LIVE caller: `live_engine._process_tick_pipeline:3554-3557` passes the aggregator's
  completed bars (cap 4000; post-BUG-058 resync standard depth ~900) → after ~2h of
  history (≥2 completed H1 buckets) h1_momentum is real (measured 6.77, clipped to 3.0
  by feature bounds) and m30_structure becomes 0/1.
- Depth grid (same synthetic window): depth 55/60 → feat41=0.0, feat42=0.0;
  depth 120/240/400/4000 → feat41=3.0, feat42=1.0. Not a math bug — an INPUT HISTORY
  asymmetry between the two callers of the SAME engine.
- Consequence: the champion is fed slot-41/42 values at inference that the training
  distribution NEVER contained (all-zero), through a scaler whose std on those slots
  is ~0 → hard saturation. This is the same *class* as BUG-190/197B/217 (train/live
  encoding divergence) but in the BASE/HTF family and NOT yet ledgered.

**Probe inventory (untracked, re-runnable with `.venv/Scripts/python.exe`):**
- `scratch/mlpower_parity_corpus_probe.py` (+ `.out.txt`) — MLPWR-06-01 full 70-slot
  parity corpus over the scalp_v3 dataset builder vs live-style recomputation.
- `scratch/mlpower_parity_feat41_diag.py` — isolates feat41/42: full 240-bar history
  vs 55-bar window.
- `scratch/mlpower_parity_htf_window_diag.py` — depth grid 55→4000.
- `scratch/mlpower_parity_htf_live_train_callgrid.py` — static caller grid with
  file:line for both sides.
- `scratch/mlpower_parity_htf_realdepth_probe.py` — audit.db live-depth census
  (needs `artifacts/audit.db`).

**Decision needed (feature-contract owner TASK-03-70D-PARITY lineage + model owner):**
(a) change the TRAIN builder to pass live-equivalent depth (canonical semantics =
HTF over real history), or (b) bound live HTF to the 55-bar semantics, or
(c) window-normalize HTF aggregation inside `compute_from_bars`.
ANY choice changes feature semantics → dataset MUST be regenerated and champion
RETRAINED (see §5). Do not hot-fix one side silently.

**Before registering:** grep `^## BUG-` in `agents/bugs.md` — BUG-231 is the latest
known row; parallel sessions may have taken 232+. Register with this evidence,
then commit the probes under `scratch/` (scratch is TRACKED in this repo — keep them).

## 5. FUTURE PATH (ordered — this is the repair roadmap)

1. **Pin the serving-bundle identity (§3.3).** Reconcile /health vs
   configs/base.yaml; establish per-mode champion resolution; assert
   boot-time `CHAMPION VERIFIED hash == sha256(file)`; log the resolved path.
   Without this, no repair claim is verifiable.
2. **Register + fix the HTF window asymmetry (§4).** Smallest correct layer per the
   contract; regression tests both sides (train row has nonzero-capable HTF OR live
   bounded to train semantics — per the owner's decision); add to critical_suite.
3. **Regenerate the dataset** from the corrected builder (content-addressed
   dataset_id guards this; TASK-4 fairness gate).
4. **Retrain the champion from CLEAN research data** — canonical path:
   research pipeline `three_model.train_variant("70d_liquidity", ...)` family /
   BUG-141 recovery recipe. NEVER fine-tune the live paper-fill buffer into the
   champion (BUG-225 residual risk (b): those labels are self-fulfilling).
5. **Governed promotion, no auto-promotion:** `validate_candidate` → challenger →
   champion promotion (CHG-0046 shadow evidence / `scripts/shadow_replay_evidence.py`
   runner exists). CHECK-MDL-02 must PASS (not UNKNOWN) on the candidate.
6. **Restart the engine WITH the repair artifact ready.** A bare restart repairs
   nothing and the user owns engine lifecycle — never restart without user OK.
7. **Keep the canary permanent:** `test_real_champion_artifact_is_trained` stays in
   critical_suite.txt as the runtime invariant (red = incident, not a broken gate).
8. **Close the provenance gap:** `walk_forward_trainer._save_metadata` writes
   num_features/schema but no run_id/dataset_id/commit — add them so "which data
   produced this champion" stops being archaeology.
9. **Disposition of the 50D fresh inits (§3.2):** retire or retrain
   XAUUSD/v1.0.0 + EURUSD/v1.0.0; CHECK-MDL-02 already flags them.
10. **Optional hardening:** extend the corpus probe (§4) into a CI-friendly
   train-vs-live parity test with synthetic depth (the current probe is a scratch
   tool, not a gate).

## 6. VERIFIED ML LEARNING-CHAIN MAP (do NOT re-investigate; from the audited pass)

- Outcomes: `execution/order_manager.py:6067` → `experience/intelligence.py:608`
  `record_trade_outcome` → ledger `record_outcome` (idempotency key) →
  research worker `_refresh_dataset` rebuilds from the immutable ledger.
- Online path: `_rolling_feature_records` deque(4000) → every 50 bars (≥300 rows) →
  `_trigger_async_online_fine_tune` → `walk_forward_trainer.fine_tune_online`
  (clone-safe, purge, class-balanced focal loss, quality gate, BUG-141 width-guarded
  atomic save + provenance re-register).
- Landed fixes in this chain: BUG-183 (purge/embargo SSOT constants actually wired),
  BUG-185 (retrain buffer width follows the loaded bundle contract, was 50D-locked),
  BUG-228 (zero-improvement skip + structured logging), BUG-226 (PAPER rows excluded
  from canonical accounting metrics).

## 7. GUARDRAILS FOR THE NEXT AGENT

- Engine runtime is USER-OWNED: no kill/restart without explicit user OK.
- Working tree has parallel agents' WIP (live_engine.py, adapters, Web/, release/,
  dependency_intelligence/ ...) — NOT yours; never reset/stash/clean it.
  Pre-commit: `git diff --cached --name-only | grep -cvx <your file>` must be 0
  (fresh shell each call; parallel agents can empty your index — re-`git add` right
  before commit; absorbed commits verified via `git show HEAD:<path>`).
- Registry updates are ADDITIVE (`agents/bugs.md`, `agents/taskboard.md`).
- Windows: patch tool CRLF-mangles; re-read before patch; repo venv python via `-m`.
- CI note: on machines carrying a fresh-init champion, the CRITICAL suite shows
  exactly 1 red (the canary runtime invariant) — that red IS the incident signal.
- Do not trust "trained" claims from dimension checks or green unit tests alone;
  the whole BUG-225 class passed every structural gate while serving noise.
