# Wave 2026-09-14 / Lane 02 — PASSIVITY ROOT-CAUSE: Decision Funnel Forensics

Repo: `NexusTradingForexBot` @ `nse/master-active-scalper-wave` (HEAD `9431edd2`).
Method: read-only sqlite (`mode=ro`) over `artifacts/candle_intel.db`, `artifacts/audit.db`,
runtime store `%LOCALAPPDATA%\NexusScalpEngine\databases\app_settings.db`, engine logs
`logs/{info,warning,error}/2026/09/*`, and full code read of the tick→order pipeline.
Every number below is VERIFIED by an executed query/grep recorded in this document's evidence lines.

**Headline:** on the live decision plane (2026-09-06 23:45 → 2026-09-11 19:59 UTC) the engine emitted
1,469 genuine decisions and only 12 survived policy (0.82%); of those 12, 5 reached broker fills
(2026-09-08, all exited HOLD_SCORE_DECAY, net −$88.31) and the 2026-09-11 batch never dispatched
(28 NOT_DISPATCHED / 1 REJECTED_UNFILLED / 6 unresolved; last engine `Executed order` row overall:
2026-09-03T13:04). The 0.82% survival rate was earned almost entirely by the structural
PREDICTIVE_LIMIT channel — the model-confidence path produced zero survivors in the window.
Passivity is NOT one gate — it is a stack of four independent kill-layers, each of which alone
would be near-fatal: (1) a confidence gate mathematically above the serving model's output ceiling,
(2) a regime "guardian" that labels this broker's normal gold spread as unsafe (≈24% of decisions,
plus a redundant risk-engine spread cap that finished the survivors), (3) a 60-second order-frequency
throttle that swallowed 78,798 evaluations, and (4) a stalled market-data feed (2026-09-11 20:00 →
present) so that in the final 2.5 days the tick loop dedup-returns on a frozen quote and **no
decision is generated at all** while the engine reports healthy.

---

## 0. Measurement planes & staleness

| Artifact | Window (UTC) | Rows | Status |
|---|---|---|---|
| `candle_intel.db` `trade_decisions/rule_vetoes/risk_evaluations/market_regimes` | 2026-08-17 19:37 → **2026-09-07 01:26** | 4505 / 1295 / 4505 / 4510 | **FROZEN** — subsystem disabled 09-07 (`configs/base.yaml` `candle_intel.enabled: false`; boot logs 09-13/09-14: `[CANDLE_INTEL] disabled … H5 audit rev2`; `live_engine.py:946-965`) |
| `audit.db` `audit_signals` | 2026-09-06 23:45 → **2026-09-11 19:59** | 1469 | Frozen since feed stall (see §5 RC-3); hygiene retention=7d (`hygiene/retention.py:156-161`), so pre-09-07 rows may already be pruned |
| `audit_guard_telemetry` | 2026-09-01 05:32 → 2026-09-11 16:47 | 449 rows | 78,798 ORDER_FREQUENCY_THROTTLED + 3,995 TICK_DUPLICATE_SUPPRESSED events |
| `audit_experiences` / `_outcomes` | 2026-08-17 → 2026-09-11 16:47 | 1373 / 1118 | outcomes: 163 executed, 955 not-executed |
| `audit_orders` | 2026-08-17 → 2026-09-08 19:25 | 8600 | last `Executed order` **2026-09-03**; after 09-04: 5 rows total |
| broker truth (`audit_broker_deals` magic=888101) | … 2026-09-08 | 2,086 deals | last engine-relevant activity 09-08 (10 deals) |
| engine process | boots 09-12, 09-13 ×5, 09-14 | — | **alive but feed-stalled**: `[WATCHDOG] Tick stream stalled while MT5 reports connected` ×1,828 (09-13), 30+ (09-14, every ~20 s); `Out-of-order tick dropped … last_accepted=2026-09-11T20:00` |

`candle_intel` and `audit_signals` windows **do not overlap the same regime of the code**: the 4,505
bar decisions are produced by a **dead subsystem** (zero consumers — see §6), while the live policy
funnel is only measurable through `audit_signals` (1,469 rows, 5 days).

---

## 1. Rejection attribution table

Normalized over both planes. `% bars` = of 4,505 M1 decisions (candle plane, dead subsystem);
`% sig` = of 1,469 audit_signals (live plane); `telem` = of ~82,793 guarded evaluations (telemetry plane).

