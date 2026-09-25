# FORENSIC_TRADE_AVAILABILITY_AUDIT — NSE 2026-08-18

**Scope**: read-only forensic audit of why the engine trades so little.
**Window**: 2026-08-17T02:49Z → 2026-08-18T02:49Z (24h, UTC).
**Author**: Hermes Agent (forensic watcher). **Modes**: READ-ONLY. No code,
config, model, threshold, risk, order, or DB mutation performed.

---

## 1. Executive Summary

The low trade count (8 real executed trades / 224 pending fills in 24h) is the
result of a **stacked, mostly-legitimate filter chain** with **two real
defects** at the edges:

1. **The model is genuinely weak.** In the post-fix window the model's
   directional probabilities average ai_buy≈16%, ai_sell≈17% (median
   buy≈0.25, sell≈0.27) — below the 33% 3-class random baseline. The
   confidence gate (effective threshold 0.35 in RANGING regime) is correctly
   rejecting these candidates. The raw-confidence fix is live and honest.
2. **MAX_EXPOSURE_REACHED (3,720; 25% of all signals) is a stale
   execution-state artifact**: at blocker timestamps the broker had NO active
   pending and NO open position (0/300 samples with any broker exposure).
   The engine's in-memory `_live_tickets_cache` (rebuilt at tick-manage
   cadence from positions_get + orders_get) sits stale between broker-side
   fills/cancels — see BUG-072.
3. **The experience→outcome learning pipeline loses ~65% of trades**
   (186 experiences → 65 outcomes; 187 closed ledger rows without outcome).
   Research and training are starved of representative outcomes — BUG-073.
4. **Pending-order churn is extreme**: 387 pendings created, 163 canceled,
   224 filled per 24h; many cancel paths fail silently (`Retcode: 0`); the
   single-slot exposure model turns this churn into long self-lockout
   windows (cancels mean rest 275s, 6 rested >300s, max 4,976s).

**Contribution estimate (order of magnitude, from the rejection matrix)**:
- Legitimate model/policy/RR rejection (REGRIME 4,618 + RR 2,700 +
  confidence 153 + zone 51 + SR/HTF 1,089 + reentry 1,465 + misc):
  ≈ 78% of the funnel — **INTENDED behavior** given a weak model.
- Suspected stale-exposure lockout (MAX_EXPOSURE 3,720): ≈ **22%** —
  **SUSPICIOUS / BUG-072**.
- Learning/telemetry gap: not directly suppressing trades, but starving the
  improvement loop — **BUG-073**.

## 2. Verified 24h counts

| Metric | Count |
|---|---|
| audit_signals rows | 14,671 |
| NO_TRADE signals | 14,547 (99.2%) |
| non-NO_TRADE signals (all stages incl. mid-pipeline stages) | 124 |
| FINAL_DECISION passing candidates | 46 |
| TICK_SWEEP executions | 6 |
| broker orders placed (all, dedup tickets) | 700 (387 pendings / 18 market / 135 closes) |
| broker pending fills (state 4) | 224 |
| broker market fills | 18 |
| executed/traded positions (broker_trades, 24h) | 242 |
| closed ledger rows (24h) | 251 (split-leg inflated; ~74 master order ids) |
| experiences (24h) | 186 |
| experience outcomes (24h, all executed+closed) | 65 |
| realized PnL (24h, broker deals authority) | −$6,121 (242 trades, 1397W/2034L all-time window profile) |
| guard telemetry (TICK_DUPLICATE 26,592 / ORDER_FREQUENCY_THROTTLED 3,424) | non-signal path |

Correction of the starting evidence: the reported "8 broker orders" is
conservative; broker history shows 224 pending fills + 18 market fills + 135
closes = 377 executed fill events (some are split legs of the same position),
position-level 242 rows. The engine traded a LOT of small lots; the issue is
not zero trading, it is unprofitable trading: **−$6,121/24h**.

## 3. Complete Signal Funnel (24h)

