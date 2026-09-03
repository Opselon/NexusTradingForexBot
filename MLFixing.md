# MLFixing — ML-lane handoff (everything done so far + future path)

> **Purpose.** Single entry point for any agent working on the ML lane of NSE
> (feature contracts → datasets → training → champion artifacts → live serving).
> Read this BEFORE touching `src/nexus_scalp/features*`, `model_generation/`,
> `training/`, `model_lifecycle/`, or `artifacts/models/`.
>
> **Maintenance rule:** append dated updates at the bottom (`## Doc log`), never
> rewrite history sections. Evidence over claims: every statement below carries
> its commit / file / probe reference.
>
> Authoritative ledgers: `agents/bugs.md` (BUG-NNN), `agents/taskboard.md`
> (TASK-ID), `agents/change_control.md` (CHG). This doc coordinates the lane; it
> does not replace the ledgers.

- LAST UPDATED: 2026-09-03 ~21:10 +03:30 (Iran) — Nexus-Main
- Repo head at writing: `7882c39c` (main == origin/main)
- Engine state at writing: NOT running (web/API on port 8099, PID 28360,
  `engine_running=false`; last ASYNC RETRAIN 2026-09-03 20:44:00 local)

---

## 1. Mission context (why this lane exists)

Live engine (XAUUSD, PAPER) was funneling to NO_TRADE ~98%+ ("TASK-TDF funnel").
Forensics decomposed the funnel into independent root causes. The two DOMINANT
ML-side causes, both PROVEN with executed probes:

1. **BUG-225 — the serving champion is untrained noise** (P0). The live
   champion checkpoint was BYTE-IDENTICAL to a fresh `ScalpNet` init minted
   under `torch.manual_seed(42)` (the trainer pins the process-global RNG in
   `WalkForwardTrainer.__init__`). Confidence ceiling ~0.335 vs gate 0.40–0.60
   ⇒ mathematically unreachable ⇒ permanent NO_TRADE. Detection landed;
   runtime repair (retrain + governed promotion) is still OPEN.
2. **MLPWR-06-02 — train/live feature-window asymmetry** (P0-class, unfixed in
   code). `ScalpFeatureEngine.compute_from_bars` slices the LAST 55 bars for
   base features but aggregates HTF features (feat_40..43) over the FULL bar
   list it is handed. Train always hands 55 bars; live hands up to 4000. Same
   market state ⇒ different feat_41/feat_42 ⇒ every live decision after ~2h
   uptime runs on feature values the training set NEVER contained, and every
   online-retrain record is poisoned the same way. Evidence below.

Secondary ML-side defects already fixed: BUG-190, BUG-197B, BUG-217 (news
family train/live encoding), BUG-185 (retrain-record width), BUG-141 (bundle
clobber width guard), BUG-183 (purge/embargo defaults), BUG-228 (trainer
zero-improvement misreport).

---

## 2. DONE — evidence ledger

### 2.1 Feature-contract repairs (landed, committed)

| BUG | What | Where | Commit |
| :-- | :--- | :---- | :----- |
| BUG-190 | live 70D news block read raw `CurrentNewsContext.model_dump()` — 4/10 slots wrong keys | `_build_live_feature_vector` / `_build_retrain_record` → canonical projection (`governance.alignment.vectorize_news_context` + `shadow70.build_news_10`) | CHG-0038 fidelity-audit lane |
| BUG-197B | slot 50 carried RAW aggregate event count ⇒ every tick with ≥4 events failed `[-3,+3]` and blocked ALL 70D inference (13k+ failures in one log) | `vectorize_news_context` now emits bounded 0/1 flag at training-distribution max | `6b893f04`, ledger `5a895ab7` |
| BUG-217 | news state encoding BREAKING=4.0 / STALE=5.0 exceed `[-3,+3]` at slot 59 (latent; would have blocked all 70D on a BREAKING event) | repaired producer-side, clamped to training semantics (CHG-0052) | `c576dfac` |
| BUG-185 | rolling retrain buffer class-locked to 50D ⇒ every online fine-tune silently skipped while 70D champion served | `_retrain_record_dim()` builds records at loaded bundle width | `203f1873` + `b873c047` |
| BUG-141 | 70D bundle clobbered by 50D checkpoint write; no width guard on artifact writers | width-contract guards on writers + recovery recipe | ledger `agents/bugs.md` BUG-141 |
| BUG-183 | production research path ran purge/embargo = 0.0 despite BUG-140 constants (false provenance) | wired `DEFAULT_PURGE_SECONDS=300` / `DEFAULT_EMBARGO_SECONDS=60` into pipeline/OOS/walk-forward/backtest | `11ea316`, `128f87c`, `967a468` |

