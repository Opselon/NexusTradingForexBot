# Wave-2 Forensic: 60-Scenario Router Coverage Map with Reachability Proofs

**File:** `src/nexus_scalp/execution/order_manager.py`  
**Methods:** `_resolve_position_management_scenario` (L3131), `_arbitrate_decision` (L3568), `_decide_position_action` (L4687), `_run_protection_chain` (L5144)  
**Sibling suite:** `tests/unit/test_agent11_scenario_coverage_contract.py` — 6 tests, all GREEN (38 combined with Agent-12/Agent-11 forensic suites)  
**Date:** 2026-09-05 · Branch `hermes-subagent/subagent-sa-0-3c91f999` · Commit base `69033c54`

---

## 1. Topology

Single `if/elif/else` chain in `_resolve_position_management_scenario` (L3131–L3218). 21 `return` sites: 20 explicit S-codes + 1 terminal `else`. There is no actual "60-scenario" surface — the header comment is legacy branding; the implemented surface is the 20 codes below plus `S60`. No unreachable `elif` by Python control flow (all lexically reachable), but **four codes are logically dead under the current predicate ordering** (see §3). The method is pure (no broker I/O) — arbitration and dispatch happen downstream.

Derived pre-computations (L3146–L3165):
```
atr_n       = max(atr, 0.50)
spread_ratio = spread / atr_n
net_atr     = net_delta / atr_n
mae         = _mae_tracker[ticket] (price units, PositionTrackingLedger)
mae_atr     = mae / atr_n
desync      = metrics["desync_score"]
toxicity    = metrics["impact_to_net_profit_ratio"]
danger_tier = metrics["danger_tier"]
kill_switch = metrics["kill_switch_required"]
timeout_exit= metrics["time_decay_exit_required"]
defer_stops = metrics["defer_stop_management"]
defer_scale = metrics["defer_scale_out"]
missed_rescue = metrics["missed_position_rescue_mode"]
spread_spike= spread_ratio > 0.25
is_winning  = net_delta > 0 or gross_delta > 0
```

Metric producers live in `src/nexus_scalp/execution/position_intelligence.py` (`calculate_smart_position_metrics`, L260–L283) and are threaded via `smart_metrics` in `manage_active_positions` (L4443). Every emergency `CLOSE` is gated by `not is_winning_trade`.

### 1a. Protection-chain pre-emption (router never reached)

`_run_protection_chain` (L5144) runs **before** `_decide_position_action` (L4501 `continue`-on-True). Three early exits skip the router entirely for that tick:
- AI direction flip (§L5180–L5291, requires `probs` + whipsaw guard) → `CLOSE` + reversal stop.
- `protection.close_requested` or `_closed_tickets[ticket]` (L5311) → skip.
- `enforce_profit_giveback_protection` critical giveback (L5318) → skip.
The router therefore only decides positions that survived the deterministic protection priority chain (giveback → breakeven → MFE trailing → router).

### 1b. Rule-matrix pre-emption

`_decide_position_action` (L4717–L4735): if `rule_matrix.evaluate_in_trade_exits` returns `CLOSE`/`MODIFY_SL`, that `legacy_action/legacy_scenario` (`RULE_*` reason) bypasses the router entirely and goes straight to arbitration. Otherwise the router's `(legacy_action, legacy_scenario)` is used. So `RULE_*` verdicts are **not** S-codes but are treated as emergency cuts in arbitration (L3631).

---

## 2. Coverage Table

