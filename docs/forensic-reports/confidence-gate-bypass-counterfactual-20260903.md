# Counterfactual Deep-Dive: 11 Trades Executed Below the Confidence Gate

**Verdict: STRUCTURAL-CONFIRMATION BY DESIGN, but with an evidence-recording defect (explainability gap), not a gate-ordering bug.**
Phase-2 counterfactual analysis · read-only · 2026-09-03 · worktree `hermes-subagent/subagent-sa-0-cb5be7dd`

---

## 1. The question

3341 audited signals (2026-08-25 → 09-02) produced 50 actionable proposals, yet 11
executed trades carried `confidence < 0.20` (6 SELL_LIMIT + 5 BUY_LIMIT) while the
documented gate rejects `INSUFFICIENT_CONFIDENCE` below the effective threshold
(base 0.35 in live config, 0.20 code default, +0.10 RANGING penalty). How did
sub-threshold trades reach the broker?

## 2. What the confidence gate actually covers

The gate at `src/nexus_scalp/signals/policy.py:974-988`
(`decision_stage="CONFIDENCE_GATE"`, `blocked_by="CONFIDENCE_FAIL"`, reason
`INSUFFICIENT_CONFIDENCE: Model Confidence (x.xx) < Effective Threshold (x.xx)
[Base: …, Range Penalty: …, Survival Mode: …]`) sits **inside the STANDARD decision
flow only** — it is evaluated *after* two early-return paths have already returned:

| # | Path | Call site | Gate it applies | Returns before gate? |
|---|------|-----------|-----------------|----------------------|
| 1 | Tick sweep | `policy.py:619-642` → `_evaluate_tick_sweep` | **Model confidence floor**, stricter semantics: `sweep_conf_thresh = confidence_threshold + range_penalty_if_range` (`policy.py:1248-1252`), plus 4 structural conditions (pierce, reversal, OFI flip, velocity > 5) | Yes — early return at `policy.py:640-642` |
| 2 | Predictive OB limit | `policy.py:644-662` → `_evaluate_predictive_limit` | **NO confidence gate of any kind.** Entry condition is `valid_ob and not smc_god_mode_active and total_exposure < MAX_TOTAL_EXPOSURE` (`policy.py:1331`) — a pure structural predicate (order_block_type ≠ 0, `policy.py:560`) | Yes — early return at `policy.py:660-662` |
| 3 | Standard flow | `policy.py:664+` | Full confidence gate (+ zone-quality, flip, RR, re-entry, rule-matrix, guardian) | n/a |

The standard-flow gate is real and healthy: 29/50 actionable signals were killed by
`INSUFFICIENT_CONFIDENCE` variants in the reconstructed funnel.

## 3. Attribution of the 11 sub-0.20 executions (audit.db, read-only copy)

Cross-checked `audit_signals` → `audit_orders` (`reason='dispatch_order pending …'
| exec=EXEC-…`, `execution_mode='PREDICTIVE_LIMIT'`) → `audit_broker_trades` by
ticket:

- **11/11 originated from `_evaluate_predictive_limit`**
  (`decision_stage='PREDICTIVE_LIMIT_GENERATION'`,
  `reason_code='PREDICTIVE_OB_{BUY,SELL}_LIMIT_EQUILIBRIUM'`,
  `execution_mode='PREDICTIVE_LIMIT'`). Zero from tick-sweep, zero from standard.
- **7/11 provably filled at the broker** (audit_broker_trades rows):
  152553849882 (+0.10), 152553869562 (+3.90), 152553909269 (−24.99),
  152561514145 (+0.32), 152569444046 (+8.50), 152569673850 (−48.18),
  152569686362 (+7.00) → net **−$52.85**. The other 4 were paper-mode tickets
  (100004/100009/100010/152569691786-era) with no broker-deal rows (paper adapter
  after 2026-09-01T19:55, the last broker sync).
- The 29 trades in the 0.20–0.35 bucket are the **same path** — every one of the 47
  actionable PREDICTIVE_LIMIT signals has conf < 0.35, 10 at exactly 0.0.
  Full-fill attribution (since 08-27, 20 real fills): 7 at <0.20, 13 at 0.20–0.35,
  **0 at ≥0.35**. The STANDARD path never executed below its gate.
- The predictive payload is doubly uninformative: `ai_buy/sell/no_trade_probability
  = 0.0`, `risk_checks` absent, `model_action` = the limit action — i.e. the model
  likely produced NO_TRADE (conf 0.0 rows) and the structural OB predicate fired
  anyway. `risk_allowed=false` is stamped on these rows, confirming they bypassed
  the standard gating chain by design of the early return.

## 4. Verdict — intended structural confirmation, not a bypass bug

