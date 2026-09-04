# MLFixing — ML-lane handoff (everything done so far + future path)

> **Purpose.** Single entry point for any agent working the ML-repair lane of NSE
> (feature contracts → datasets → training → champion artifacts → live serving).
> Read this BEFORE touching `src/nexus_scalp/features*`, `model_generation/`,
> `training/`, `model_lifecycle/`, or `artifacts/models/`.
>
> **Contract:** read `agents/skill.md` + `agents/bugs.md` first; ledger discipline:
> BUG-NNN only for PROVEN defects; grep `^## BUG-` in `agents/bugs.md` before
> claiming a number (parallel sessions take numbers fast); claim a TASK-ID in
> `agents/taskboard.md` before starting; `<AGENT>: <imperative>` commit messages,
> commit-per-step. Repo python = `.venv/Scripts/python.exe` (always `-m`).
> Quality gate before push = `beforePush.sh` (ruff, ruff format, mypy, CRITICAL suite).
>
> **Maintenance rule:** append dated updates at the bottom (`## Doc log`), never
> rewrite history sections. Evidence over claims: every statement carries its
> commit / file / probe reference. This doc coordinates the lane; it does not
> replace the ledgers.

- LAST UPDATED: 2026-09-03 ~21:20 +03:30 (Iran) — Nexus-Main (merged handoff v2)
- Repo head at writing: `f72c9dac` (on main; base evidence HEAD `7882c39c`)
- Engine state at writing: NOT running (web/API on 127.0.0.1:8099, PID 28360,
  `engine_running=false`; last ASYNC RETRAIN 2026-09-03 20:44:00 local)

---

## 1. MISSION CONTEXT (why this lane exists)

Live engine (XAUUSD, PAPER) funneled to NO_TRADE ~98%+ ("TASK-TDF funnel").
Forensics decomposed the funnel into independent root causes. The two DOMINANT
ML-side causes, both PROVEN with executed probes:

1. **BUG-225 — the serving champion is untrained noise** (P0). The live champion
   checkpoint was BYTE-IDENTICAL to a fresh `ScalpNet` init minted under
   `torch.manual_seed(42)` (the trainer pins the process-global RNG in
   `WalkForwardTrainer.__init__`). Confidence ceiling ~0.335 vs gate 0.40–0.60
   ⇒ mathematically unreachable ⇒ permanent NO_TRADE. Detection landed;
   runtime repair (retrain + governed promotion) still OPEN.
2. **MLPWR-06-02 — train/live HTF feature-window asymmetry** (P0-class,
   probe-proven, NOT yet a BUG row, NOT fixed in code). Details in §4.

Secondary ML-side defects already fixed: BUG-190, BUG-197B, BUG-217 (news family
train/live encoding), BUG-185 (retrain-record width), BUG-141 (bundle clobber
width guard), BUG-183 (purge/embargo defaults), BUG-228 (trainer
zero-improvement misreport). Adjacent funnel causes fixed outside this lane:
BUG-229 (reason-code misattribution), BUG-230 (WARMUP regime seam).

---

## 2. DONE — evidence ledger

### 2.1 Feature-contract repairs (landed, committed)