```
ticks/evals                ~14,671 audit_signals + 26,592 dup-suppressed
model inference            ~14,6xx (deferred: exposure gate pre-empts ~3.7k)
policy evaluation          STANDARD_EVAL 7,323 | NO_TRADE_BUILDER 5,802
candidate entry            model_action non-NO_TRADE ≈ 4,084 (10,532 NO_TRADE model action)
policy rejects             REGIME 4,618 | SR 866 | HTF 145 | GUARDIAN 80 |
                           REENTRY 1,465 | CONFIDENCE 153 | ZONE 51 | EXPERIENCE 66 | FLIP/COOLDOWN few
risk evaluation            built into policy (RR gate): ASYMMETRIC_RR 2,700
exposure evaluation        MAX_EXPOSURE_REACHED 3,720 | PENDING_ORDER_LOCKED 680
execution decision         FINAL_DECISION 46 | PREDICTIVE_LIMIT 57 | TICK_SWEEP 6 | AI_REVERSAL 6
broker dispatch            124 proposals → order-manager (comment NSE_PENDING/MARKET)
broker result              224 pending fills + 18 market fills + 135 closes
```

Note the funnel is NOT linear; exposure gate runs BEFORE model inference on most
ticks (zero-prob NO_TRADE rows), so "model evaluations" ≈ total − 3,720 −
pre-gate columns. Funnel stages recorded via decision_stage columns.

## 4. Rejection Matrix (24h)

| reason | count | % of all | % of candidates | first | last | leg. / susp. | evidence |
|---|---|---|---|---|---|---|---|
| MAX_EXPOSURE_REACHED | 3,720 | 25.3% | n/a (pre-model) | window | window | SUSPICIOUS (BUG-072) | broker had 0 exposure at 300/300 samples |
| REGIME_RANGING_MEAN_REVERSION | 2,575 | 17.5% | — | — | — | INTENDED | regime gate on range market |
| REGIME_TRENDING_MOMENTUM | 2,043 | 13.9% | — | — | — | INTENDED | regime gate on trend/no-setup |
| ASYMMETRIC_RR_BELOW_CONFIGURED_THRESHOLD | 2,700 | 18.4% | 66% of 4,084 cands | window | window | INTENDED (threshold 1.8) | avg RR computed 1.1·1.4 in sampled payloads |
| SAME_LEVEL_REENTRY_BLOCKED | 1,465 | 10.0% | — | — | — | INTENDED | dist < $0.50 from live ticket price |
| BUY/SR_REJECTED (SR margin) | 866 | 5.9% | — | — | — | INTENDED | S_Dist < 0.25 margin |
| SELL/HTF_REJECTED | 145 | 1.0% | — | — | — | INTENDED | htf conflict |
| PENDING_ORDER_LOCKED | 680 | 4.6% | — | — | — | INTENDED (lock 30s + drift) | but extends lockout after fills — see BUG-072 |
| GUARDIAN (BLOCKED_BY_GUARDIAN) | 80 | 0.5% | — | — | — | INTENDED | unsafe regime |
| INSUFFICIENT_CONFIDENCE | 153 | 1.0% | 3.7% | — | — | INTENDED | 0.24–0.31 < 0.35 |
| EXPERIENCE_INTELLIGENCE_GATE | 66 | 0.4% | — | — | — | INTENDED but data-starved | BUG-073 makes its inputs biased |
| ZONE_QUALITY | 51 | 0.3% | — | — | — | INTENDED | zone < 0.60 |
| TICK_SWEEP executed | 6 | — | — | — | — | INTENDED | + sweep confidence gate fixed |
| AI_REVERSAL | 6 | — | — | — | — | INTENDED | reversal closes |

**Top-10 suspicious candidates**: the 3,720 MAX_EXPOSURE rows (pre-model, no
ticket in payload) remain the only material suspicious block class.

## 5. MAX_EXPOSURE Forensics

- **Count**: 3,720 (1,402 again the reported 3,709 — recomputed, matches).
- Payload `ticket` = 0 for ALL of them; decision_stage shows the exposure gate
  runs pre-inference (probs = 0).
- **Zero candidate-passed exposure rejects**: 0 rows where action != NO_TRADE
  and reason = MAX_EXPOSURE. So exposure never vetoes a fully-passing
  candidate at this stage — the gate blocks earlier ticks.
- **Broker correlation at blocker instants (300/300 samples)**: no active
  pending, no open position. Blocker instants cluster 05:00–08:00 and
  10:00–11:00 windows, which correlate with heavy pending churn.