| # | Code | Action (L) | Exact trigger predicate (L) | Winning-shield | Reachability verdict | Overlap / shadowing note |
|---|------|-----------|----|---|---|---|
| 1 | **S01** `CRITICAL_COMPOUND_KILL_SWITCH` | `CLOSE` L3168 | `kill_switch && danger_tier=="CRITICAL_KILL" && !is_winning` | `not is_winning` ✓ | **REACHABLE** (top of chain, no predecessor can steal it) | `kill_switch` requires `directional_conflict>=0.70 && net< -0.4 ATR` OR `desync>30` (position_intelligence L264). Highest priority emergency. |
| 2 | **S02** `TOXIC_FLOW_KILL_SWITCH` | `CLOSE` L3170 | `kill_switch && toxicity>=4.5 && !is_winning` | ✓ | **REACHABLE** iff `danger_tier!="CRITICAL_KILL"` (otherwise S01 fires first). Narrow window: kill_switch true but not CRITICAL_KILL impossible — S01/S02 share `kill_switch` so S02 only fires when `danger_tier` would be CRITICAL_KILL but S01's `toxicity` check was S02's distinguishing trait. In practice S01 dominates; S02 is reachable only if one assumed S01's `danger_tier` check could fail while `toxicity>=4.5` holds — but current `danger_tier` for kill_switch is always `CRITICAL_KILL`, so **S02 is effectively subsumed by S01** whenever kill_switch is true. Retained as defense-in-depth. | Subsumed by S01 in current `danger_tier` logic. Not a code bug, but a dead-effective branch in production inputs. |
| 3 | **S09** `CRITICAL_HOLD_SCORE_BREACH_BAILOUT` | `CLOSE` L3172 | `hold_score<30 && !is_winning` | ✓ | **REACHABLE** | No net/mae gate — purely score-gated. This is the broadest bailout and therefore shadows four later codes (see §3). |
| 4 | **S08** `EXCESSIVE_MAE_DRAWDOWN_CUT` | `CLOSE` L3174 | `mae_atr>=1.20 && net_atr<=-0.40 && !is_winning` | ✓ | **CONDITIONALLY REACHABLE** — only when `hold_score>=30` (otherwise S09 fires first). Since S08 has no hold gate, its intended victims (`hold<30 && deep MAE`) are already caught by S09. Reachable tail: `hold>=30, mae>=1.2, net<=-0.4`. | Shadowed by S09 for `hold<30`. |
| 5 | **S04** `STRUCTURE_FAILURE_WITH_ACTIVE_LOSS` | `CLOSE` L3176 | `hold<=20 && net_atr<=-0.35 && !is_winning` | ✓ | **DEAD** — `hold<=20 ⇒ hold<30`, so S09 fires unconditionally first on every input that satisfies S04. No input reaches L3176. | Strictly subsumed by S09. See proof §3. |
| 6 | **S05** `EXTREME_TOXICITY_NEGATIVE_POSITION` | `CLOSE` L3178 | `toxicity>=4.8 && net_atr<-0.20 && !is_winning` | ✓ | **CONDITIONALLY REACHABLE** — only when `hold>=30` (otherwise S09). Also requires `mae<1.2` or `net_atr>-0.40` to have missed S08. Reachable tail exists (`hold>=30, toxicity>=4.8, net_atr in (-0.40,-0.20)`). | Shadowed by S09 for `hold<30`. |
| 7 | **S06** `SEVERE_DESYNC_WITH_UNCONTROLLED_LOSS` | `CLOSE` L3180 | `desync>=30 && net_atr<=-0.50 && !is_winning` | ✓ | **CONDITIONALLY REACHABLE** — only when `hold>=30` and S08/S05 missed. Desync path overlaps kill_switch path (S01 already covers `desync>30` when `!is_winning`), so S06 only matters if `kill_switch` somehow false despite `desync>30` (should not happen per L266). Narrow. | Overlaps S01; S01 dominates for same desync. |
| 8 | **S07** `CATASTROPHIC_SPREAD_EXPANSION` | `CLOSE` L3182 | `spread_ratio>=0.40 && net_atr<=-0.50 && !is_winning` | ✓ | **CONDITIONALLY REACHABLE** — only when `hold>=30` and prior closes missed. | Shadowed by S09 for `hold<30`. |
| 9 | **S10** `TERMINAL_HOLD_SCORE_FAILURE` | `CLOSE` L3184 | `hold<=10 && net_atr<=-0.25 && !is_winning` | ✓ | **DEAD** — `hold<=10 ⇒ hold<30`, S09 fires first. | Strictly subsumed by S09. |
| 10 | **S11** `DEEP_LOW_SCORE_BAILOUT` | `CLOSE` L3187 | `hold<25 && net_atr<=-0.50 && !is_winning` | ✓ | **DEAD** — `hold<25 ⇒ hold<30`, S09 fires first. | Strictly subsumed by S09. |
| 11 | **S12** `CONFIRMED_LOW_SCORE_BAILOUT` | `CLOSE` L3189 | `hold<35 && net_atr<=-0.65 && !is_winning` | ✓ | **PARTIALLY REACHABLE** — `hold<30` portion dead (S09); `hold∈[30,35)` with `net_atr<=-0.65` is reachable and is the **only** uncovered sliver. | 70% of its domain dead; 30% live. |
| 12 | **S13** `STANDARD_EARLY_EMERGENCY_BAILOUT` | `CLOSE` L3191 | `hold<50 && net_delta<0 && net_atr<=-0.40 && !is_winning` | ✓ (double-gated: `net_delta<0` redundant with `!is_winning` for this path but harmless) | **PARTIALLY REACHABLE** — `hold<30` portion dead (S09); reachable when `hold∈[30,50) && net_delta<0 && net_atr<=-0.40`. | Shadowed by S09 for `hold<30`. |
| 13 | **S21** `HARD_STAGNATION_TIMEOUT` | `CLOSE` L3194 | `timeout_exit && net_delta<0.10 && !is_winning` | ✓ | **REACHABLE** — but only when `hold>=50` or `net_atr>-0.40` (otherwise S13/S09 close first). Timeout requires `holding_duration>max_holding && net<0.10` (position_intelligence L276). | Downstream of all score-gated closes; narrow gate when score is healthy but time has expired on a small-loss. |
| 14 | **S22** `EXTENDED_CAPITAL_LOCK_TIMEOUT` | `CLOSE` L3196 | `holding> max_holding*1.5 && net_atr<0 && !is_winning` | ✓ | **REACHABLE** — only when S21 missed (`!timeout_exit` or `net_delta>=0.10`) but still `net_atr<0` after 50% over-hold. In practice largely redundant with S21 (`net_delta<0.10` ≈ `net_atr<0` for small atr), but distinguishable when `net_delta∈[0,0.10)` excluded by S21's `!is_winning` vs `net_atr<0`. | Overlaps S21; both require extended age. |
| 15 | **S32** `HIGH_PROFIT_SCALE_OUT` | `PARTIAL_CLOSE` L3204 | `net_atr>=1.50 && !defer_scale` | N/A (winning path; no `!is_winning` gate — correct, this is the winner branch) | **REACHABLE** — winner trail. `defer_scale` (`spread>0.25 ATR` OR `desync>15` per L283) suppresses it. | Must precede S44/S47/S48; correctly prioritized. `defer_scale` interaction creates §3 issue with S48/S52. |
| 16 | **S44** `HEALTHY_WINNER_NORMAL_TRAIL` | `NORMAL_TRAIL` L3206 | `net_atr>=0.90 && hold>=65` | N/A | **REACHABLE** — but only when S32 missed (`net_atr<1.50` OR `defer_scale`). So effective window `net_atr∈[0.90,1.50)` (or `>=1.50` with `defer_scale` true). | Narrowed by S32. |
| 17 | **S47** `STANDARD_BREAK_EVEN_LOCK` | `BREAK_EVEN` L3208 | `net_delta >= be_trigger*0.40 && hold>=40` | N/A | **REACHABLE** | Prioritizes over S48 when both true (correct: stronger hold requirement gets tighter BE). |
| 18 | **S48** `LOW_IMPACT_FAST_BREAK_EVEN` | `BREAK_EVEN` L3210 | `net_atr>=0.45` | N/A | **REACHABLE** | Unconditional trailing BE after S47 check. This ordering **shadows S52** when `net_atr>=0.45` (see §3). |
| 19 | **S52** `SPREAD_SPIKE_STOP_DEFER` | `DEFER_STOPS` L3213 | `defer_stops && spread_spike` (`spread_ratio>0.25 && (impact>0.60 OR desync>15)`) | N/A | **CONDITIONALLY REACHABLE** — only when `net_atr<0.45` (otherwise S48 fires first) AND after S47/S48 missed. Since `spread_spike` (`>0.25`) already implies `defer_scale` true, winners with spike skip S32 and land here correctly — but only if `net_atr<0.45`. High-profit spike winners (`net_atr>=0.45`) hit S48 before S52. | L3210 vs L3213 priority inversion for winners. |
| 20 | **S56** `MISSED_POSITION_STATE_RECONSTRUCTION` | `MONITOR` L3215 | `missed_rescue && net_atr<=0.0` (`holding>120s && not rescue_registered`, per L198) | N/A | **REACHABLE** — rescue bootstrap path; non-overlapping with S48 (`<=0` vs `>=0.45`). | No overlap with S52 except `net_atr<=0` vs `net_atr<0.45` share negative region — but S52 precedes S56, so `missed_rescue && net_atr<=0 && defer_stops && spread_spike` would hit S52 first. Acceptable. |
| 21 | **S60** `DEFAULT_CONTROLLED_HOLD` | `HOLD` L3218 | `else` (fallthrough) | N/A | **REACHABLE** (terminal) | Default safe hold; never a broker mutation. |