| # | Category | Reason code(s) | Plane | n | % | file:line of gate |
|---|---|---|---|---|---|---|
| 1 | REGIME (spread-driven) | `BLOCKED_BY_GUARDIAN_UNSAFE_REGIME` (all HIGH_SPREAD_CHOP; live spread p25 $0.26/p50 $0.30) | sig | 356 | 24.2% sig | `signals/policy.py:227-229`, `:2154-2222` (`UNSAFE_REGIMES` set `:2166-2173`); trigger `features/regime_classifier.py:139-140` (enter ≥ $0.25, exit ≥ $0.18) |
| 1b | REGIME | `REGIME_BLOCKED:UNKNOWN` | bars | 16 | 0.36% bars | `candle_intelligence/decision.py:116-125` |
| 2 | CONFIDENCE | `INSUFFICIENT_CONFIDENCE … < Effective Threshold (0.40\|0.50)` | sig | 436 | 29.7% sig | `policy.py:1215-1229`; threshold build `:723-727` (base 0.40 runtime + 0.10 range penalty) |
| 3 | CONFIDENCE (duplicate of #2) | `ZONE_QUALITY_BELOW_THRESHOLD (x < 0.70)` where `zone_quality_score := confidence` | sig | 45 | 3.1% sig | `policy.py:1235-1242` (`:1236` — same variable, second threshold) |
| 4 | STRUCTURE | `NO_CANDIDATE_{RANGING_MEAN_REVERSION,TRENDING_MOMENTUM,VOLATILITY_EXPANSION}` (no channel fired) | sig | 434 | 29.5% sig | `policy.py:1017-1021` (default label), candidate construction `:474-503`, `:1028-1134` |
| 5 | STRUCTURE | `BUY/SELL_REJECTED_SR_{RESISTANCE,SUPPORT}_MARGIN_FAIL` (≥$0.25 zone margin) | sig | 52 | 3.5% sig | `policy.py:816-820`, `:1049-1053`, `:1097-1101` |
| 6 | STRUCTURE | `*_REJECTED_HTF_TREND_CONFL_FAIL`, `RANGE_FILTERED_IMPULSIVE_*` | sig | 22 | 1.5% sig | `policy.py:802-807`, `:1044-1047`, `:1092-1095`, `:1084`, `:1131` |
| 7 | RISK (geometry) | `ASYMMETRIC_RR_BELOW_CONFIGURED_THRESHOLD` (min_rr=2.2 runtime; 37/61 rows are DEDUP re-surfaces) | sig | 61 | 4.2% sig | `policy.py:1324-1328`; bypass requires conf ≥ `high_confidence_threshold`=0.95 `:1306-1308` |
| 8 | RISK (soft intelligence) | predictive-channel killed by `EXPERIENCE_DEGRADED` (26) + `SUITABILITY_GATE` (22) after `PREDICTIVE_OB_*_EQUILIBRIUM` | sig | 48 | 3.3% sig | `application/live/tick_pipeline.py:71-89`; `experience/intelligence.py:353-361`; `intelligence/gate.py:205-220` |
| 9 | COOLDOWN | `ORDER_FREQUENCY_THROTTLED` (hard 60 s) | telem | 78,798 events | 95.2% of guarded evals | `policy.py:1894-1909` (`:1905` `elapsed < 60.0`) vs constructor `cooldown_seconds=4.0` at `live_engine.py:1377` |
| 9b | COOLDOWN | `TICK_DUPLICATE_SUPPRESSED` / engine dup early-return | telem | 3,995 events | 4.8% | `policy.py:2006-2098`; `runtime_loop.py:413-423` |
| 9c | COOLDOWN | `COOLDOWN_ACTIVE` (4 s), `PENDING_ORDER_LOCKED` (30 s), `FLIP_PROTECTION` (8 s), `SAME_LEVEL_REENTRY` ($0.50) | sig | 0 | 0% | `policy.py:1275-1282`, `:1877-1891`, `:1247-1271`, `:1179-1213` |
| 10 | POSITION_LIMIT | `MAX_EXPOSURE_REACHED` (engine-wide 1 position OR 1 pending) | sig | 3 | 0.2% sig | `policy.py:1809-1854`; dispatch echo `execution/lifecycle/dispatch.py:374-393` |
| 11 | SPREAD (late-layer) | risk-engine `Spread exceeds maximum threshold` (max_spread_points=20 ⇒ $0.20; logged kills at 21/24/27/28/35 pts) | log | 5 (09-11 alone) | 42% of the 12 survivors | `risk/risk_engine.py:413-422`; runtime value from `app_settings.db` `risk.max_spread_points=20` (repo yaml says 60) |
| 11b | SPREAD (unused gates) | `SPREAD_TP_RATIO/SESSION_PCT/ATR_RATIO_EXCEEDED` | sig | 0 | 0% | `policy.py:1329-1350` — session gate input table `audit_paper_executions` has **0 rows** ⇒ gate (b) permanently no-op |
| 12 | LIQUIDITY | `EXCESSIVE_MARKET_IMPACT_REJECTED` | log | 1 (09-11) | — | `risk_engine.py:631-659` |
| 13 | NO_DATA / stale-input | `BLOCKED_BY_STALE` freshness downgrade; account-stale tick skip; `PROBS_UNAVAILABLE_DEGRADED` | log/sig | 2 (09-11 logs) + 0 | — | `live_freshness.py:120-130`, `tick_pipeline.py:192-197`, `runtime_loop.py:378-386`, `policy.py:210-226` |
| 14 | INVALID_DATA (bar plane) | `INVALID_CANDLE_DATA` / `VETO:INVALID_CANDLE` | bars | 92 | 2.0% bars | `candle_intelligence/decision.py:74-83` |
| 15 | STRUCTURE (bar plane) | `WEAK_CLOSE_BLOCKS_ENTRY:INDECISION` (741) + `:TRAPPED_BREAKOUT` (446) | bars | 1,187 | 26.3% bars | `decision.py:211-212`; **zero live consumers** |
| 16 | NEWS | news gate = bounded conf adjustment only, never blocks | sig | 0 | 0% | `tick_pipeline.py:101-134` |
| 17 | EXECUTION (terminal outcomes) | `NOT_DISPATCHED` (879), `REJECTED_UNFILLED` (63), `CANCELED_UNFILLED` (13) of 1,118 outcomes | outcomes | 955 not-executed | 85.4% of outcomes | `execution/lifecycle/dispatch.py:265,286,345,388,407`; `tick_pipeline.py:150-179` |
| 18 | UNKNOWN | reason_code unmapped | — | 0 | 0% | — |

Cross-check (bar plane): `rule_vetoes` 1295 rows = exactly the `decision_type='NO_TRADE'` count; all at
`veto_level=3`; preceding state by regime: HIGH_SPREAD_CHOP 653, RANGING 499, TRENDING 101, UNKNOWN 33,
VOL-EXP 9. `risk_evaluations`: **4,505/4,505 `risk_allowed=1`, empty payloads** — that layer never
gated anything (vacuous).

---

## 2. Funnel counts

### Live decision plane (audit.db, 09-06 → 09-11)

```
tick loop iterations (50 ms poll, ~86k+ guarded evals observed via telemetry + signals)
 ├─ duplicate-quote early-return (runtime_loop.py:413)            3,995 counted
 ├─ ORDER_FREQUENCY_THROTTLED (policy.py:1905, 60 s hard)        78,798 counted
 └─ genuine evaluations persisted .................................   1,469
     ├─ GUARDIAN pre-model freeze (HIGH_SPREAD_CHOP) ..............   -356   (24.2%)
     ├─ model ran, NO structural candidate ........................   -434   (29.5%)
     ├─ HTF trend / S/R margin / range filters ....................    -73    (5.0%)
     ├─ CONFIDENCE_GATE (< 0.40 base / < 0.50 in range) ...........   -436   (29.7%)
     ├─ ZONE_QUALITY (duplicate conf re-check vs 0.70) ............    -45    (3.1%)
     ├─ ASYMMETRIC_RR (min_rr 2.2; 37 are dup re-surfaces) ........    -61    (4.2%)
     ├─ exposure / pending state .................................     -3    (0.2%)
     └─ ACTIONABLE survivors after policy ..........................    12    (0.82%)
         (sum of rows 1-8 above = 1,408 explicit rejections + 49
          STANDARD_EVAL/DEDUP "passed-candidate" rows that carry a
          candidate conf but no emitted action = 1,457 NO_TRADE;
          the only 12 actionable rows are all PREDICTIVE_LIMIT — every
          STANDARD-path candidate died at the confidence pair. Before
          downgrades, policy emitted 72 PREDICTIVE_OB proposals (12
          actionable + 60 killed post-policy by the experience/
          suitability pair: 72→12, an 83% kill rate on the channel
          that is policy's only remaining exit))
         ├─ post-policy: experience/suitability kill ..............   -48 of 60 rejections (80%)
         ├─ risk_engine.evaluate_proposal (spread>20pts ×5, impact ×1, 09-11) 
         │    → RISK_EVALUATION_REJECTED .........................    at least -6 of 7 that day
         ├─ dispatch → broker fill ...............................     5 (2026-09-08 only;
         │    all exited HOLD_SCORE_DECAY, net -$88.31; outcome ids 152599606787..152600834087)
         └─ dispatch → broker fill from the 2026-09-11 survivors ..     0
             (28 NOT_DISPATCHED + 1 REJECTED_UNFILLED + 6 outcome-less rows that day;
              the 09-08 fills are the last executions anywhere: last `Executed order`
              STANDARD row 2026-09-03, broker deals magic=888101 end 2026-09-08).
```

### Bar plane (candle_intel.db, dead subsystem, 08-17 → 09-07)

```
4,510 regime evals → 4,505 bar decisions
 ├─ vetoed (decision_type NO_TRADE) .............. 1,295 (28.7%)
 │    WEAK_CLOSE:INDECISION 741 · TRAPPED_BREAKOUT 446 · INVALID_CANDLE 92 · REGIME_UNKNOWN 16
 ├─ HOLD 180 · FAST_EXIT 78
 └─ entry_allowed=1 .............................. 2,952 (65.5%)  → consumed by: NOTHING
      (trade_proposals=0 rows, exit_signals=0, open_positions=0; configs/base.yaml candle_intel
       enabled:false; live_engine.py:946-965)
```

### Outcome plane

1,118 outcomes → 163 executed (14.6%) with net **−$2,984.45** (77 wins), 955 never executed
(NOT_DISPATCHED 879 / REJECTED_UNFILLED 63 / CANCELED_UNFILLED 13). Behavioral flags:
PREMATURE_ENTRY ×54, RISK_DEVIATION ×8.

---

## 3. Every veto/gate from tick to order (file:line, enforcement order)

**L0 — runtime loop** (`application/live/runtime_loop.py`)
1. `:384-386` account snapshot None/STALE → skip tick entirely (fail-closed, silent)
2. `:413-423` duplicate-quote early return (BUG-169; state `_pipeline_last_*`)
3. `:174` + `application/live/warmup.py` HTF warmup gate — entries fail-closed until H1/H4 ready

**L1 — signal policy** (`signals/policy.py`, inside `evaluate_probabilities:171`)
4. `:210-226` `PROBS_UNAVAILABLE_DEGRADED` (blocked_by=INFERENCE_DEGRADED)
5. `:227-229` → `:2154-2222` **GUARDIAN_GATE**: `UNSAFE_REGIMES={HIGH_SPREAD_CHOP, UNKNOWN, MARKET_HALTED, LOW_LIQUIDITY, NEWS_LOCK, MACRO_NEWS_FREEZE}` or FREEZE_ALL → pre-model NO_TRADE (conf 0.0, `PRE_MODEL_GUARDIAN`)
6. `:231-233` → `:2100-2152` SYMBOL_WHITELIST_GATE (XAUUSD-only operator ruling)
7. `:239-243` → `:2006-2098` DEDUP_GATE (same ts or same bid/ask)
8. `:340-357` AI-reversal path (close-first; flips gated by L6 risk engine, `decision_executor.py:159-205`)
9. `:359-363` → `:1894-1909` **ORDER_FREQUENCY_THROTTLED — hardcoded 60 s** (`elapsed < 60.0`)
10. `:366-384` → `:1785-1892` EXPOSURE_GATE (MAX_TOTAL_EXPOSURE=1; `SAME_LEVEL_REENTRY_BLOCKED`, `PENDING_ORDER_LOCKED` 30 s + 1×ATR drift)
11. `:965-972` rule-matrix pre-trade filters (**20/20 rules `is_enabled=0`** — `audit.db trading_rules_config`)
12. `:1028-1038` fast-liquidity-sweep channel; `:822-844`/`:1547-1668` TICK_SWEEP channel
13. `:1044-1047`/`:1092-1095` HTF_TREND_FILTER; `:1049-1053`/`:1097-1101` SR_MARGIN_FILTER ($0.25);
    `:1084`/`:1131` RANGE_FILTER; `:1146-1152` OB_EQUILIBRIUM_FILTER (sell side, <50%)
14. `:847`/`:1670-1783` PREDICTIVE_LIMIT structural channel (bypasses conf gate by design)
15. `:1179-1213` REENTRY_GATE ($0.50 same-level); `:1215-1229` **CONFIDENCE_GATE**
    (active_threshold = 0.40 runtime + 0.10 range + 0.10 survival; `:723-727`)
16. `:1235-1242` **ZONE_QUALITY_GATE** (`zone_quality_score = confidence` — duplicate variable vs 0.70)
17. `:1247-1271` FLIP_PROTECTION (thr+0.10, 8 s memory); `:1275-1282` COOLDOWN (4 s from engine)
18. `:1324-1328` ASYMMETRIC_RR (min_rr 2.2; high-conf bypass needs 0.95); `:1329-1350`
    SPREAD_TP (15% of TP), SPREAD_SESSION_PCT (P70 — no-op: `audit_paper_executions`=0 rows), SPREAD_ATR (18%)

**L2 — post-policy pipeline** (`application/live/tick_pipeline.py`)
19. `:71-76` PHASE-08 experience gate → `EXPERIENCE_INTELLIGENCE_GATE` (`experience/intelligence.py:353-361`)
20. `:85-89` PHASE-09 suitability gate → `TRADE_INTELLIGENCE_GATE` (`intelligence/gate.py:205-220`, sets conf→0.0)
21. `:101-134` PHASE-12 news gate (bounded ±0.05/0.10 adjustment, never blocks; failure = no-op)
22. `:192-197` G29 freshness gate → `BLOCKED_BY_STALE` downgrade (`live_engine.py:3561`, `live_freshness.py:120-130`)

**L3 — decision executor** (`application/live/decision_executor.py`)
23. `:101-116` DEDUP_GATE proposals force-downgraded (never executable)
24. `:129-150` SHADOW-mode observation boundary
25. `:253-281` **RiskEngine.evaluate_proposal — mandatory for every primary entry** (AGENT-6 fix); None ⇒ `RISK_EVALUATION_REJECTED`

**L4 — risk engine** (`risk/risk_engine.py`)
26. `:321-323` kill switch; `:332-340` circuit breakers (daily/weekly budgets, 8-loss→4 h cooldown;
    runtime `risk.max_account_drawdown_pct=95.0` ⇒ drawdown breaker effectively disarmed)
27. `:354-361` max_concurrent_positions=1; `:366-372` max_pending; `:387-394` opposing-exposure; `:403-410` max_allowed_lots (1.0 runtime)
28. `:416-422` **spread gate: max_spread_points=20 runtime ($0.20)** (repo yaml 60 — drift)
29. `:436-442` RR gatekeeper again (duplicate of #18); `:449-475` triple broker stops-level validation
30. `:580-659` margin/exposure clamp (volume→0 ⇒ reject) + Almgren-Chriss impact guard (>45% reward ⇒ reject)

**L5 — dispatch** (`execution/lifecycle/dispatch.py`)
31. `:252-272` KILL_SWITCH/persisted halt; `:277-291` SAFE_MODE (3-consecutive-rejection breaker `:496-505,576-580`)
32. `:311-350` MAINTENANCE_WINDOW (23:00–01:00 server time, directional entries only, fail-closed)
33. `:353-367` request-id idempotency; `:374-393` MAX_EXPOSURE re-check; `:396-412` HARD_MAX_LOTS/free-margin clamp ⇒ `LOT_SIZE_REJECTED`
34. broker `OrderSend` failures → `REJECTED_UNFILLED`/`CANCELED_UNFILLED` outcomes

**Parallel dead path** — `candle_intelligence/decision.py` gates `INVALID_CANDLE_DATA:74`,
`REGIME_BLOCKED:116`, `WEAK_CLOSE_BLOCKS_ENTRY:211` (and `hold/fast_exit:269-306`) write only to
candle_intel.db; no reader exists in policy/execution/features (`grep` §6).

---

## 4. Justified vs. pathological classification

| Gate(s) | Verdict | Why (evidence) |
|---|---|---|
| #5 GUARDIAN (HIGH_SPREAD_CHOP) | **stale-input + false-safety + duplicate-veto** | enter-threshold $0.25 was calibrated on a p50=$0.04 spread era (`features/regime_classifier.py:135-140` comment; `configs/execution_assumptions.json` notes the 3.7× distribution disagreement), while the live broker spread is p50 $0.23–0.30 (audit p50 0.23; market_regimes p50 0.30) → **44–65% of quotes classify as unsafe**; the same condition is re-vetoed later by #11 (risk spread 20 pts) and #18 — quadruple counting of one physical fact. LOW_LIQUIDITY/NEWS entries are justified; the chop arm is not. |
| #15 CONFIDENCE_GATE | **impossible-threshold (dominant)** | serving 70D champion outputs near-uniform 4-class softmax: mean buy 0.387/sell 0.360/no-trade 0.253; **max normalized confidence ever recorded on the live plane = 0.5015** (1 row of 1,113 ≥0.50, 323 ≥0.40 all pre-range-penalty). Runtime base 0.40 (`app_settings.db model.confidence_threshold=0.40`) + 0.10 range penalty = 0.50 equals the global ceiling. The repo's own comment at `policy.py:248-261` records this exact failure once already (0/464 candidates pre-CHG-0042); the normalization repair moved the ceiling but not the threshold. |
| #16 ZONE_QUALITY | **duplicate-veto** | `zone_quality_score = confidence` (`policy.py:1236`) — re-tests the identical variable at 0.70 (runtime; yaml 0.60). Any candidate surviving #15 then dies if conf<0.70; mathematically conf∈[0.40/0.50, 0.70) is the entire kill band; observed kills all 0.41–0.46 — i.e. it only fires because #15's threshold is lower, adding no information. |
| #9 60-s throttle | **false-safety (15× the intended constant) + duplicate-timer** | `policy.py:1905` hardcodes 60 s while the engine passes `cooldown_seconds=4.0` (`live_engine.py:1377`) and #17 also implements a 4 s COOLDOWN; the throttle is the *first* gate after reversal, so it also delays protective AI-reversal entries. 78,798 swallowed evals vs 1,469 signals. |
| #18 ASYMMETRIC_RR | **impossible-bypass + input duplication** | min_rr runtime 2.2 (yaml 1.8); TP is pre-stretched to `min(1.10, active_min_rr)` × risk (`:1310-1321`), so actual_rr∈[1.10, 2.2) can only pass via structural geometry; the relaxed 1.2 bypass demands conf ≥ 0.95 — unreachable (ceiling 0.50). 37/61 rows are DEDUP re-surfaces inflating the count. Repeated verbatim at `risk_engine.py:436-442` (#29) — a third duplicate. |
| #19/#20 experience+suitability pair | **duplicate-veto over a bypass channel; partly dead-subsystem** | their 48 kills are all on `PREDICTIVE_OB_*` — the *only* channel that ever survived policy (72 emissions; 60 killed = 83%). They re-do evidence-quality work of #15/#16 on structural proposals whose design contract (`policy.py:1763-1781`) explicitly says model confidence is NOT_REQUIRED; underlying evidence is paper/synthetic-era (SYNTHETIC default `PaperDataConfig`, `config.py:206-234`) with −$2.98k expectancy. |
| #12-#14 channel + SR/HTF filters | **justified (mostly)** | 434 NO_CANDIDATE means the multi-confluence channels genuinely didn't fire in chop/range; SR ($0.25 on a $4,400 asset = noise-level margin, borderline) and HTF kills are small-count and directionally sensible. |
| #28 risk spread ($0.20) | **impossible-threshold at this broker** | live spread p50 $0.23 ⇒ even if all upstream gates opened, the majority of survivors die here — observed 5/7 kills on 09-11; runtime 20 pts vs repo 60 pts = undocumented config drift. |
| #31-#33 dispatch stack | **justified** | kill-switch/SAFE_MODE/maintenance/idempotency/exposure/lot-clamp are capital-safety invariants; no evidence of false firing (zero SAFE_MODE rows in 09-11..14 logs). |
| candle_intel gates (decision.py) | **dead-subsystem** | produced 1,295 vetoes + 2,952 entry-allow verdicts with **zero consumers** (operator ruling 09-07, `configs/base.yaml:67-74`, `live_engine.py:946-965`); the 16% "INDECISION blocks" people might attribute passivity to never existed on the live path. |
| `risk_evaluations` table | **vacuous artifact** | 4,505/4,505 allowed, empty payloads — must not be read as "risk layer passes". |
| #2/#3 feed freshness/account-stale/dedup | **justified behavior, fatal current input** | the guards are correct; the *input* has been frozen since 2026-09-11 20:00 UTC (watchdog storm, out-of-order drops), so the engine currently decides nothing at all. |

---

## 5. Top-5 ranked root causes

**RC-1 — The confidence gate sits above the model's output ceiling (≈33% of all live decisions, ~77% of candidate-stage rejections).**
436 `CONFIDENCE_FAIL` + 45 `ZONE_QUALITY` = 481/1,469 (32.7%). Live normalized confidence: mean 0.167
over ALL rows, max 0.5015; raw softmax ≈ uniform (buy .387/sell .360/no-trade .253). Runtime threshold
0.40 (+0.10 in range regime = 0.50, and 286/436 kills were RANGING — `regime` breakdown §1) equals the
all-time max. The model is uncalibrated/degenerate — `audit_experiences.model_probability` for executed
trades sits at 0.56 avg and the same ceiling appears in `policy.py:248-261`'s own postmortem of the
earlier 0/464 incident. Gate: `signals/policy.py:723-727,1215-1229`; thresholds
`app_settings.db model.confidence_threshold=0.40` (repo yaml 0.35 — drift). Even a perfect structural
setup cannot pass: STANDARD-path survival requires conf ≥ 0.50 **and** RR≥2.2 bypass impossible (needs 0.95).

**RC-2 — Regime classifier labels this broker's normal XAUUSD spread as HIGH_SPREAD_CHOP; the guardian freezes 24% of decisions pre-model, and the identical physical condition re-kills the last survivors at the risk layer.**
356 `BLOCKED_BY_GUARDIAN_UNSAFE_REGIME` (24.2%), all HIGH_SPREAD_CHOP; guardian-row spreads p25 $0.26 /
p50 $0.30 vs enter-threshold $0.25 (`regime_classifier.py:139-140`, calibrated on a p50=$0.04 era —
`execution_assumptions.json`). 49.4% of bar-plane regimes are HIGH_SPREAD_CHOP. Duplicate veto: runtime
`risk.max_spread_points=20` ($0.20) rejects ≥ the remaining survivors (5 logged kills at 21–35 pts on
09-11; `risk_engine.py:416`). Two more spread gates (#11b) and a session-percentile gate with a 0-row
input table sit unused.

**RC-3 — The market-data feed has been frozen since 2026-09-11 20:00 UTC while the engine reports alive: current passivity is largely NO-DATA, not rejections.**
Last `audit_signals` row 09-11 19:59:59; last bar reseed `last=2026-09-11T19:59`; boots on 09-12/13/14
immediately log `Out-of-order tick dropped … last_accepted=2026-09-11T20:00` and `[WATCHDOG] Tick
stream stalled while MT5 reports connected (is_connected=True)` ×1,828 on 09-13 alone; the duplicate-quote
early-return (`runtime_loop.py:413-423`) then skips the pipeline every iteration, so freshness gate,
policy, risk and dispatch never run. Any passivity report for the last 2.5 days is a data-plumbing bug,
invisible in rejection tables (0 rows because 0 attempts).

**RC-4 — Suppressive-timer stack: a hardcoded 60-s order-frequency throttle (15× the configured 4-s cooldown) swallowed 78,798 evaluations, layered over 4-s cooldown, 30-s pending lock, 8-s flip memory and a $0.50 re-entry lock.**
`policy.py:1905` (`elapsed < 60.0`, "MIN_ORDER_INTERVAL_SECONDS = 60") vs `live_engine.py:1377`
(`cooldown_seconds=4.0`); telemetry shows 78,797+3,995 guard events vs 1,469 persisted signals (96:1)
across only 45 active throttle-hours. It runs *before* candidate construction (#9 precedes everything
after guardian) and caps entry frequency at 1/min even in ideal conditions; `COOLDOWN_ACTIVE` itself
never fires (0 rows) only because the throttle pre-empts it — a dead duplicate.

**RC-5 — The only surviving channel is strangled by redundant intelligence gates, while every designed alternative entry source is switched off: rule matrix 0/20 enabled, candle-intel verdicts (2,952 entry-allowed) disconnected, 70D model near-uniform.**
12 survivors are all `PREDICTIVE_LIMIT`; 60 of its 72 emissions were post-policy downgraded
`EXPERIENCE_DEGRADED`/`SUITABILITY_GATE` (83% — `tick_pipeline.py:71-89`), and all 6 survivors of
2026-09-11 that reached the risk engine were killed there (5×`Spread exceeds max_allowed=20` at 21–35
pts, 1×`EXCESSIVE_MARKET_IMPACT` — warning logs 09-11 17:19–19:01); only the 2026-09-08 batch ever
filled (5 trades, −$88.31, all exited by HOLD_SCORE_DECAY before even resting). Meanwhile
`trading_rules_config` has all 20 rules
`is_enabled=0` (rule-matrix entry engine `policy.py:974-1002` can never fire), the `NO_CANDIDATE` default
(434) shows the STANDARD confluence channels need ichimoku/kumo or |z|≥2 stat-arb that chop markets
rarely provide, and the `audit_executions` table contains 3 synthetic `test_req_*` rows + 954
BUG-254-duplicate REJECTED rows — i.e. no real execution history feeds the experience evidence either.
Net: each layer is individually defensible; composed, the funnel has no viable path from tick to order.

---

## 6. Evidence appendix (key executed probes)

```sql
-- live plane attribution
SELECT decision_stage, blocked_by, COUNT(*) FROM audit_signals GROUP BY 1,2;      -- §1 table
SELECT COUNT(*) FROM audit_signals WHERE confidence>=0.50 AND decision_stage!='GUARDIAN_GATE'; -- 1
SELECT AVG(raw_prob_buy),AVG(raw_prob_sell),AVG(raw_prob_no_trade) FROM audit_signals
  WHERE raw_prob_buy>0;                                                            -- .387/.360/.253
SELECT reason_code, SUM(count) FROM audit_guard_telemetry GROUP BY 1;              -- 78,798 / 3,995
SELECT is_executed, COUNT(*) FROM audit_experience_outcomes GROUP BY 1;            -- 163 / 955
SELECT action, MAX(timestamp) FROM audit_orders GROUP BY action;                   -- Executed order ≤ 09-03
SELECT rule_name,is_enabled FROM trading_rules_config;                             -- 20 × 0
-- bar plane (dead subsystem)
SELECT entry_allowed, COUNT(*) FROM trade_decisions GROUP BY 1;                    -- 2,952 / 1,553
SELECT veto_rule, COUNT(*) FROM rule_vetoes GROUP BY 1;                            -- §1 #14-15
SELECT risk_allowed, COUNT(*) FROM risk_evaluations GROUP BY 1;                    -- 4,505 × 1
-- runtime truth (not repo yaml)
-- %LOCALAPPDATA%/NexusScalpEngine/databases/app_settings.db:
--   model.confidence_threshold=0.40  algo.ai_zone_confidence_threshold=0.70
--   algo.min_risk_reward_ratio=2.2   risk.max_spread_points=20
--   execution.mode=LIVE              risk.max_account_drawdown_pct=95.0
```
Logs: `grep WATCHDOG logs/warning/2026/09/2026-09-13.log` (×1,828);
`zcat logs/warning/2026/09/2026-09-11.log.gz | grep -E "ENTRY_BLOCKED|Spread exceeds|EXCESSIVE|FRESHNESS"`
→ 5×`Spread exceeds max_allowed=20 (21..35 pts)`, 1×`EXCESSIVE_MARKET_IMPACT`, 2×`BLOCKED_BY_STALE`,
6×`[ENTRY_BLOCKED] layer=RISK_ENGINE reason=RISK_EVALUATION_REJECTED`.
Consumers of candle-intel verdicts: `grep -r "entry_allowed|_last_candle_decision" src/nexus_scalp` →
writers + `bar_handler.py:40-66` capture only; no policy/execution/feature/UI read.

### Recommended (out of scope, for lane 03+) — ordered by expected funnel recovery
1. RC-3: restart/repair the MT5 tick feed (watchdog is warning-only and self-declares connected; nothing fails closed loudly). Until fixed, no gate tuning has any effect.
2. RC-1: recalibrate or re-threshold confidence to the model's measured distribution (or wire a validated calibration artifact — `risk_engine.py:519-560` already awaits one); delete the duplicate zone-quality re-check (#16) and reconcile repo yaml vs app_settings (0.35/0.60/1.8/60 vs 0.40/0.70/2.2/20).
3. RC-2: re-calibrate `spread_chop_enter/exit` to the current broker distribution (p50 ≥ $0.23), and collapse the four spread vetoes into one owner (guardian XOR risk gate).
4. RC-4: single source of truth for order frequency (make `MIN_ORDER_INTERVAL` read `cooldown_seconds`, or delete one of the two timers).
5. RC-5: decide predictive-channel authority: if PREDICTIVE_LIMIT is meant to bypass model confidence, the post-policy pair must not re-impose it; re-enable (or delete) the dead rule matrix and candle-intel connect-or-purge ruling.