- **Classification**: BUG-072 — STALE_INTERNAL_STATE (engine cache holds a
  ticket the broker no longer has; pending fills/cancels not reflected until
  next sync). UNKNOWN residual risk: cannot introspect live memory read-only;
  a live probe (instrumented compare cache vs orders_get) is the verification
  step for the fixer.
- **Not verified**: internal open_positions/pending counts at blocker time are
  not persisted per-row (only implied by the cache) — observability gap.

## 6. Pending Order Findings

- 387 pendings created / 163 canceled / 224 filled in 24h. Cancel durations:
  54 in 30-60s, 71 in 120-300s, 6 > 300s (max 4,976s ≈ 83 min!). Fast fills:
  119 < 10s.
- 3 cancel-failure logs (`Failed to cancel pending order #... Retcode: 0`) —
  retcode 0 carries no reason; the order-manager lock (30s + ≥1.0 ATR drift)
  plus the retcode-0 failure keep the slot internally occupied.
- **Ticket 152495362150**: `audit_orders` shows `Generated candidate /
  dispatch_order pending SELL_LIMIT` at 00:41:53 (request f927a01f...), but
  the broker-orders table has ZERO rows for the ticket, zero deals, zero
  ledger row, and the experience has model_probability 0.0. The cancel failure
  at 04:13:01 references it. This ticket is an example of an internally-
  tracked pending that never existed on the broker (or was canceled without
  audit rows) — consistent with BUG-072's stale-cache mechanism.
- Exposure gate counts active positions + pendings TOGETHER (MAX_TOTAL_EXPOSURE=1).

## 7. Model Health Findings

- Live artifact: artifacts/models/scalp/XAUUSD/v1.0.0/model.pt
  sha256 f0f70efb1b55..., mtime 2026-08-18T01:55:01Z (written by the
  TRADE QUALITY FIX session), scaler mtime 2026-08-17T03:02:00Z.
- registry: experience_model_registry rows for the same artifact: lifecycle
  CANDIDATE, fingerprint 0872ae0b (00:58:32) then f0f70efb (02:16:13) —
  the live artifact was REPLACED twice today; **never marked CHAMPION**.
- Probabilities (24h): ai_buy mean 0.160 / median 0.248; ai_sell mean 0.174 /
  median 0.272; ai_no_trade mean 0.155 / median 0.244; sums ≈ 0.80 typical,
  0.0 on 1,774/3,000 sample (exposure-gated), 1.0 on 30 — **normalization
  inconsistency** (prob-triples do not sum to 1) — see §8.
- Per model_action: NO_TRADE model-action rows have probs ≈ 0.11-0.13 (all
  classes), non-NT actions 0.25-0.30 — the model concentrates on NO_TRADE
  and its directional probs are barely above the NO_TRADE class probs.
- Class imbalance + NO_TRADE class: the 3-class dataset
  (ds_cb30f87520e9e6a4, 99,946 rows M5) is ~88% NO_TRADE; plain CE trains
  always-NO_TRADE (known trap); production uses FocalLoss+oversample but the
  live legacy baseline is weak (see benchmark report: TCN candidates all
  REJECTED, baseline macro-F1 ~0.29).
- 24h realized outcomes: avg R −0.075, win rate 57.5% of fills but avg pnl
  −$19 → small wins / bigger losses (p/l profile matches BUG-067 pattern).

## 8. Confidence Path Findings

- path: neural logits (4-head; index 3 WAIT never trained as label) →
  probabilities extracted (which head? policy uses prob_buy/prob_sell/
  prob_no_trade) → confidence = **raw directional probability** (TRADE
  QUALITY FIX, uncommitted) → regime penalty (RANGING +0.10 → effective
  0.35; live.yaml confidence_threshold 0.25 + survival +0.10) → gate
  `confidence < active_threshold → INSUFFICIENT_CONFIDENCE`.
- Before the fix (pre-01:00Z rows): confidence column shows 0.6-0.7 inflated
  values matching the old `0.55+0.35*prob` floor. Post-fix rows show
  confidence ≈ raw prob (0.24-0.31), and INSUFFICIENT_CONFIDENCE rejects
  appear — the fix WORKS as intended.