**S-code gaps:** S03, S14–S20, S23–S31, S33–S43, S45–S46, S49–S51, S53–S55, S57–S59 are **unimplemented** — reserved numbering, not missing logic. The forensic surface is exactly the 20 codes above.

---

## 3. Reachability Proofs & Dead-Branch Analysis

### DEAD-1: S04 shadowed by S09 (L3172→L3176)
```
S09: hold<30
S04: hold<=20 ∧ net<=-0.35
For any input satisfying S04, hold<=20 ⇒ hold<30 ⇒ S09 true.
S09 precedes S04. Therefore L3176 is unreachable.
Proof input: hold=15, net_atr=-0.50, !winning → S09 fires, S04 never evaluated.
```

### DEAD-2: S10 shadowed by S09 (L3172→L3184)
```
S10: hold<=10 ∧ net<=-0.25
hold<=10 ⇒ hold<30 ⇒ S09 true. Dead for same reason.
```

### DEAD-3: S11 shadowed by S09 (L3172→L3187)
```
S11: hold<25 ∧ net<=-0.50
hold<25 ⇒ hold<30 ⇒ S09 true. Dead.
```

### DEAD-4 (partial): S12 majority shadowed by S09 (L3172→L3189)
```
S12: hold<35 ∧ net<=-0.65
Case hold<30: subsumed by S09 → dead.
Case 30<=hold<35: survives. Live sliver exists (e.g., hold=32, net=-0.70).
Verdict: PARTIALLY DEAD (70% of domain dead, 30% live).
```