| BUG | What | Where | Commit |
| :-- | :--- | :---- | :----- |
| BUG-190 | live 70D news block read raw `CurrentNewsContext.model_dump()` — 4/10 slots wrong keys | `_build_live_feature_vector` / `_build_retrain_record` → canonical projection (`governance.alignment.vectorize_news_context` + `shadow70.build_news_10`) | CHG-0038 fidelity-audit lane |
| BUG-197B | slot 50 carried RAW aggregate event count ⇒ every tick with ≥4 events failed `[-3,+3]` and blocked ALL 70D inference (13k+ failures in one log) | `vectorize_news_context` now emits bounded 0/1 flag at training-distribution max | `6b893f04`, ledger `5a895ab7` |
| BUG-217 | news state encoding BREAKING=4.0 / STALE=5.0 exceed `[-3,+3]` at slot 59 (latent; would have blocked all 70D on a BREAKING event) | repaired producer-side, clamped to training semantics (CHG-0052) | `c576dfac` |
| BUG-185 | rolling retrain buffer class-locked to 50D ⇒ every online fine-tune silently skipped while 70D champion served | `_retrain_record_dim()` builds records at loaded bundle width | `203f1873` + `b873c047` |
| BUG-141 | 70D bundle clobbered by 50D checkpoint write; no width guard on artifact writers | width-contract guards on writers + recovery recipe | `agents/bugs.md` BUG-141 |
| BUG-183 | production research path ran purge/embargo = 0.0 despite BUG-140 constants (false provenance) | wired `DEFAULT_PURGE_SECONDS=300` / `DEFAULT_EMBARGO_SECONDS=60` into pipeline/OOS/walk-forward/backtest | `11ea316`, `128f87c`, `967a468` |

### 2.2 BUG-225 — untrained champion (detection LANDED, repair PENDING)

- Full ledger row: `agents/bugs.md` `## BUG-225` (complete root-cause chain and
  evidence artifacts).
- Detection landed in commit `3f5f9db7`:
  - `src/nexus_scalp/model_lifecycle/integrity.py::detect_untrained_fresh_init(path, dim)`
    — byte-compares the checkpoint to the canonical seed-42 fresh init (exact,
    causal, cheap).
  - `CHECK-MDL-02 check_model_semantic_health()` wired into the deploy-gate
    Model group (CRITICAL + `UNTRAINED_CHAMPION_ARTIFACT` on byte-equal).
  - `tests/unit/test_bug225_untrained_champion_canary.py` (7 tests) registered
    in `tests/critical_suite.txt`.
  - `test_real_champion_artifact_is_trained` is the runtime-facing invariant —
    INTENTIONALLY RED when BUG-225 landed; GREEN as of 2026-09-03 20:44 (see
    §3.1 caution — one green canary ≠ "repair complete").
- Self-perpetuation mechanism (proven): quality gate correctly REJECTS every
  online fine-tune against the degenerate baseline and rolls back to baseline —
  but the baseline IS the fresh init, and LiveEngine persisted the returned
  model unconditionally (`ASYNC RETRAIN SUCCESS` + atomic save after
  `accepted=False`).

### 2.3 BUG-228 — trainer honesty on zero-improvement fine-tunes (FIXED)