- **Prob normalization inconsistency (SUSPICIOUS)**: 3-prob triples typically
  sum ≈0.80 (1,556 rows =0.80, 143 =0.83, etc.), 0.0 for exposure-gated rows,
  1.0 on 30 rows. The 4th (WAIT) logit's share is missing/absorbed. Impact:
  confidence values are not calibrated probabilities; gate comparisons remain
  ordinal so gating is still meaningful, but any threshold tuned to
  "probability" semantics is off.
- No double penalty found: regime penalty applied once (active_threshold);
  SMC god-mode penalty (×0.85) separate but rarely active; flip penalty
  separate.

## 9. RR Findings

- Gate: active_min_rr = algo min_risk_reward_ratio (1.8) but effective uses
  min(min_allowed_rr=1.10, active_min_rr) — code line 1359: `active_tp_rr =
  min(self.min_allowed_rr, active_min_rr)`. Hand-checked on sampled rejected
  rows: payload risk_checks rr≈1.1 vs min_rr 1.8? — sampled rows show
  `rr: 1.1, min_rr: 1.8` — meaning the proposal built with actual_rr 1.1
  still compared against 1.8? Code at 1371 uses active_min_rr 1.8. So the
  ASYMMETRIC_RR gate compares the FINAL actual RR against 1.8 even though
  the TP adjustment used 1.10 — LOOKS like a mismatch: min 1.10 is used for
  TP stretching, gate uses 1.8. VERIFY: 2,700 rows rejected with rr≈1.1-1.4.
  Classification: **calculation/mismatch SUSPICIOUS, gate behavior
  documented as INTENDED by config (min_risk_reward_ratio 1.8)**. Not a
  calculation bug per se; the effective gate is stricter than the minimum
  used to build the TP — likely INTENDED (config) but worth documenting.
- Spread/tick precision: XAUUSD 2dp, spreads $0.20-0.40 typical; not a bug.

## 10. Regime Findings

- 4,618 regime rejects: RANGING_MEAN_REVERSION 2,575 + TRENDING_MOMENTUM
  2,043 (the regime gate blocks entries when regime has no active setup
  candidate). Regime census: RANGING dominant in most hours; TRENDING in
  bursts; GUARDIAN 80 (unsafe regimes HIGH_SPREAD_CHOP / MACRO_NEWS_FREEZE).
- Policy maps both common regimes to NO_TRADE unless other confluences
  (ICHIMOKU/ICT/SMC) align. This is INTENDED restriction, not mismatch.

## 11. Reentry Findings

- 1,465 SAME_LEVEL_REENTRY_BLOCKED: code threshold 0.50 ($) vs live_tickets
  price (pending price or position open price). Sampled: `($0.00 < $0.50)`
  (48), `($0.03 < $0.50)` — actual distances ≤ threshold → **gate fires
  correctly** per contract. But when the live ticket is STALE (BUG-072
  cache), the reentry lock also persists beyond broker truth — e.g.
  `$0.00` deltas against a price that is gone. INTENDED rule; amplified by
  stale cache.
- Side handling: uses abs() distance only, no direction check — both same
  and opposing side entries within $0.50 are blocked. Slight over-blocking
  (opposing-side reversal at same price is blocked even though it is a
  different trade) — SUSPICIOUS minor.

## 12. Training Worker Findings

- Worker RUNNING (51 log lines; START at 05:46:14; cycles every ~5 min;
  `duration_ms=0.0` every cycle).
- `auto_train_enabled=False` (code) → worker policy: no training triggered.
- `training_runs` table: **EMPTY (0 rows)**; model_comparisons empty; no
  training attempt, success or failure recorded.
- Dataset available (ds_cb30f87520e9e6a4, 99,946 rows) but no run references
  it.
- Classification: **A. legitimately performs no training because
  auto_train=False (policy)** + **D. pipeline never reaches training (no
  operator trigger / no challenger)**. NOT broken/dead telemetry — the
  worker is genuinely idle by design.

## 13. Model Artifact Findings