Evidence:

1. **Origin & intent.** The path was introduced wholesale in
   `bd740300` ("CRITICAL EXECUTION ARCHITECTURE UPGRADE … event-driven, predictive
   SMC execution engine", Part 2: *"We don't wait for candle close; place limit
   order immediately on OB validation"*). The design is explicit: order placement
   is conditioned on order-block structure (valid OB, 50% equilibrium entry, SL
   beyond OB wick + ATR buffer, TP forced to ≥ `min_risk_reward_ratio` 1.8 at
   `policy.py:1366-1379`) — **structure replaces model confidence**, the SMC
   doctrine the engine was re-architected around.
2. **The tick-sweep path shows the team knows how to pin a confidence contract
   onto a structural path** — and did (test_policy.py:375
   `test_tick_sweep_requires_model_confidence`, written after the conf-0.00 sweep
   losses of −$189/−$190 on 2026-08-18; comment at policy.py:392-400 documents the
   same lesson for the synthetic-confidence floor). No equivalent pin exists for
   the predictive-limit path — its only test
   (`test_execution_architecture.py:152 test_predictive_limit_orders`) explicitly
   *lowers* `confidence_threshold` to 0.10 to make the proposal fire and never
   asserts a floor.
3. **Consistency.** The 0-conf predictive rows are not a regression of a formerly
   gated path; the path has never had a model-confidence gate. The downstream
   stack (experience intelligence gate, freshness gate, risk sizing, exposure
   clamps) still runs on these proposals and has independently strangled the
   family (EXPERIENCE_INTELLIGENCE_GATE DEGRADED rejects, agents/bugs.md:3130) —
   i.e. the system already treats predictive-limit quality as an evidence problem,
   not a gate-ordering accident.

**However**, the *documented contract* (docs/architecture/data-flow.md:20
"confidence gate 0.35"; docs/architecture/execution-pipeline.md:17 "policy/
confluence checks (SMC matrix, Regime Guardian, confidence gate)"; agents/skill.md:51)
describes ONE pipeline without disclosing that two of its three execution modes
return before the gate. The executed-below-gate outcome is therefore *intended
mechanically* but **undisclosed and unmeasurable** in the audit trail — an
explainability gap, not an execution bug.

## 5. Explainability gap (deliverable for the INTENDED branch)

`risk_checks` (CHG-0043 decision-evidence dict, `policy.py:684-700`) is only built
inside `build_nt()` in the STANDARD flow. The predictive-limit and tick-sweep
proposals carry **no gate-evidence payload at all**: the persisted
`audit_signals.payload` for the 11 rows is
`{model_action, ai_*_probability=0, regime_confidence, risk_allowed:false,
guardian_status, rejection_reason, blocked_by:"", decision_stage}` — it neither
states that a structural path was taken, nor which structural conditions were
verified, nor that the confidence gate was not applicable on that path. A forensic
reader (as here) must reverse-engineer the path from `execution_mode` +
`decision_stage`; the counterfactual engine cannot distinguish "gate bypassed"
from "gate never applied".

**Recommended fix contract (observability-only, no gate change):**

1. `policy.py _evaluate_predictive_limit` / `_evaluate_tick_sweep`: stamp a
   path-specific `risk_checks` dict on the TradeProposal, e.g.
   `{"decision_path": "PREDICTIVE_LIMIT", "confidence_gate_applied": false,
   "structural_gate": {"valid_ob": true, "order_block_type": -1,
   "equilibrium_entry": …, "min_rr_enforced": 1.8, "smc_god_mode": false,
   "total_exposure": 0}, "model_confidence": 0.0,
   "model_confidence_verdict": "NOT_REQUIRED_STRUCTURAL_PATH"}` (mirror for
   TICK_SWEEP with `sweep_conf_thresh`, ofi, velocity, pierced/reversal flags).
2. Persist the same dict into `audit_signals.payload` (`risk_checks` key) — the
   audit repository already serializes whatever `risk_checks` carries.
3. Regression test (new, mirroring test_tick_sweep_requires_model_confidence):
   fire a predictive-limit proposal with conf 0.0 and assert
   `proposal.risk_checks["confidence_gate_applied"] is False` and the structural
   flags are populated — pinning the *contract* (structure replaces confidence,
   and the audit row proves it) rather than changing behavior.

**Optional hardening (policy decision, not required by the evidence):** if the
owner decides a zero-confidence model verdict should never underwrite real money,
add a `predictive_min_model_confidence` (e.g. 0.10 sanity floor) to
`_evaluate_predictive_limit` with its own named reason code — but that is a
strategy change; the −$52.85 realized across the 7 filled sub-0.20 trades is the
only direct cost evidence so far.