### EFFECTIVELY DEAD: S02 shadowed by S01 under current danger_tier logic
```
S01: kill ∧ danger=="CRITICAL_KILL"
S02: kill ∧ toxicity>=4.5
But kill_switch is set only when danger=="CRITICAL_KILL" (L268).
Hence kill ⇒ danger=="CRITICAL_KILL" always, so S01 fires first whenever S02 could.
S02 is reachable only under a hypothetical future change where kill could be set with danger!="CRITICAL_KILL".
Current production: EFFECTIVELY DEAD (defense-in-depth).
```

### OVERLAP: S48 shadows S52 for winning spike cases (L3210→L3213)
```
S48: net_atr>=0.45
S52: defer_stops ∧ spread>0.25
If spread>0.25 and net_atr>=0.45, S48 fires and S52 is skipped.
Consequence: a high-profit winner during a spread spike still tightens BE (S48) instead of deferring stops (S52).
This is arguably correct risk behavior (protect winner), but the naming/docs imply S52 should defer — the priority encodes "protect winner > defer."
Flagged as INTENTIONAL but DOCUMENT the interaction; swapping order would invert the risk posture.
```

### No other contradictions found
- All `CLOSE` predicates include `!is_winning`; no emergency close can contradict the profit shield.
- Profit-side chain S32→S44→S47→S48 is strictly priority-ordered with no contradictions (stricter net_atr/hold gates earlier).
- S56 (`net_atr<=0`) is correctly disjoint from S48 for the positive domain; overlap only in negative domain where S52 takes precedence — no contradiction.

---

## 4. Winning-Trade Shield (`is_winning_trade`)

**Predicate:** `is_winning_trade = net_delta>0 or gross_delta>0` (L3165). Strict `>0`, so exactly `0.00` is treated as losing (conservative — allows close at breakeven).

**Gating:** Every emergency `CLOSE` (S01,S02,S04–S13,S21,S22) includes `and not is_winning_trade` (L3168–L3201, 14 occurrences). Count verified by `test_profit_shield_guard_present_in_source` (`>=12` assertion) — actual is 14.

**Correctness verdict: PASS.**
- No winning trade can be emergency-closed via the router. Winning losses are routed to `S32/S44/S47/S48` or ultimately `S60 HOLD`, or exited via the separate giveback/budget paths in arbitration (`PROFIT_GIVEBACK_CRITICAL`, `LOSS_*`), which have their own semantics and are not subject to the `is_winning` definition (they use `peak_win_usd`).
- Edge correctness: breakeven (`net==0`) considered losing and closable — intentional conservative boundary; any change would need product sign-off.