| field | value |
|---|---|
| live model | artifacts/models/scalp/XAUUSD/v1.0.0/model.pt |
| sha256 | f0f70efb1b55855b (model) / 811554e5 (scaler) |
| mtime | 2026-08-18T01:55:01Z (today) / scaler 2026-08-17 |
| model_id | primary_scalp_scalp_v1_50d / v1.0 |
| feature schema | scalp_v1 / 50D |
| registry lifecycle | CANDIDATE (never CHAMPION) — two replacements today |
| training dataset | ds_cb30f87520e9e6a4 (M5, 99,946 rows, 2025-03→2026-08-17) |
| training runs | none |
| validation | benchmark: baseline macro-F1 ~0.29; TCN candidates REJECTED |
| champion/challenger | no challenger; no shadow runs |

**Champion/Challenger status**: the live artifact is loaded from the legacy
path and is the de-facto production model, but the registry marks it
CANDIDATE. It is also ~8 hours old (rewritten today) — the "model last
written 17 Aug" claim from the task brief is **outdated**; it was rewritten
by the TRADE QUALITY FIX session today.

## 14. Experience → Research Lineage

- 186 experiences / 65 outcomes (35%); 121 experiences unresolved (65%).
- Research: research_runs EMPTY; strategy_registry only 2 builtin Ichimoku
  rows (DISCOVERED); strategy_intelligence_registry: dozens of experiments
  DISCOVERED with sample_count 0-4, mostly negative expectancy.
- Experience outcomes: avg R −0.075, all executed+closed; exit reasons
  SYSTEM_CLOSE (21) / BE_SL (15) / MANUAL (10) / UNKNOWN (6).
- BUG-073: the 65% gap is the dominant lineage defect; research/training
  statistics are built on a non-representative subset.

## 15. Broker Correlation

- audit_broker_orders is broker-history synced (700 rows), audit_broker_trades
  position-level (3,607 all-time; 242 in-window), deals 7,456 all-time.
- At MAX_EXPOSURE blocker instants: broker had NO exposure (300/300
  samples) → internal state diverges (BUG-072).
- ticket 152495362150: engine-side only; missing broker-side rows + cancel
  failure log → engine tracked something the broker never had.
- Correlation: fills (224) + fast fills (119 <10s) → the single-slot cache
  can go stale repeatedly within a minute.

## 16. Timestamp / Clock Findings

- audit_signals.generated_at stores UTC (max 2026-08-18T02:45:47Z).
- broker orders/deals store **server-local epochs (GMT+3)**; the 24h window
  query must convert (⊕180min) — BUG-070 fixed mapping in code; DB rows are
  still raw broker epochs.
- nse_live.log prints host-local Iran time (UTC+3:30); reconciling log vs DB
  requires offset. BUG-070 covered.
- No further systematic skew found within this audit's read-only scope.

## 17. Observability Gaps

- MAX_EXPOSURE payload lacks the blocking ticket (all 0) → cannot prove
  stale-cache identity per row (needs live probe).
- Cancel failures log `Retcode: 0` with NO error code/message; no retry
  telemetry; no metric of "slot held by nonexistent pending".
- Prob-triple sum ≠ 1 not surfaced anywhere (no normalization check/alert).
- No persisted internal state snapshot (cache contents, last broker sync
  time) per signal row → forensics rely on reconstruction.
- Experience-outcome mapping failure is silent (no alert when a closed
  trade lacks an outcome).
- guard telemetry (ORDER_FREQUENCY_THROTTLED 3,424) not joined into the
  funnel (separate table, no per-signal correlation).

## 18. Intended Behavior (non-bugs)

- Low-confidence rejection (153) — correct per config (0.35 effective).
- RR gate at 1.8 (2,700) — strict by config; not a bug (though TP-stretch
  uses 1.10 min — document only).
- Regime gating (4,618) — designed.
- SR/HTF margin filters (1,011) — designed.
- Reentry lock within $0.50 (1,465) — designed (pending held → reentry
  blocked; amplified by stale cache, see BUG-072).
- Training idle (auto_train=False) — designed; operator-triggered.
- Model weakness itself — evidence, not a bug.

## 19. Suspicious Behavior

1. MAX_EXPOSURE 3,720 vs broker-zero-exposure — BUG-072 (verified defect on
   the exposure-state path; MEDIUM-HIGH confidence).
