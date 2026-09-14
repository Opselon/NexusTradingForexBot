# NSE Master Wave — Coordination Matrix (live)

Wave branch: `nse/master-active-scalper-wave` (pushed; base = origin/main 9431edd2, CI 14/14 green).
Date: 2026-09-14. Coordinator: Hermes Master. Lanes run as READ-ONLY forensic subagents;
only the Master commits. Each lane writes exactly one file in this directory.

| Lane | Objective | Agent | Status | Owns file | Risk |
|---|---|---|---|---|---|
| 01 | Git/taskboard forensics, duplication matrix | wave01 | RUNNING | 01_git_master.md | read-only |
| 02 | Passivity funnel from production DBs | wave02 | RUNNING | 02_funnel_rootcause.md | read-only |
| 03 | Setup families + duplicate gate inventory | wave03 | RUNNING | 03_setup_intel.md | read-only |
| 04 | DB size/garbage/tiered retention forensics | wave04 | RUNNING | 04_db_forensics.md | mode=ro |
| 05 | Zero-to-clean-state bootstrap audit | wave05 | RUNNING | 05_zero_state.md | no artifact touch |
| 06 | News intelligence + dead news-dims probe | wave06 | RUNNING | 06_news.md | mode=ro |
| 07 | 70D train-serve parity + model economics | wave07 | RUNNING | 07_model_parity.md | read-only |
| 08 | Experience feedback loops vs paralysis | wave08 | RUNNING | 08_experience.md | mode=ro |
| 09 | Indicator canonicality UI<->backend | wave09 | RUNNING | 09_indicators_ui.md | read-only |
| 10 | Performance / bounds / hot path | wave10 | RUNNING | 10_performance.md | read-only |
| 11 | Input validation boundary matrix | wave11 | RUNNING | 11_input_validation.md | read-only |
| 12 | Client clone-and-run (Master-owned, post-reports) | master | PENDING | 12_clone_and_run.md | isolated env |
| 13 | API/service contract audit | wave13 | RUNNING | 13_api_contract.md | read-only |
| 14 | QA failure matrix (runs real suites) | wave14 | RUNNING | 14_qa_matrix.md | executes tests |
| 15 | Security / release gate | wave15 | RUNNING | 15_security_release.md | read-only |

## Master's own early evidence (2026-09-14, direct DB probes, all mode=ro)

Rejection attribution from `artifacts/audit.db.audit_signals` (1,469 signals,
2026-09-06..2026-09-11, PAPER/predictive window):

| blocked_by | count | share |
|---|---|---|
| CONFIDENCE_FAIL | 436 | 29.7% |
| REGIME_GUARDIAN | 356 | 24.2% |
| (none — passed to limit generation) | 446 | 30.4% |
| ASYMMETRIC_RR_LIMIT | 61 | 4.2% |
| ZONE_QUALITY_FAIL | 45 | 3.1% |
| SR margins (S+R) | 52 | 3.5% |
| EXPERIENCE_DEGRADED | 26 | 1.8% |
| SUITABILITY_GATE | 22 | 1.5% |
| HTF_TREND_CONFL_FAIL | 21 | 1.4% |
| EXECUTION_STATE_BLOCK | 3 | 0.2% |

Actions: NO_TRADE 1,457 / SELL_LIMIT 7 / BUY_LIMIT 5 / market orders 0.

Model probability mass (VERIFIED from rows): mean raw_prob_buy 0.36-0.38,
raw_prob_sell 0.33-0.36, raw_prob_no_trade 0.22-0.25; ALL-TIME max confidence
in the window = 0.5015 vs effective gate 0.50 (base 0.40 + range penalty 0.10).
=> Signature of classification collapse near uniform (3-class masked);
"model paralysis" (L) is the leading root-cause hypothesis, not filter wall per se.

`HIGH_SPREAD_CHOP` regime rows (356, 24%): raw probs (0,0,1.0) — guardian veto
fires BEFORE inference (policy.py:2190) — a regime classifier label that fully
disables the engine for ~1/4 of signals needs quality review.

Outcome ledger (1,118 rows, 2026-08-17..2026-09-11): 879 NOT_DISPATCHED,
63 REJECTED_UNFILLED, ~170 actually filled-closed; net realized -2,984.45 USD,
avg R -0.012, wins 77/1,118. Funnel dies overwhelmingly at signal stage.

candle_intel.db trade_decisions (4,505 bars, ends 2026-09-07, STALE vs audit.db
ending 2026-09-11 => candle-intel subsystem off/unchanged since H5 gating):
26.4% VETO:WEAK_CLOSE (INDECISION/TRAPPED_BREAKOUT), rest entry-allowed-ish.

## Convergence rules for Phase 3 fixes
- No lane edits code; Master implements after reports converge, one seam per commit.
- Files with multi-lane overlap (policy.py, live_engine.py, order_manager.py):
  single owner = Master; lanes submit file:line evidence, not patches.
- Every fix needs: RED-before probe + regression test + critical_suite entry if P0.
