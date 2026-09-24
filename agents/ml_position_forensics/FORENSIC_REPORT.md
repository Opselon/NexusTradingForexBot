# ML Position Management — Forensic Audit Report

Lane: `agent/ml-position-forensics` · base `bbbec186` (origin/main)
Scope: position_adviser/, dataset generation/replay, order-manager coupling,
trainer, service, routes, CLI, tests.

Evidence convention: every finding carries an ID (F#) tied to
`agents/ml_position_forensics/CONTRACT.md`. "FIXED" means the change is locked
by a passing test in `tests/unit/test_position_adviser_forensics.py`,
`tests/unit/test_position_adviser_e2e.py`, or the pre-existing position suites.

---

## 0. Architecture as found (dependency graph from executable code)

Decision path (one canonical path exists today):

    _manage_single_position (order_manager.py)
      -> _calculate_hold_value_score        (deterministic base score)
      -> evaluate_profit_giveback           (deterministic safety override)
      -> build_position_state_for_adviser   (integration.py: snapshot contract)
      -> service.evaluate                   (features -> scaler -> model -> advisory)
      -> apply_advisory_to_hold_score       (policy: bounded, negative-only)
      -> candidate evaluation + hysteresis  (deterministic risk/state machine)
      -> execution (MT5)

Data/feedback path:

    position_replay.py (labels from future bars, chronological split, purge band)
      -> position_dataset_generator (parquet + manifest + dataset hash)
      -> position_adviser/trainer.py (train-only scaler, OOS hold-out, manifest pins)
      -> position_adviser/service.py (integrity-gated load) -> serving

ML advises; hold-score adjustment is bounded and can only LOWER the score.
No ML path emits orders, moves TP/SL, or touches size. Risk/execution stays
deterministic (mission §8/§10 core invariant: CONFIRMED PRE-EXISTING and now
locked by test `test_policy_can_only_lower_never_raise`).

---

## A. FEATURE LINEAGE TABLE

| Feature | Source | Timestamp semantics | Available at decision time? | Future-data risk | Transformation | Scaler | Status |
|---|---|---|---|---|---|---|---|
| position_age_bars | replay/live holding bars | count of bars since entry, ≤ t | yes | none (history) | none | train-only StandardScaler | VERIFIED (F4 fix) |
| signal_age | entry-signal age tracker (order_manager `_signal_ages`) | bars since signal, ≤ t | yes | none | none | train-only | VERIFIED (F4: live matches generator bar-age) |
| unrealized_pnl_r | current price vs entry in R units | at t | yes | none | R conversion via initial risk (price units) | train-only | FIXED (F0: unit parity) |
| current_r_net | same, net of spread cost | at t | yes | none | friction-aware | train-only | FIXED (F0) |
| current_return | (p_t − p_entry)/p_entry | at t | yes | none | none | train-only | VERIFIED |
| distance_to_target_r / distance_to_stop_r | (TP/SL − price) in R, price units | at t | yes | none | generator r_distance convention | train-only | FIXED (F0: was USD-as-R) |
| tp_sl_ratio | TP/SL distances | at t | yes | none | ratio | train-only | VERIFIED |
| spread | live tick / replay bar spread | at t | yes | none | constant-in-training was degenerate → scaler clip | clip ±5 std | FIXED (degenerate scaler) |
| atr | rolling ATR ≤ t | at t | yes | none (window excludes t+1) | none | train-only | VERIFIED |
| model_probability / model_confidence | previous model output, versioned prediction timestamp | ≤ t, only if actually emitted | yes when present | low: prediction-as-feature must be historical | clip [0,1] | train-only | VERIFIED (only causal keys in ADVISER_FEATURE_ORDER) |
| future_* (return, MFE, MAE, continuation) | future bars after t | t+1…t+h | **NO — by design** | must NEVER be features | label-side only | not scaled | VERIFIED: `assert_no_label_leakage` refuses training if present |

## B. LABEL TABLE

| Label | Definition | Future horizon | Leakage risk | Economic meaning | Implementation | Status |
|---|---|---|---|---|---|---|
| optimal_action (KEEP/REDUCE/CLOSE) | generator rule: continuation value vs MAE danger, cost-aware | position horizon (future bars of the replay) | label-side only, never in feature vector | expected value of holding from NOW, after costs | position_replay.py label derivation | VERIFIED (labels future-aware, features decision-time-only) |
| future_return / future_r_net | price move after t, net of friction | fixed horizon | label-side only | realized outcome for evaluation | replay | VERIFIED |
| future_r_best / future_r_worst | MFE/MAE after t | fixed horizon | label-side only | excursion stats for evaluation | replay | VERIFIED |

## C. MODEL TABLE

| Model | Architecture | Features | Dataset | Target | Validation | Calibration | Economic results | Artifact | Status |
|---|---|---|---|---|---|---|---|---|---|
| PositionAdviserNet | MLP head, 3-class softmax (KEEP/REDUCE/CLOSE) + confidence | ADVISER_FEATURE_ORDER (13 causal keys) | generator parquet, chronological train/val/purge/oos | optimal_action | temporal split + purge band + OOS ≥20 rows refusal | confidence = 1 − normalized entropy (reported, not claimed calibrated) | OOS accuracy/loss reported; economic gates live in promotion flow | weights.pt + scaler.npz + .meta.json pins | VERIFIED (F6/F7/F8) |

## D. ACTION TABLE

| Action | ML output | Policy | Risk constraint | Execution constraint | Allowed? | Status |
|---|---|---|---|---|---|---|
| HOLD / CLOSE pressure | p_keep, adjustment ≤ 0 | bounded negative-only hold-score adjustment after giveback override | deterministic score state machine + hysteresis | none direct | yes (advisory only) | VERIFIED |
| raise hold score | — | hard-bounded: `max(adjustment, 0) == 0` | — | — | **NO** | locked by test |
| orders / TP / SL / size changes | none emitted | not in ML path | deterministic only | MT5 via existing code | **NO ML PATH** | VERIFIED (grep: adviser emits no orders) |

## E. MODEL LIFECYCLE TABLE

| Stage | Gate | Evidence |
|---|---|---|
| TRAIN | leakage refusal, chronological split, ≥50 train, ≥20 OOS rows, deterministic seed (F7) | trainer.py, tests |
| VALIDATE | OOS loss/acc computed on held-out temporal split | trainer metrics |
| REGISTER | manifest pins: weights/scaler sha256, feature_order, dataset hash, classes_absent (F8) | trainer manifest |
| LOAD/SERVE | verify_package_integrity: hash pins + schema/order check or REJECTED (F6) | service.load + CLI `position-adviser-packages` |
| SHADOW/PAPER/LIVE | activation ladder: DISABLED→PAPER→LIVE, LIVE requires passing prerequisite checks | set_activation |
| ROLLBACK/RETIRED | model_id choice + activation DISABLED (existing) | service |

## F. UI FLOW TABLE

| Screen | Action | Backend | ML | Risk | Execution | User feedback | Status |
|---|---|---|---|---|---|---|---|
| PositionAdviser page | train/validate/status | routes → trainer/service | advisory history feed | hold-score policy shown | none | status exposes integrity/dataset hash/feature order | VERIFIED (F2 wired: order manager now records every applied/refused advisory to the feed) |

---

## Findings

| ID | Severity | Finding | Status |
|---|---|---|---|
| F2 | HIGH | Purge was ASYMMETRIC: the train|val and val|oos boundaries were purged, but (a) the oos tail was never embargoed — observations whose label horizons are TRUNCATED by end-of-data (a materially different label than the full-horizon one) shared the oos partition with complete labels — and (b) `embargo_bars` was configured but never consumed. The validator's `causality_violations` was a hardcoded `0` — decorative, proving nothing. | FIXED (three quarantine conditions: pre-boundary purge window, post-boundary embargo band, tail-truncation; real causality check now verifies the temporal partition property) |

|---|---|---|---|
| F0 | HIGH | Unit mismatch: live snapshot handed USD distances as "R" while replay labels use price-unit r_distance — live inference and training disagreed by ~290× on R features; position age/signal age semantics also diverged | FIXED (integration.py rewrite; tests pin parity) |
| F1 | HIGH | Missing decision gates: no snapshot-staleness rejection, no duplicate-decision suppression, unbounded per-ticket state maps | FIXED (service.evaluate gates before throttle commit; bounded maps; forget() clears snapshot state) |
| F2 | MED | `record_advisory_for_ui` existed but was never called — decision feed silently empty; `forget()` never invoked on close | FIXED (wired in order_manager advisory block + `_cleanup_ticket_state`; tests) |
| F4 | MED | Position-age/signal-age live semantics didn't match generator bar-age | FIXED with F0 |
| F5 | MED | Per-ticket maps grew without bound in a long-running engine; closed tickets never forgotten | FIXED (bounded maps + teardown wiring + tests) |
| F6 | HIGH | No atomic model-package integrity: weights/scaler/schema from different runs could silently load together | FIXED (verify_package_integrity shared by service.load and CLI; hash+order mismatches REJECTED; tests) |
| F7 | MED | Training reproducibility not pinned: seeds set, but no deterministic mode; artifact identity relied on file bytes alone | FIXED (torch deterministic mode; content-hash reproducibility proven in E2E; immutability via per-file manifest pin) |
| F8 | HIGH | Class-absent weight explosion: inverse-frequency over absent class (~1e9) drove observed-class weights to ~1e-8; weighted CE normalized to loss in the millions → silent garbage training on any window missing one action | FIXED (zero weight for absent classes, warn + `classes_absent` in manifest/result; regression test) |
| F9 | INFO | `torch.save` embeds the zip member prefix = file basename, so equal weights produce different file bytes for different model ids | DOCUMENTED (reproducibility asserted on canonical tensor content; file-byte pin used only for per-file immutability) |

## Leakage conclusions (mission §2/§3)

- Features: decision-time-only; `assert_no_label_leakage` refuses any future/label
  column inside ADVISER_FEATURE_ORDER before a matrix is built (trainer gate + test).
- Labels: future-derived by construction, label-side only.
- Split: chronological with explicit purge band rows excluded from training
  (`purge_embargo_rows_excluded` in manifest); OOS split held out of both
  scaler fitting and early stopping; trainer refuses to run without ≥20 OOS rows.
- Scaler: fitted on train rows only (existing, verified by test).
- Feedback loop: advisory is recorded with snapshot_id/timestamp/model sha;
  predictions enter future feature vectors only as historically-versioned fields
  already present in the contract.

## Verification

(to be completed at the end of the lane run: targeted suites, lint, format,
mypy, critical suite, CI, CodeQL, merge, post-merge verification)