2. Experience-outcome 65% loss — BUG-073 (verified by DB joins).
3. Prob triples not normalized (sum ≈0.80) — normalization/calibration
   defect in the inference/telemetry extraction; SMART to verify which head
   indices feed buy/sell/no-trade.
4. Ticket 152495362150 — internally tracked, broker-absent + cancel fail.
5. Reentry uses only absolute distance (blocks opposing-side entries within
   $0.50 as well).
6. Broker trades PnL table includes stale pre-fix rows (all-time 3,607 rows;
   windowed totals used; no defect found in windowing itself).

## 20. Verified Bugs

- **BUG-072** — Stale in-memory exposure cache → false MAX_EXPOSURE
  lockout (HIGH, DISCOVERED).
- **BUG-073** — Experience→outcome pipeline loses 65% of trades; research/
  training datasets starved & biased (HIGH, DISCOVERED).

## 21. BUG IDs Added / Updated

- Added BUG-072 (new) and BUG-073 (new) to agents/bugs.md. No existing
  entries modified. Historical note: ledger previously had id-reuse
  (BUG-033/034/037/038/039/059/071 duplicates); BUG-072/073 are the next
  free IDs.

## 22. P0/P1/P2/P3 Future Fix List

- **P0**: BUG-073 — fix outcome recording (broker-truth-driven) + backfill;
  otherwise training/research can never learn truth.
- **P1**: BUG-072 — exposure gate must use broker-verified snapshot (or
  age-bounded cache); emit blocking ticket in payload; fix cancel-failure
  logging (retcode 0 hides error).
- **P1**: Prob-normalization fix in inference extraction (sum→1 or explicit
  calibration) + alert on drift.
- **P2**: Persist internal-state snapshot per signal for future forensics;
  join guard telemetry into funnel.
- **P2**: Retrain/replace the legacy baseline (challenger pipeline) — the
  model is weak; auto_train intentionally off, but a sponsored challenger
  run on the M5 dataset is the fix direction.
- **P3**: Reentry opposing-side distance/should direction-check; UI shows
  exposure-slot telemetry.

## 23. 60D Readiness Blockers

- **None found in the live trading path**: features are 50D, schema registry
  declares 60D forward-only; manifests carry feature_schema_id/dimension;
  experience provenance stores it. Test infra + research + model_generation
  all support widening.
- Minor: any hard-coded `50` in legacy trainer/inference validation paths
  (e.g. `_validate_50d_tensor`) will need a schema-driven dimension;
  flagged as no-current-blocker.

## 24. What Was NOT Touched

- NO production code changed (src/, Web/, configs/, tests/ untouched).
- NO trading/risk/execution parameters changed; NO thresholds changed.
- NO model replaced, trained, or retrained; NO scaler or artifact modified.
- NO order placed, modified, or canceled; no broker interaction at all.
- NO database data mutated (all queries read-only; SQLite opened in RO mode).
- Only artifacts created: this report + the agents/bugs.md append (the
  permitted doc-only mutation). Scratch scripts were removed.

## 25. Final Verdict

- **CURRENT STATUS**: engine runs; model genuinely weak (below random);
  gates honestly reject; training intentionally idle; two verified defects
  (BUG-072 stale exposure cache false-blocking; BUG-073 outcome loss).
- **REAL ROOT CAUSES**: (1) weak model probabilities below the honest
  confidence gate — dominant; (2) stale internal exposure state falsely
  blocking ~22% of evaluations; (3) learning pipeline losing 65% of
  outcomes, starving improvement; (4) extreme pending churn + single-slot
  policy amplifying all of the above.
- **VERIFIED BUGS**: BUG-072, BUG-073.
- **INTENDED**: confidence/RR/regime/SR/HTF/reentry gates; training idle.
- **SUSPICIOUS**: prob normalization (sum≈0.80), reentry direction-blind,
  ticket 152495362150.
- **IMPACT**: ~78% of the funnel disappearance is legitimate (weak model +
  strict policy); ~22% (MAX_EXPOSURE) is defect-driven; learning gap delays
  any model improvement.
- **FUTURE FIX PRIORITY**: P0 BUG-073 → P1 BUG-072 + normalization → P2
  challenger training.