### 2.2 BUG-225 — untrained champion (detection LANDED, repair PENDING)

- Full ledger row: `agents/bugs.md` `## BUG-225` (read it — it has the complete
  root-cause chain and evidence artifacts).
- Detection landed in commit `3f5f9db7`:
  - `src/nexus_scalp/model_lifecycle/integrity.py::detect_untrained_fresh_init(path, dim)`
    — byte-compares checkpoint to the canonical seed-42 fresh init (exact,
    causal, cheap).
  - `CHECK-MDL-02 check_model_semantic_health()` wired into the deploy-gate
    Model group (CRITICAL + `UNTRAINED_CHAMPION_ARTIFACT` on byte-equal).
  - `tests/unit/test_bug225_untrained_champion_canary.py` (7 tests) registered
    in `tests/critical_suite.txt`.
  - `test_real_champion_artifact_is_trained` is the runtime-facing invariant —
    it stays RED until the champion artifact is actually replaced. NOTE: as of
    2026-09-03 20:44 it passes (see §2.5/§3) — but see the caveat there; do not
    read one green canary as "repair complete".
- Self-perpetuation mechanism (proven): quality gate correctly REJECTS every
  online fine-tune against the degenerate baseline, rolls back to baseline —
  but baseline IS the fresh init, and LiveEngine persisted the returned model
  unconditionally (`ASYNC RETRAIN SUCCESS` after `accepted=False`).

### 2.3 BUG-228 — trainer honesty on zero-improvement fine-tunes (FIXED)

