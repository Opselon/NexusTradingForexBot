# TRADE AVAILABILITY FORENSIC FINAL REPORT

**Task:** Deep forensic log analysis - why does the bot not trade?
**Agent:** Hermes-Forensic-02 (deep runtime forensics)
**Date:** 2026-08-19
**Evidence sources:** `artifacts/logs/nse_live.log` (2256 lines), `artifacts/audit.db` (read-only), `app_settings.db` (read-only), configs/live.yaml + base.yaml, model artifact tensors, source code (policy/intelligence/evaluator/integrity/champion/live_engine).

---

## 1. ANALYSIS WINDOW

| | |
|---|---|
| First log timestamp (host) | 2026-08-19 04:43:11 (Iran, UTC+3:30) = 01:13:11Z |
| Last log timestamp (host) | 2026-08-19 05:51:16 = 02:21:16Z |
| Total duration | 68 minutes |
| Engine restarts | 2 (session1 04:43:11, session2 05:43:22) |
| MT5 reconnects | 0 (connected immediately both sessions) |
| Model reloads | 2 |
| Configuration reloads | 2 (startup) |

**Sessions / phases:**
- **Session 1** (04:43:11-04:45:12 host, ~2 min): model `INTEGRITY_FAILURE` (BUG-110 false positive) -> `Champion unavailable`; tick stream quiet (watchdog); engine went silent/terminated.
- **Session 2** (05:43:22-05:51:16 host, ~8 min): `CHAMPION VERIFIED`; ticks flowing (radar every ~5 s), bars completing every minute; fully operational; **61 MARKET RADAR decisions, 61 NO_TRADE**.
- Startup/warmup: ~15 s per session (H1/H4 14/14 ready, feature status READY).
- **Operational trading time: session2 only** (session1 never reached inference). All zero-trade analysis below refers to the 61 evaluations of session2 (+2 startup rows in DB).

## 2. RUNTIME STATUS

- MT5 connected: YES (both sessions, login 10011755849, balance 33507.09).
- Ticks: flowing in session2 (radar cadence ~5 s); session1 quiet (1 watchdog note).
- Bars: 8 completed M1 bars in session2 (01:14-02:20Z), each triggering liquidity calc + policy eval.
- Inference events: 63 (61 logged radar + 2 startup DB rows). All NO_TRADE.
- Workers: ACCOUNTING (16 cycles, healthy), INTELLIGENCE (16 cycles, healthy), RESEARCH (8 cycles, work_done only cycle 1), TRAINING/SHADOW (0.0 ms cycles - idle), TELEGRAM (delivered 3).

## 3. MODEL

- **Artifact:** `artifacts/models/scalp/XAUUSD/v1.0.0/model.pt`
- **Identity:** primary_scalp_scalp_v1_50d, version v1.0, fingerprint `9105cef7d93e23b8` (matches log).
- **Tensors verified:** `input_projection.weight (128, 50)` = 50-dim input -> 128 hidden; `classifier.weight (4, 32)` = **4 classes**; scaler mean/std (50,). Hash matches.
- **State:** LOADED, VALID, COMPATIBLE, ACTIVE.
- **Session1 anomaly:** `INTEGRITY_FAILURE actual_classes=128 expected_classes=4` - **FALSE POSITIVE (BUG-110)**. HEAD `integrity.py` reads `input_projection.weight[0]` (hidden width 128) as the class count. The working tree contains a parallel agent's fix (reads `classifier.weight` -> 4) with regression tests `test_aihub_01..13` (40 tests green). Session2 ran the fixed code and verified the Champion.
- **70D state:** `scalp_v3` (70D) is registered in the schema registry but **NOT active**; no 70D artifact exists. Runtime uses scalp_v1 50D everywhere (experiences schema_distribution `{'scalp_v1/50D': 231}`). UI == backend == registry == runtime (all 50D v1.0.0).

## 4. FEATURE PIPELINE

- total_features=50, valid=31-35, fallback=15-19, **invalid=0**, htf_fallbacks=0, status=READY.
- Liquidity: `FEATURE_CALCULATION_OK source=UNAVAILABLE` every bar (liquidity disabled by config; governor reports UNAVAILABLE but never blocks).
- Schema: scalp_v1 50D for every evaluation. No 60D/70D vector ever produced.

## 5. SIGNAL FUNNEL (exact counts, DB window 01:13-02:21:30Z)

| Stage | Count |
|---|---|
| Market evaluations | 63 |
| Feature-ready evaluations | 63 |
| Model predictions | 63 |
| NO_TRADE | 63 |
| WAIT | 0 |
| BUY / SELL (market/stop) candidates | 0 |
| BUY_LIMIT candidates (predictive OB) | 3 |
| SELL_LIMIT candidates | 0 |
| Policy accepted | 0 |
| Policy rejected | 63 |
| Risk accepted / rejected | 0 / 0 |
| Exposure rejected | 0 |
| Execution attempted | 0 |
| Broker accepted / rejected | 0 / 0 |
| Filled / cancelled | 0 / 0 |