- Commit `52615bf7`: when early-stop restores a `best_state` equal to baseline
  (no epoch ever beat it), the trainer logs honestly at INFO ("no improvement;
  keeping baseline weights"), skips the gate/rollback theater, and genuine gate
  rejections go through the structured logger (raw ANSI print removed).
  Regression: `tests/unit/test_walk_forward_trainer.py::
  test_wf_zero_improvement_early_stop_skips_quality_gate_rejection`.
- Live-confirmed working: 2026-09-03 20:44:00 retrain window logged the honest
  line (`baseline_acc=0.667 ... val_acc=0.667`).

### 2.4 Live runtime observations (2026-09-03, verified on this machine)

- Champion verify lines (logs/info/2026/09/2026-09-03.log):
  `[MODEL] CHAMPION VERIFIED hash=a4b95406088ed618 model_id=primary_scalp
  version=v1.0` at 18:48 / 18:49 / 19:48 local — matches the on-disk 70D file
  hash (sha256-16 `a4b95406088ed618`, mtime 2026-09-03 20:44).
- 20:44:00 local: `ASYNC RETRAIN START buffer_size=300` → 20:44:00.872 the
  BUG-228 honest line (no improvement; baseline kept) → `ASYNC RETRAIN
  SUCCESS`. Consequence: `70d_liquidity/model.pt` mtime changed to 20:44 and
  the canary now reports `(False, 'DIVERGES_AT:input_projection.weight')` —
  no longer byte-identical to the fresh init, but **trained-quality is NOT
  proven** (see §3.1 caution and §5 step 4 for why the divergence itself needs
  explaining).
- Engine snapshot 17:55Z: `engine_running=false` (only the API/UI process was
  listening). Per the standing rule: engine lifecycle is owned by the USER
  session — do NOT start/stop/restart without explicit user consent.

---

## 3. CURRENT ARTIFACT TRUTH TABLE (canary run 2026-09-03 ~21:05 local)

### 3.1 The 70d_liquidity artifact is NO LONGER the fresh init

- `artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt`
  sha256-16 `a4b95406088ed618`, mtime 2026-09-03 20:44.
- `detect_untrained_fresh_init(..., 70)` → `(False,
  'DIVERGES_AT:input_projection.weight')`. Canary suite 7/7 GREEN today.
- **CAUTION (do not skip):** "diverges from fresh init" ≠ "well trained". The
  current weights are the product of online fine-tunes whose baseline was the
  degenerate init and whose labels came from paper fills (BUG-225 residual
  risk (b)). Real repair is still a CLEAN-dataset retrain + governed
  promotion (§5). The canary only proves "not the canonical fresh init".
- **P0-3 (explain before trusting):** the ONLY retrain window since detection
  logged "no improvement; keeping baseline weights" — yet the checkpoint now
  diverges at exactly `input_projection.weight`. Either (a) a legitimate
  small update slipped through a path that bypassed the gate, or (b) mint
  nondeterminism (seed handling). Diff the 31 tensors against the seed-42
  init, trace the 20:44 write path, confirm whether an `accepted=True` write
  occurred; file the answer in the BUG-225 row.

### 3.2 The 50D artifacts ARE STILL fresh inits

| Artifact | Canary | sha256-16 | mtime | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `XAUUSD/v1.0.0/model.pt` (50D) | `(True, BYTE_EQUAL_TO_FRESH_INIT)` | `0872ae0b85b3c74b` | 08-24 06:46 | CONTAMINATED |
| `EURUSD/v1.0.0/model.pt` (50D) | `(True, BYTE_EQUAL_TO_FRESH_INIT)` | — | 08-21 12:19 | CONTAMINATED |
| `50d_main` / `70d_news` bundles | diverge (trained) | — | — | Clean per BUG-225 probes |

If any boot path serves a v1.0.0 artifact, it serves untrained noise. Decide:
retire or retrain (§5 step 9).

### 3.3 UNRESOLVED CONFLICT: which bundle does a boot actually serve?

- `configs/base.yaml`: `model_artifact_path:
  artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt` (70D).
- But the live API on `127.0.0.1:8099` (2026-09-03 ~21:00) reported:
  MODEL = `...\XAUUSD\v1.0.0\model.pt (31 tensors)`, `FEATURE_SCHEMA scalp_v1
  / 50D (legacy 50D) (serving bundle: model.pt)`, MODEL_CONTRACT WARNING
  `NO_MODEL_METADATA` (bundle metadata incomplete), `engine_running: false`.
- **Interpretation:** an API/boot path still resolves the LEGACY 50D artifact
  (a fresh init!) while config declares the 70D path. Until reconciled, every
  "is the champion fixed?" answer is boot-mode dependent.
- **FIRST ACTION for the next agent:** read the boot/champion-loader code path
  (or boot with user OK) and pin WHICH file the champion loader opens per
  mode; assert boot-time `CHAMPION VERIFIED hash == sha256(file)`; log the
  resolved path. Port drifts — ground truth is `curl /health` + `/api/status`
  on the live port.

Scalers: `70d_liquidity/model.scaler.npz` mtime 09-03 06:11 — any retrain must
keep scaler/model coherent (CHG-0046 D6 `scale_like_champion` semantics:
trainer std floor + clip `[-5,5]`).

---

## 4. NEW FINDING — NEXUS-MLPOWER lane 06: TRAIN vs LIVE HTF window asymmetry
   (probe-proven, NOT yet a BUG row — register before fixing)

**Symptom:** full-vector TRAIN-vs-LIVE parity corpus is bit-exact on 68/70
features (`PARITY VERDICT: MISMATCH`, max_delta 3.0, TOL 1e-12) — mismatches
ONLY at idx 41 `htf_h1_momentum` (train 0.0 / live 3.0) and idx 42
`htf_m30_structure` (train 0.0 / live 1.0). Name registry + schema hash
`235b8fccc96b7e0e` identical on both sides.

**Root cause (caller-level, static + dynamic evidence):**
- `ScalpFeatureEngine.compute_from_bars` slices the LAST 55 bars for base
  features (`scalp_features.py:16 tail_bars = completed_bars[-55:]`) but
  aggregates HTF from the FULL `completed_bars` list passed in (`:231-234`
  aggregate_bars 15/30/60/240).
- TRAIN builder: `model_generation/schema_v2.compute_70d_frame:74`
  `window = all_bars[max(0, i - 54) : i + 1]` → ALWAYS 55 bars → H1 bucket
  count ≤1 → h1_momentum structurally 0.0 in EVERY training row.
- LIVE caller: `live_engine._process_tick_pipeline:3554-3557` passes the
  aggregator's completed bars (cap 4000; post-BUG-058 resync standard depth
  ~900) → after ~2h of history (≥2 completed H1 buckets) h1_momentum is real
  (measured 6.77, clipped to 3.0 by feature bounds) and m30_structure becomes
  0/1.