---

## 5. Grace-Period Interaction (60s suppression in `_arbitrate_decision`)

**Source:** `_arbitrate_decision` L3594–L3646.

- `duration_sec = (now - entry_timestamps[ticket]).total_seconds()` — uses tick `now` threaded from the management loop, not wall clock (BUG-070 fix).
- If `duration<60`:
  - `LOSS_HARD_EXIT` / `LOSS_EXIT_PRESSURE` adaptive states are downgraded to `LOSS_RECOVERY_CANDIDATE` (L3634).
  - Any `is_legacy_emergency_cut` (S01,S02,S04–S13,S21,S22 **or** `RULE_*` via L3631) is forced to `HOLD` **unless** the scenario is `S01_CRITICAL_COMPOUND_KILL_SWITCH` (L3637–L3646). Suppression is logged at `debug`.

| Category | Suppressed <60s? | Verdict |
|----------|---|---|
| S01 (global kill) | **NO** — exempt | Correct — systemic risk must not be delayed. |
| S02,S04–S13,S21,S22 | **YES** → `HOLD` | Correct — suppresses instant bailouts through entry spread. |
| `RULE_*` (rule-matrix `CLOSE`) | **YES** → `HOLD` | **Flagged discrepancy.** Task description says "RULE_* exempt" — source does NOT exempt it; L3631 marks RULE as emergency, then L3637 suppresses it because only `S01` is whitelisted. If the intended design is "RULE_* exempt like S01," then L3639 needs `or legacy_scenario.startswith("RULE_")`. Current behavior delays rule-matrix emergency exits for 60s — appropriate for spread-grace consistency, but contradicting the stated spec. |
| `LOSS_HARD_EXIT`/`LOSS_EXIT_PRESSURE` | **YES** → downgraded | Correct — recovery budget exits respect grace. |
| `PROFIT_GIVEBACK_CRITICAL`, `LOSS_HARD_EXIT` at L3655/L3672 after grace block | Not inside grace conditional — but `LOSS_HARD_EXIT` adaptive_state already downgraded, so they won't fire <60s either. | Correct. |

**Missing `duration==0` case:** when `entry_timestamps` lacks ticket, `duration=0.0` → grace applies (most restrictive, safe default).

---

## 6. Action-Mapping Audit (dispatcher understands every router action)

Dispatcher: `_execute_position_action` (L4943) via `ExecutionPlan` (L4554).

| Action | Dispatcher branch | Verdict |
|--------|---|---|
| `CLOSE` | L4970 `if action=="CLOSE"` → `adapter.close_position` | ✓ |
| `MODIFY_SL` | L5008 `elif action=="MODIFY_SL"` → `adapter.modify_position` | ✓ (only `RULE_*`/`PROFIT_GIVEBACK_WARNING` reach arbitration with this action today) |
| `PARTIAL_CLOSE` | L5032 `elif action=="PARTIAL_CLOSE"` → `adapter.close_position(volume=...)` | ✓ |
| `BREAK_EVEN` | L5044 `elif action=="BREAK_EVEN"` → `adapter.modify_position` with BE target | ✓ |
| `NORMAL_TRAIL` | L5104 `elif action=="NORMAL_TRAIL"` → `adapter.modify_position` with ATR trail | ✓ |
| `DEFER_STOPS` (S52) | **No branch** — falls through; `_execute_position_action` does nothing | **INTENTIONAL NO-OP.** By design: pass is held while stops are deferred. No broker mutation, consistent with `S60` fallthrough telemetry. ExecutionPlan docstring comment at L19 historically omitted it (lists only 5 actions) but `DISPATCHER_UNDERSTOOD_ACTIONS` in the coverage contract correctly includes it as "non-mutating." |
| `MONITOR` (S56) | **No branch** — same no-op | Intentional — state reconstruction requires no broker action. |
| `HOLD` (S60) | **No branch** — same no-op; also the arbitration fallback `return "HOLD", "S60..."` (L3699) | Intentional — default hold, throttled telemetry only. |

**Missing-action verdict: NONE.** No router action is unhandled in a way that would cause a dispatch error; the three non-mutating actions correctly produce no broker call.