## 6. POLICY FUNNEL (rejection reasons)

| Reason family | Count | % | First | Last |
|---|---|---|---|---|
| ZONE_QUALITY_BELOW_THRESHOLD (< 0.60) | 34 | 53.97% | 01:14:10Z | 02:22:25Z |
| INSUFFICIENT_CONFIDENCE (< 0.35/0.25) | 14 | 22.22% | 02:15:04Z | 02:22:48Z |
| REGIME_*_NO_TRADE (no candidate) | 6 | 9.52% | 01:14:10Z | 02:23:05Z |
| ASYMMETRIC_RR_BELOW_CONFIGURED_THRESHOLD | 4 | 6.35% | 02:15:08Z | 02:23:00Z |
| PREDICTIVE_OB_BUY_LIMIT_EQUILIBRIUM -> EXPERIENCE_INTELLIGENCE_GATE (DEGRADED) | 3 | 4.76% | 02:17:00Z | 02:24:00Z |
| BUY_REJECTED_SR_RESISTANCE_MARGIN_FAIL | 2 | 3.17% | 01:14:54Z | 02:23:09Z |

Decision stages: ZONE_QUALITY_GATE 34, CONFIDENCE_GATE 14, STANDARD_EVAL 10, SR_MARGIN_FILTER 2, EXPERIENCE_INTELLIGENCE_GATE 3.
Blocked_by: ZONE_QUALITY_FAIL 34, CONFIDENCE_FAIL 14, ASYMMETRIC_RR_LIMIT 4, EXPERIENCE_DEGRADED 3, SR_RESISTANCE_MARGIN_FAIL 2, (none) 6.
All 30 DB trading rules DISABLED - RuleMatrix contributed nothing.

## 7. RISK FUNNEL

RiskEngine saw **zero candidates** - it never rejected anything. `risk_allowed` never evaluated.

## 8. EXPOSURE

Internal positions 0, internal pending 0, broker positions 0, broker pending 0. Startup reconciliation `pending_internal=0 pending_broker=0 mismatch=False`. **No exposure gate ever triggered.** Stale-exposure class of bug NOT present.

## 9. EXECUTION / BROKER

- Execution attempts in live window: **0**. No candidate survived policy+experience gates.
- The 9 `audit_executions` rows in the window are unit-test artifacts (`test_req_*`, price 2000.0) - not live.
- Broker: 3635 closed trades (historical; latest close 2026-08-18T20:05:04Z), 0 open, 0 pending, 0 rejected. The 7516 deals / 9634 orders / 3639 trades in the history sync are historical records, NOT live executions.

## 10. CONFIGURATION (effective)

| Parameter | Effective | Source |
|---|---|---|
| confidence_threshold | 0.25 | live.yaml |
| active threshold (ranging) | 0.35 | 0.25 + 0.10 range penalty |
| zone quality threshold | 0.60 | base.yaml algo |
| min R:R | 1.8 | base.yaml algo |
| risk per trade / max positions / max lots | 0.5% / 1 / 2 | live.yaml |
| liquidity_features_enabled | false | live.yaml |
| news enabled | true (no blocks) | base.yaml |
| execution mode | LIVE | live.yaml |
| trading rules | all 30 disabled | DB |
| experience gate | enabled, min qualify 0.40, degraded x0.70 | code defaults |

**No settings-DB / env / tuner / hot-reload overrides.** app_settings.db holds only `telegram.enabled`. UI == runtime (no AI-Hub divergence).

## 11. WORKERS

All RUNNING. ACCOUNTING/INTELLIGENCE productive; RESEARCH idle after cycle 1 (dataset unchanged); TRAINING/SHADOW idle (0.0 ms - no work); TELEGRAM delivered. No worker failure.

## 12. LOG vs DATABASE RECONCILIATION

| Metric | Logs | DB | Delta | Explanation |
|---|---|---|---|---|
| Signals | 61 | 63 | +2 | DB includes 2 session1 startup evals before first radar line |
| NO_TRADE | 61 | 63 | +2 | same |
| BUY/SELL | 0 | 0 | 0 | - |
| Policy rejects | 61 | 63 | +2 | same |
| Risk/exposure/execution/broker/fills | 0 | 0 | 0 | - |
| PRE_TRADE experience rejects | 3 | 3 | 0 | exact match |

No unexplained deltas. DB confirms logs.

## 13. ROOT CAUSE