- Depth grid (same synthetic window, re-executed today):

  ```
  depth | h1_mom | feat41 | feat42
     55 |  0.000 |  0.000 |  0.0
     60 |  0.000 |  0.000 |  0.0
    120 |  6.770 |  3.000 |  0.0
    240 |  6.770 |  3.000 |  1.0
   4000 |  6.770 |  3.000 |  1.0
  ```

  Not a math bug — an INPUT HISTORY asymmetry between two callers of the SAME
  engine (`scratch/mlpower_parity_feat41_diag.py`: full 240-bar call ⇒
  `feat_41=3.0 feat_42=1.0`; 55-bar call ⇒ `0.0 / 0.0`).
- Consequence: the champion is fed slot-41/42 values at inference that the
  training distribution NEVER contained (all-zero), through a scaler whose
  std on those slots is ~0 → hard saturation. Same *class* as
  BUG-190/197B/217 (train/live encoding divergence) but in the BASE/HTF
  family. It also poisons every online-retrain record — a plausible
  contributor to the 17+ fine-tune rejections observed 2026-09-02.

**Probe inventory (untracked `scratch/`, re-runnable read-only; commit them
with the fix):**
- `scratch/mlpower_parity_corpus_probe.py` (+ `.out.txt`) — MLPWR-06-01 full
  70-slot parity corpus over the scalp_v3 dataset builder vs live-style
  recomputation (3 windows: n=120 seed=7, n=240 seed=11/23).
- `scratch/mlpower_parity_feat41_diag.py` — isolates feat41/42: full 240-bar
  history vs 55-bar window.
- `scratch/mlpower_parity_htf_window_diag.py` — depth grid 55→4000.
- `scratch/mlpower_parity_htf_live_train_callgrid.py` — static caller grid
  with file:line for both sides.
- `scratch/mlpower_parity_htf_realdepth_probe.py` — audit.db live-depth census
  (needs `artifacts/audit.db`).

**Decision needed (feature-contract owner TASK-03-70D-PARITY lineage + model
owner):**
(a) change the TRAIN builder to pass live-equivalent depth (canonical
semantics = HTF over real history), or
(b) bound live HTF to the 55-bar semantics, or
(c) window-normalize HTF aggregation inside `compute_from_bars`.
ANY choice changes feature semantics → dataset MUST be regenerated and the
champion RETRAINED (§5). Do not hot-fix one side silently. Prefer a fix that
does NOT change feature names/order (schema hash). Register as a BUG row with
this evidence FIRST, add a CHG row, and add a regression pin test (feat_41/42
invariance vs history depth on a synthetic window).

---

## 5. FUTURE PATH (ordered — this is the repair roadmap)

