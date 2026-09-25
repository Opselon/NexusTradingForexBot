# AGENT 7 — DATA-FLOW / CONTRACT / HOT-PATH FORENSIC — FINAL HANDOFF (2026-09-05)

Agent: Agent 7 (Nexus-Main orchestrated)
Role: Data-Flow / Contract / Hot-Path Forensics
Task: user brief 2026-09-05 — trace the REAL canonical tick-to-decision pipeline and FIX every confirmed defect
Change control: CHG-0060 (registered at 9f799ea2)
Taskboard: TASK-AGENT7-DATAFLOW (FIXED)

## Branch / base / final

- Branch: main (CHG-0060 worktree was registered on agent/agent7/dataflow-forensic at
  9f799ea2 but the session's subsequent work continued on shared main per contract 53a —
  swarm policy — then taskboard disclosure was closed on main directly; no separate branch push required)
- Starting HEAD: b635a36f (TASK-AGENT15-BTREPLAY registration)
- Ending HEAD: 13aeebd2 — taskboard disclosure (push verified: origin/main == local HEAD)
- Prior Agent-7 evidence commits: 46380b8c (TDF-1..5 probes), 0b6668e3 (TDF-6..9),
  6599e4d5 (TDF-F1 test fix; in push ancestry)
- Worktree: N/A (main; never deleted another agent's worktree)

## What was proven (evidence probes under scratch/ns_agent7_*)

| Probe | Surface | Verdict |
|-------|---------|---------|
| TDF-1 | 50D contract via REAL ScalpFeatureEngine.compute_from_bars + to_tensor_input | PASS — 50 floats, finite, [-3,3], deterministic, cold-start safe, NaN sanitized, hash 235b8fccc96b7e0e stable, 70D base block == FEATURE_NAMES |
| TDF-2 | 70D assembly + InferenceValidator chain | PASS — Base 0..49/News 50..59/Liquidity 60..69 geometry exact; missing family raises; validate_70d_vector dim/hash/NaN/Inf/bounds raise; ALL 10 rejection codes REACHABLE and blocking incl. SCHEMA_HASH_MISMATCH + SCALER_MISMATCH |
| TDF-3 | ScalpNet IO + masked_softmax + confidence semantics | PASS — 2D/3D/batch shape; WAIT masked ~0 + trained-mass renormalization == 3-class softmax; _directional_confidence = max(BUY,SELL)/(BUY+SELL+NO_TRADE) with RAW fallback, never manufactures |
| TDF-4 | Hot-path stage timing (real pipeline) | PASS — features mean 4-6ms/p95 11ms; regime 0.02ms; audit queue-put 0.03ms; risk math 0.004ms; rule_matrix TTL sqlite refresh 0.05-2ms/5s — TTL-capped, INV-001 honored |
| TDF-5 | Zero-order-authority AST scan (205 files across 11 subsystems) | PASS — ZERO OrderManager/adapter/dispatch hits; streaming_replay RiskEngine = simulation math only |
| TDF-6/7 | Real end-to-end tick->50D->scaler->ScalpNet(masked)->regime->policy->risk trace | PASS — guardian fail-closed on HIGH_SPREAD_CHOP; sizing + HARD_MAX_LOTS clamp 999->1.0 lots demonstrated on trading-allowed fixture |
| TDF-9 | Contract-breaking injection matrix (14 injections) | 14/14 PASS — every failure stopped at its correct boundary (dimension, hash, NaN/Inf/bounds, news/liquidity UNAVAILABLE, 69D/71D, scaler mismatch, 70D->50D model RuntimeError, low confidence CONFIDENCE_FAIL->NO_TRADE, tier clamp, duplicate request_id idempotency, SAFE_MODE) |

## Confirmed defect FIXED

TDF-F1 — env-dependent test
`tests/unit/test_live_engine_regime_state_freshness.py::test_regime_state_max_age_sec_default_and_yaml_keys`:
`configs/live.yaml` is GIT-IGNORED, operator-owned — it can legally omit the whole `algo`
section (its current content is only `risk:`). The test asserted the key inside
`live.yaml["algo"]` -> KeyError on this host (red-before). QA TASK-NX-STP0-QA
independently flagged the same failure as this-host-only. Fix: pin default
(AlgoConfig 300.0) == tracked base.yaml (300.0) + conditional check on live.yaml
only when the `algo` section exists. Red-before/green-after, ruff clean, suite 6/6.

## Web / Telegram / audit boundaries

- /api/positions/modify and /api/positions/close are OPERATOR endpoints; they route
  through OrderLifecycleManager (Agent-11 BUG-242 fix on main, INV-004 honored) — not a bypass.
- Telegram is send-only (no getUpdates/CommandHandler/handlers found; purely an observer surface).
- Audit hot path: log_signal = queue.put_nowait with UNIQUE signal_dedup_key + guard
  telemetry aggregation; flush is a background worker with batches up to 500 +
  queue.join on close (verified in source).

## Contracts verified

INV-001 (no sync DB on tick path): verified by hot-path timing + source walk —
log_signal is queued, regime/features pure CPU, rule_matrix refresh is TTL-capped
every 5s (worst ~2ms once, negligible). Held.
INV-002, INV-003, INV-004, INV-008, INV-009, INV-010: all held
(see probes + source-trace above).

## Files changed by Agent 7 (this mission)

- tests/unit/test_live_engine_regime_state_freshness.py (TDF-F1 test fix)
- scratch/ns_agent7_* (evidence probes + TDF-F1 note; additive)
- agents/change_control.md (CHG-0060 registration — foreign WIP on
  mt5_tick_dataset/streaming_replay/oos.py not staged)
- agents/taskboard.md (TASK-AGENT7-DATAFLOW disclosure row)

## Known residuals / handed to owner

- Any per-tick synchronous DB on the live path beyond rule_matrix refresh:
  follow-up closes only if a concrete stall reproduces (currently none in evidence).
- Other agents' known open work: Agents 3/12/14/15/17/18 continue their scoped
  forensic missions per taskboard — their boundaries were respected.

## Commits (Agent 7, this mission)

- 46380b8c — TDF-1..5 real-pipeline probes (50D/70D contract, validator reachability,
  model+confidence semantics, hot-path timing, order-authority isolation)
- 0b6668e3 — TDF-6..9 real end-to-end trace + risk-boundary + confidence-gate
  boundary walk + 14-injection contract-breaking matrix
- 6599e4d5 — TDF-F1 fix: env-dependent regime-freshness test
  (red-before/green-after; live.yaml is git-ignored operator config)
- 13aeebd2 — taskboard disclosure row (pushed origin/main == HEAD, verified)