**PRIMARY: POLICY_OVER_REJECTION / NO_EXECUTABLE_SIGNALS (session2).**
Every one of the 63 evaluations was rejected before Risk/Execution. Two stacked, intentional gates are responsible:
1. **ZONE_QUALITY_GATE (34 rejects, 54%)**: `ai_zone_confidence_threshold=0.60` rejects candidates whose confidence equals the model's raw directional probability (0.36-0.60 range in a ranging regime). A 2026-08-18 "trade quality fix" made `cand_confidence = raw model probability` (no floor), so the 0.60 zone-quality gate is unreachable in normal ranging conditions (only 2/63 evaluations reached 0.60+).
2. **CONFIDENCE_GATE (14) + ASYMMETRIC_RR (4) + REGIME_NO_TRADE (6)**: same root - raw probabilities 0.02-0.68, threshold 0.25/0.35.
3. **EXPERIENCE_INTELLIGENCE_GATE (3)**: the ONLY BUY_LIMIT candidates ever produced (predictive OB equilibrium) were killed because their strategy family is `DEGRADED` (recent_expectancy_r < 0) -> `confidence * 0.70 < 0.40` qualify floor. **Feedback trap**: the DEGRADED family never gets trades -> never accrues new experiences -> stays DEGRADED forever, permanently blocking the only candidate type that fires.

**SECONDARY (session1): MODEL_INVALID false positive (BUG-110).**
Session1 never traded because the verifier misread the class count (128 hidden width vs 4 classes) and declared the Champion invalid. The working tree already contains the fix + regression tests (uncommitted, owned by a parallel agent).

**Classified:** MODEL_INVALID (session1, bug - already fixed in tree) + POLICY_OVER_REJECTION / NO_EXECUTABLE_SIGNALS (session2, intentional gates) + LEGITIMATE_NO_SIGNAL (ranging regime, weak probabilities).
**FINAL ANSWER: MULTIPLE_ROOT_CAUSES**

## 14. BUGS (proven)

1. **BUG-110** (session1): `integrity.py` class-count misread -> false INTEGRITY_FAILURE on every valid ScalpNet artifact -> Champion unavailable -> no model in runtime. Fix + regression tests exist in the dirty working tree (parallel agent); not yet committed. NOT my commit to make (conflict risk).
2. **SSE datetime serialization** (12 errors): `event_generator` `json.dumps(payload)` crashes on datetime in liquidity pools payload (web/server.py:6039) - the working tree contains the ISO-string fix. Dashboard-only; does NOT affect trading.
3. **Registry pollution**: 10 `REGISTRY_RECONCILED` governance events recorded pytest temp paths as the live Champion (02:12:08-12Z), corrected by live sync 02:13:25Z. Cosmetic; does not affect trading.

## 15. FIXES (this agent)

None applied - all proven bugs already have fixes in the dirty working tree owned by parallel agents, and the remaining gate behavior is intentional (do NOT weaken thresholds per task rule 40/38). This report documents the evidence; the BUG-110 fix must be committed by its owner (see handoff).

## 16. TESTS

- `test_model_lifecycle_phase10.py`: 40 passed (incl. new BUG-110 regressions test_aihub_01..13).
- `test_experience_intelligence.py` + `test_intelligence_phase09.py`: 73 passed.
- No new tests written by this agent (no code changed - forensic-only task).

## 17. FINAL ANSWER

```
MULTIPLE_ROOT_CAUSES
```

- Session 1 (04:43-04:45): **NO_TRADE_IS_CAUSED_BY_MODEL** - BUG-110 false INTEGRITY_FAILURE (verifier bug, not model defect; fixed in working tree).
- Session 2 (05:43-05:51): **NO_TRADE_IS_CORRECT** relative to the configured gates - 100% of evaluations rejected by intentional policy/zone-quality/confidence gates plus the experience-intelligence DEGRADED gate. The model was loaded and predicting (raw probs 0.02-0.68); the market state (ranging) + strict 0.60 zone-quality gate + 0.40 experience qualify floor made executable signals impossible by design.
- No execution, exposure, broker, tick-pipeline, worker, or configuration-override defect found.

**First point where a trade becomes impossible:**
- Session1: model load (verifier bug) - before inference.
- Session2: policy ZONE_QUALITY_GATE / CONFIDENCE_GATE for standard evals; EXPERIENCE_INTELLIGENCE_GATE for the only BUY_LIMIT candidates.

**Recommended follow-up (no behavior change):** commit the BUG-110 fix (owner: parallel agent), consider whether the 0.60 zone-quality floor should be re-calibrated against raw model probabilities (product decision, NOT a bug fix), and break the DEGRADED feedback trap (e.g. allow probationary placement or decay the penalty) - product decision.