1. **Pin the serving-bundle identity (§3.3).** Reconcile /health vs
   configs/base.yaml; establish per-mode champion resolution; assert boot-time
   `CHAMPION VERIFIED hash == sha256(file)`; log the resolved path. Without
   this, no repair claim is verifiable.
2. **Register + fix the HTF window asymmetry (§4).** Smallest correct layer per
   the contract; regression tests both sides; add to critical_suite; re-run
   `mlpower_parity_corpus_probe.py` → verdict must flip to **MATCH** across all
   windows/seeds.
3. **Regenerate the dataset** from the corrected builder (content-addressed
   dataset_id guards this; TASK-4 fairness gate).
4. **Retrain the champion from CLEAN research data** — canonical path:
   research pipeline `three_model.train_variant("70d_liquidity", ...)` family
   / BUG-141 recovery recipe. NEVER fine-tune the live paper-fill buffer into
   the champion (BUG-225 residual risk (b): those labels are self-fulfilling).
5. **Governed promotion, no auto-promotion:** `validate_candidate` →
   challenger → champion promotion (CHG-0046 shadow evidence;
   `scripts/shadow_replay_evidence.py` runner exists). CHECK-MDL-02 must PASS
   (not UNKNOWN) on the candidate; BUG-166 fingerprint == disk.
6. **Restart the engine WITH the repair artifact ready.** A bare restart
   repairs nothing and the user owns engine lifecycle — never restart without
   user OK.
7. **Keep the canary permanent:** `test_real_champion_artifact_is_trained`
   stays in critical_suite.txt as the runtime invariant (red = incident, not a
   broken gate).
8. **Close the provenance gap:** `walk_forward_trainer._save_metadata` writes
   num_features/schema but no run_id/dataset_id/commit — add them so "which
   data produced this champion" stops being archaeology.
9. **Disposition of the 50D fresh inits (§3.2):** retire or retrain
   XAUUSD/v1.0.0 + EURUSD/v1.0.0; CHECK-MDL-02 already flags them; add bundle
   metadata while there (clears the MODEL_CONTRACT `NO_MODEL_METADATA`
   WARNING).
10. **Optional hardening:** extend the corpus probe (§4) into a CI-friendly
    train-vs-live parity test with synthetic depth (current probe is a scratch
    tool, not a gate).

### Lane hygiene (P1)

- Commit the MLPWR probes from `scratch/` with the §4 fix commit (they are the
  reproducibility evidence; `scratch/` is ruff-excluded but TRACKED — keep
  them runnable read-only).
- BUG-106 (O(n^2) 70D frame builder) still blocks TASK-04 A/B/C benchmark
  EXECUTION per the taskboard — fix or descope explicitly.
- MLPWR-05 registry/name-level identity stays enforced via
  `schema_contract.canonical_feature_names` + `feature_schema_hash` — any
  feature change must re-pin the hash in the same commit.

### Adjacent guardrails (P2 — do not regress)

- BUG-227 waves: strategy constants still need behavior pins (Wave B: policy
  flip-penalty/memory + throttle windows; Wave C: mslie sweep window + regime
  escalation map). `test_temporal_liquidity_phase20.py` still absent from
  `tests/critical_suite.txt`.
- Keep BUG-183 purge/embargo provenance fields flowing into run configs.

---

## 6. VERIFIED ML LEARNING-CHAIN MAP (do NOT re-investigate; from the audited pass)

- Outcomes: `execution/order_manager.py:6067` →
  `experience/intelligence.py:608 record_trade_outcome` → ledger
  `record_outcome` (idempotency key) → research worker `_refresh_dataset`
  rebuilds from the immutable ledger.
- Online path: `_rolling_feature_records` deque(4000) → every 50 bars
  (≥300 rows) → `_trigger_async_online_fine_tune` →
  `walk_forward_trainer.fine_tune_online` (clone-safe, purge, class-balanced
  focal loss, quality gate, BUG-141 width-guarded atomic save + provenance
  re-register).