- Commit `52615bf7`: when early-stop restores a `best_state` equal to baseline
  (no epoch ever beat it), the trainer now logs honestly at INFO ("no
  improvement; keeping baseline weights"), skips the gate/rollback theater, and
  genuine gate rejections go through the structured logger (raw ANSI print
  removed). Regression: `tests/unit/test_walk_forward_trainer.py::
  test_wf_zero_improvement_early_stop_skips_quality_gate_rejection`.
- Live-confirmed working: 2026-09-03 20:44:00 retrain window logged
  `Online fine-tune produced no improvement over baseline; keeping baseline
  weights (baseline_acc=0.667 ... val_acc=0.667)`.

### 2.4 MLPWR lane — train/live full-vector parity (root cause PROVEN, fix NOT landed)

Probes (read-only, in `scratch/`, currently UNTRACKED — keep them, commit them
with the fix):

- `scratch/mlpower_parity_corpus_probe.py` (+ `.out.txt`): full 70-feature
  TRAIN (`compute_70d_frame`, canonical dataset builder) vs LIVE-style
  (`ScalpFeatureEngine` direct) parity corpus over 3 windows (n=120 seed=7,
  n=240 seed=11/23), TOL=1e-12. Verdict: **MISMATCH** — 1–2 mismatches per
  window, ALL confined to index 41 (`htf_h1_momentum`) and index 42
  (`htf_m30_structure`). Schema hash `235b8fccc96b7e0e`, 70 canonical names.
- `scratch/mlpower_parity_feat41_diag.py`: same engine, same market —
  full-240-bar call ⇒ `feat_41=3.0 feat_42=1.0`; 55-bar call ⇒ `0.0 / 0.0`.
  Proves input HISTORY, not math, is the difference.
- `scratch/mlpower_parity_htf_window_diag.py` — depth grid (decisive output):

  ```
  depth | h1_mom | feat41 | feat42
     55 |  0.000 |  0.000 |  0.0
     60 |  0.000 |  0.000 |  0.0
    120 |  6.770 |  3.000 |  0.0
    240 |  6.770 |  3.000 |  1.0
   4000 |  6.770 |  3.000 |  1.0
  ```

  ⇒ HTF features activate at depth ≥ 120 (need ≥2 completed H1 buckets) and
  live sits there for essentially its whole uptime.
- `scratch/mlpower_parity_htf_live_train_callgrid.py` (static caller grid):
  - TRAIN `model_generation/schema_v2.py:74`: `window = all_bars[i-54:i+1]` —
    ALWAYS 55 bars.
  - LIVE `application/live_engine.py:3557`: `aggregator.get_completed_bars()`
    (cap 4000 at `:3554`; standard 900-bar broker resync) and warmup probes
    `:2691 / :3177 / :3265` — same deep history.
  - Engine internals: `features/scalp_features.py:16` `tail_bars =
    completed_bars[-55:]` (base) vs `:231-234` `aggregate_bars(completed_bars,
    15/30/60/240)` (HTF over FULL list).
- `scratch/mlpower_parity_htf_realdepth_probe.py`: live `audit_signals` rows
  are decided at aggregator depths the training builder NEVER sees (train rows
  are always depth-55 ⇒ feat_41 ≡ 0.0 in-distribution-for-train only).

**Conclusion (ROOT_CAUSE: PROVEN):** the HTF feature family (feat_40..43) is
history-depth-dependent while the training contract freezes depth at 55. This
is a caller-contract bug, not an engine-math bug. It also means: (a) live
inference after ~2h uptime consumes never-trained feature values (clipped to
bounds), and (b) every online-retrain record carries the same poison, so the
quality gate sees self-inconsistent data — a plausible contributor to the
17+ fine-tune rejections observed on 2026-09-02.

### 2.5 Live runtime observations (2026-09-03, verified on this machine)

- Champion verify lines (logs/info/2026/09/2026-09-03.log):
  `[MODEL] CHAMPION VERIFIED hash=a4b95406088ed618 model_id=primary_scalp
  version=v1.0` at 18:48 / 18:49 / 19:48 local.
- 20:44:00 local: `ASYNC RETRAIN START buffer_size=300` → 20:44:00.872 the
  BUG-228 honest line (no improvement; baseline kept) → `ASYNC RETRAIN
  SUCCESS`. Consequence: `70d_liquidity/model.pt` mtime changed to 20:44 and
  the canary now reports `(False, 'DIVERGES_AT:input_projection.weight')` —
  it is NO LONGER byte-identical to the fresh init, but **trained-quality is
  NOT proven**: the only retrain since detection was a no-improvement
  keep-baseline window; WHY the checkpoint now diverges at exactly
  `input_projection.weight` is UNEXPLAINED and must be verified before any
  promotion decision (see P0-3).
- Web/API health (port 8099): verdict READY; **serving bundle reported =
  `XAUUSD/v1.0.0/model.pt`, FEATURE_SCHEMA scalp_v1 / 50D (legacy)**;
  MODEL_CONTRACT WARNING `NO_MODEL_METADATA` (bundle metadata incomplete).
- Engine snapshot 17:55Z: `engine_running=false` (engine stopped; only the
  API/UI process was listening). Per the standing rule: engine lifecycle is
  owned by the USER session — do NOT start/stop/restart it without explicit
  user consent.

---

## 3. CURRENT ARTIFACT TRUTH TABLE (canary run 2026-09-03 ~21:05 local)

| Artifact | Canary `detect_untrained_fresh_init` | mtime | Meaning |
| :--- | :--- | :--- | :--- |
| `artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt` (70D) | `(False, DIVERGES_AT:input_projection.weight)` | 09-03 20:44 | No longer byte-fresh. Divergence source unexplained; trained-quality unproven (only no-improvement retrain since). VERIFY before trusting. |
| `artifacts/models/scalp/XAUUSD/v1.0.0/model.pt` (50D) | `(True, BYTE_EQUAL_TO_FRESH_INIT)` | 08-24 06:46 | **CONTAMINATED — and this is the bundle health reports as SERVING.** |
| `artifacts/models/scalp/EURUSD/v1.0.0/model.pt` (50D) | `(True, BYTE_EQUAL_TO_FRESH_INIT)` | 08-21 12:19 | CONTAMINATED. |
| `50d_main` / `70d_news` bundles | diverge (trained) | — | Clean per BUG-225 reproduction probes. |

Scalers: `70d_liquidity/model.scaler.npz` mtime 09-03 06:11 — any retrain must
keep scaler/model coherent (CHG-0046 D6 `scale_like_champion` semantics:
trainer std floor + clip `[-5,5]`).

---

## 4. FUTURE PATH (prioritized; acceptance criteria included)

### P0-1 — Fix the HTF window asymmetry (MLPWR-06-02) IN CODE

Unblocks honest retraining; every further retrain before this fix produces
records with depth-dependent feat_40..43.

- Register a BUG ledger row first (next free number; root cause is PROVEN —
  cite the probes in §2.4). Suggested surface: `features/scalp_features.py`
  (make HTF aggregation depth-deterministic, e.g. aggregate from a FIXED
  trailing window identical to train) OR `model_generation/schema_v2.py`
  (feed the train builder a deeper window matching live). DECISION NEEDED from
  the model owner: parity direction (train→55 like live-short, or
  live→train-depth). Do NOT silently pick one — this changes feature
  semantics (schema hash) and therefore requires:
  - schema-contract impact assessment (`features/schema_contract.py`, hash
    `235b8fccc96b7e0e` changes if names/order change — prefer a fix that does
    NOT change names/order),
  - CHG row in `agents/change_control.md`,
  - a regression pin test in `tests/unit/` (assert feat_41/42 invariance vs
    history depth on a synthetic window),
  - re-run of `scratch/mlpower_parity_corpus_probe.py` → verdict must flip to
    **MATCH** (TOL 1e-12) across all windows/seeds.
- Note the tradeoff explicitly in the CHG: clipping live HTF to a 55-bar
  window makes feat_40..43 ~always neutral (loses signal but restores
  train==live); deepening train windows preserves signal but changes every
  historical dataset row ⇒ full retrain required anyway (P0-2).

### P0-2 — Champion repair (retrain from a CLEAN dataset + governed promotion)

- Canonical path (BUG-225 handoff): research pipeline
  (`three_model.train_variant("70d_liquidity", smoke=True)` family / BUG-141
  recovery recipe) or governed promotion of a validated candidate. A bare
  engine restart does NOT repair anything (fresh weights reload; quality gate
  keeps rejecting fine-tunes against a degenerate baseline).
- Retrain from the CLEAN research dataset — NOT the live rolling buffer
  (buffer labels came from paper fills during the contaminated window;
  partly self-fulfilling rejections).
- Sequencing: do P0-1 first if the chosen fix changes train-side features;
  otherwise retraining now bakes the asymmetry into the new champion.
- Promotion gates: provenance-registry entry (new fingerprint registered,
  CHAMPION row), CHECK-MDL-02 PASS, BUG-166 fingerprint==disk, walk-forward /
  OOS evidence, no auto-promotion (TASK-04 protocol).

### P0-3 — Explain/verify the 20:44 divergence of `70d_liquidity/model.pt`

- The checkpoint diverges from fresh init at exactly
  `input_projection.weight` after a no-improvement keep-baseline window.
  Either (a) a legitimate small update slipped through a path that bypassed
  the gate, or (b) mint nondeterminism (seed handling) — both matter. Probe:
  diff the 31 tensors against the seed-42 init, check the 20:44 checkpoint
  write path in the log, and confirm whether an `accepted=True` write
  occurred. File the answer in the BUG-225 row before P0-2 promotion.

### P0-4 — Replace the residual fresh-init artifacts

- `XAUUSD/v1.0.0/model.pt` (the 50D serving bundle per health) and
  `EURUSD/v1.0.0/model.pt` are still fresh inits. Either retrain or retire
  them; until then the canary (CHECK-MDL-02) stays red on any machine
  carrying them and `MODEL_CONTRACT` health stays WARNING
  (`NO_MODEL_METADATA` — also add bundle metadata while there).

### P1 — Lane hygiene

- Commit the MLPWR probes from `scratch/` with the P0-1 fix commit (they are
  the reproducibility evidence; `scratch/` is ruff-excluded, keep them
  runnable read-only).
- BUG-106 (O(n^2) 70D frame builder) still blocks TASK-04 A/B/C benchmark
  EXECUTION per the taskboard — fix or descope explicitly.
- MLPWR-05 registry/name-level identity stays enforced via
  `schema_contract.canonical_feature_names` + `feature_schema_hash` — any
  feature change must re-pin the hash in the same commit.

### P2 — Guardrails from adjacent programs (do not regress)

- BUG-227 waves: strategy constants still need behavior pins (Wave B: policy
  flip-penalty/memory + throttle windows; Wave C: mslie sweep window + regime
  escalation map). `test_temporal_liquidity_phase20.py` still absent from
  `tests/critical_suite.txt`.
- Keep BUG-183 purge/embargo provenance fields flowing into run configs.

---

## 5. Verification playbook (run before claiming anything)

```bash
cd C:/Users/Capsizer/source/repos/NexusTradingForexBot

# 1. Champion canary (7 tests; #7 is the live-artifact invariant)
.venv/Scripts/python.exe -m pytest tests/unit/test_bug225_untrained_champion_canary.py -q

# 2. Per-artifact fresh-init status
.venv/Scripts/python.exe -c "import sys; sys.path.insert(0,'src'); \
from nexus_scalp.model_lifecycle.integrity import detect_untrained_fresh_init as d; \
print(d('artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt',70)); \
print(d('artifacts/models/scalp/XAUUSD/v1.0.0/model.pt',50)); \
print(d('artifacts/models/scalp/EURUSD/v1.0.0/model.pt',50))"

# 3. MLPWR asymmetry reproduction (before AND after the P0-1 fix)
.venv/Scripts/python.exe scratch/mlpower_parity_htf_window_diag.py
.venv/Scripts/python.exe scratch/mlpower_parity_corpus_probe.py   # verdict must be MATCH after fix

# 4. Live state (port DRIFTS — probe, don't assume 8080/8099)
curl -s -m 5 http://localhost:8099/health | head -c 400
netstat -ano | grep LISTEN | grep -E ":80[0-9][0-9]"

# 5. Trainer honesty lines (BUG-228)
grep -E "no improvement|QUALITY GATE" logs/info/2026/09/2026-09-03.log | tail
```

Pre-push gate: `beforePush.sh` / `beforePush.ps1` via
`.venv/Scripts/python.exe -m ...` (ruff → ruff format → mypy → CRITICAL suite
→ deploy gate). NOTE for CI: while contaminated artifacts exist on a machine,
the critical suite shows exactly 1 red test (`test_real_champion_artifact_
is_trained`) — that red IS the incident signal, not a broken gate.

---

## 6. Guardrails (standing)

- Engine lifecycle (start/stop/restart) belongs to the USER session. Never
  restart to "apply" a model fix; prepare the repair artifact, then ask.
- `scratch/` probes: never delete; commit evidence probes with their fix.
- Ledger discipline: BUG-NNN only for PROVEN defects (this lane's root causes
  are probe-backed); TASK-ID claim in `agents/taskboard.md` before starting;
  `<AGENT>: <imperative>` commit messages, commit every coherent step.
- Parallel-git hazard: re-check `git diff --cached --name-only` immediately
  before every commit (foreign staged files → `git restore --staged`, never
  reset).
- Quality gate before any push; report full commit+push details afterwards.

---

## 7. Doc log

- 2026-09-03 ~21:10 +03:30 — Nexus-Main: initial handoff written. Evidence
  base: BUG-225/228/217/197B/190/185 ledger rows, commit `3f5f9db7`,
  `52615bf7`, `c576dfac`, `6b893f04`, `203f1873`, `11ea316`; MLPWR probe
  outputs re-executed today (all reproduce); artifact canary + health + logs
  re-verified today (§2.5, §3). Prior orchestrator session collected the same
  evidence but was cut before writing this file — this doc supersedes that
  attempt.