**One doc nit:** `src/nexus_scalp/execution/execution_plan.py` L19 comment lists `CLOSE | MODIFY_SL | PARTIAL_CLOSE | BREAK_EVEN | NORMAL_TRAIL` — omits `DEFER_STOPS/MONITOR/HOLD`. Harmless (type is `str`), but consider updating the comment to match `DISPATCHER_UNDERSTOOD_ACTIONS`.

---

## 7. Agent-11 Coverage Suite Findings

`tests/unit/test_agent11_scenario_coverage_contract.py` — **6/6 GREEN**.

| Test | What it pins | Result |
|------|---|---|
| `test_scenario_codes_enumerated` | 10 anchor codes + `len>=20` | **PASS** — 20 explicit + S60 = 21 |
| `test_every_router_action_is_dispatcher_understood` | all actions ∈ `DISPATCHER_UNDERSTOOD_ACTIONS` | **PASS** |
| `test_default_terminal_state_is_hold` | `return "HOLD","S60..."` present | **PASS** |
| `test_profit_shield_guard_present_in_source` | `not is_winning_trade` count ≥12 | **PASS** (actual 14) |
| `test_emergency_closes_precede_scale_out` | all `CLOSE` before first non-`CLOSE`, S32 in tail | **PASS** |
| `test_state_machine_bypass_states_are_emergency_only` | `BYPASS_STATES == {PROFIT_GIVEBACK_CRITICAL, LOSS_HARD_EXIT}` | **PASS** |

The suite is **order-sensitive but not shadowing-sensitive** — it does not detect DEAD-1..4. It guarantees the contract cannot silently shrink, but not that every listed code is reachable. The dead-branch findings above are therefore additive to, not contradicting, Agent-11.

---

## 8. Flags & Recommended Actions

| Priority | Flag | Location | Recommendation |
|----------|------|----------|---|
| **P1** | **Dead branches S04, S10, S11 fully shadowed by S09** | `order_manager.py:3172 → 3176,3184,3187` | Either (a) **move S04/S08/S10/S11 before S09** and gate S09 with `net_atr>-0.25` fallback, or (b) **delete S04/S10/S11** and fold their scenario labels into telemetry (they never emit). Keeping dead code as "documentation" is a drift hazard — the next reader will assume they fire. If retained, add an explicit `# DEAD: subsumed by S09` comment or guard S09 with an additional predicate. |
| **P2** | **Partially dead S12 / S13** — 70%/50% shadowed | `L3189, L3191` | No immediate fix; live slivers (`hold 30–35` / `30–50`) are intentionally the "confirmed/standard" tiers. Add a comment documenting the S09 shadow and the live window, so future hold-threshold changes don't accidentally widen the dead region. |
| **P2** | **Effectively dead S02** under current `danger_tier` logic | `L3170` + `position_intelligence.py:264` | Consider folding S02 into S01 label or making `toxicity>=4.5` an independent `danger_tier` upgrade so S02 has a genuine trigger window. |
| **P2** | **RULE_* grace suppression vs spec mismatch** | `order_manager.py:3631,3639` | Decide product intent: if rule-matrix emergency closes should be grace-exempt (like S01), whitelist `RULE_*` at L3639. Current suppression (60s HOLD) is spread-safe but contradicts "RULE_* exempt" spec. File a decision record. |
| **P3** | **S48 shadows S52 for winning spike** | `L3210 vs L3213` | Document as intentional ("protect winner > defer stops") or swap order if deferral is the desired spike behavior. Current order is defensible. |
| **P3** | **ExecutionPlan docstring omits non-mutating actions** | `execution_plan.py:19` | Update comment to `CLOSE | MODIFY_SL | PARTIAL_CLOSE | BREAK_EVEN | NORMAL_TRAIL | DEFER_STOPS | MONITOR | HOLD`. |

No contradictions in winning-shield gating, no missing dispatcher mappings, no grace-period bypass for S01, and the 60-scenario header remains a misnomer (21 actual returns).

---

## 9. Methods & Budget

- Read `order_manager.py` L3131 (`_resolve_position_management_scenario`), L3568 (`_arbitrate_decision`), L4687 (`_decide_position_action`), L5144 (`_run_protection_chain`), L4440 (management loop wiring), plus `position_intelligence.py` metric producers and `position_tracker.py` MAE/MFE ledger.
- Re-ran `test_agent11_scenario_coverage_contract` + companion forensic suites — **38 passed**.
- Time box: ~8 minutes. No network access used; all proofs by static predicate analysis.