- Landed fixes in this chain: BUG-183 (purge/embargo SSOT constants actually
  wired), BUG-185 (retrain buffer width follows the loaded bundle contract,
  was 50D-locked), BUG-228 (zero-improvement skip + structured logging),
  BUG-226 (PAPER rows excluded from canonical accounting metrics).

---

## 7. VERIFICATION PLAYBOOK (run before claiming anything)

```bash
cd C:/Users/Capsizer/source/repos/NexusTradingForexBot

# 1. Champion canary (7 tests; the last is the live-artifact invariant)
.venv/Scripts/python.exe -m pytest tests/unit/test_bug225_untrained_champion_canary.py -q

# 2. Per-artifact fresh-init status
.venv/Scripts/python.exe -c "import sys; sys.path.insert(0,'src'); \
from nexus_scalp.model_lifecycle.integrity import detect_untrained_fresh_init as d; \
print(d('artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt',70)); \
print(d('artifacts/models/scalp/XAUUSD/v1.0.0/model.pt',50)); \
print(d('artifacts/models/scalp/EURUSD/v1.0.0/model.pt',50))"

# 3. MLPWR asymmetry reproduction (before AND after the §4 fix)
.venv/Scripts/python.exe scratch/mlpower_parity_htf_window_diag.py
.venv/Scripts/python.exe scratch/mlpower_parity_corpus_probe.py   # must be MATCH after fix

# 4. Live state (port DRIFTS — probe, don't assume 8080/8099)
curl -s -m 5 http://localhost:8099/health | head -c 400
netstat -ano | grep LISTEN | grep -E ":80[0-9][0-9]"

# 5. Trainer honesty lines (BUG-228)
grep -E "no improvement|QUALITY GATE" logs/info/2026/09/2026-09-03.log | tail
```

Pre-push gate: `beforePush.sh` / `beforePush.ps1` via
`.venv/Scripts/python.exe -m ...` (ruff → ruff format → mypy → CRITICAL suite
→ deploy gate). NOTE for CI: while contaminated artifacts exist on a machine,
the CRITICAL suite shows exactly 1 red test
(`test_real_champion_artifact_is_trained`) — that red IS the incident signal,
not a broken gate.

---

## 8. GUARDRAILS FOR THE NEXT AGENT

- Engine runtime is USER-OWNED: no kill/restart without explicit user OK.
- Working tree has parallel agents' WIP (live_engine.py, adapters, Web/,
  release/, dependency_intelligence/, ...) — NOT yours; never reset/stash/
  clean it. Pre-commit: `git diff --cached --name-only | grep -cvx <your file>`
  must be 0 (fresh shell each call; parallel agents can empty your index —
  re-`git add` right before commit; absorbed commits verified via
  `git show HEAD:<path>`).
- Registry updates are ADDITIVE (`agents/bugs.md`, `agents/taskboard.md`).
- `scratch/` probes: never delete; commit evidence probes with their fix.
- Windows: patch tool CRLF-mangles; re-read before patch; repo venv python
  via `-m`.
- Do not trust "trained" claims from dimension checks or green unit tests
  alone — the whole BUG-225 class passed every structural gate while serving
  noise. Canary + hash + provenance or it did not happen.

---

## 9. Doc log

- 2026-09-03 ~21:20 +03:30 — Nexus-Main: handoff v2. The prior orchestrator
  session's draft (landed in the base commit as a 198-line MLFixing.md) and an
  independently re-verified second draft were MERGED into this single doc.
  New re-verification today: canary suite 7/7, `detect_untrained_fresh_init`
  on 3 artifacts, depth-grid + caller-grid probes re-executed, live /health +
  /api/status on port 8099, ASYNC RETRAIN log line, artifact mtimes.
- Evidence base: BUG-225/228/217/197B/190/185/141/183 ledger rows; commits
  `3f5f9db7`, `52615bf7`, `c576dfac`, `6b893f04`, `203f1873`, `11ea316`;
  MLPWR probe outputs (scratch/, re-executed today).
