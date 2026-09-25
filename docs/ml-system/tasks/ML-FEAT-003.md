# ML-FEAT-003 — 50D vs 70D Production Feasibility Audit & Gating Criteria

STREAM: STREAM B — FEATURE ENGINEERING
PRIORITY: P2
STATUS: HUMAN DECISION REQUIRED
DEPENDENCIES: ML-FEAT-001, ML-DATA-001
AGENT_ROLE: AGENT-FEATURE
OWNERSHIP_SCOPE: src/nexus_scalp/features/runtime70.py, docs/research/70D_FEASIBILITY.md
HUMAN_DECISION_REQUIRED: YES (Operator must approve 70D migration criteria)
PARALLELIZATION_CLASS: PARALLEL_SAFE

## OBJECTIVE
Audit the operational stability and latency of real-time News (features 50-59) and Liquidity (features 60-69) engines, formalizing the gating criteria required before 70D (scalp_v3) can replace 50D in live trading.

## WHY_IT_EXISTS
70D is currently blocked from live execution due to schema contracts, missing 70D Champion models, and strict dependency on liquidity_governor.causal_state() == VALID. Transitioning prematurely risks crashing the live tick loop during feed dropouts.

## CURRENT_EVIDENCE
- **Path:** `src/nexus_scalp/features/schema.py` (Lines: `12`)
  - **Symbol:** `ACTIVE_SCHEMA_ID`
  - **Behavior:** Hardcoded to 'scalp_v1' (50D)
  - **Classification:** `PRODUCTION`
  - **Confidence:** 100%
  - **Contradiction:** Docs historically claimed 70D was active
- **Path:** `src/nexus_scalp/features/runtime70.py` (Lines: `35-90`)
  - **Symbol:** `Runtime70Hook`
  - **Behavior:** Observability-only hook; explicitly does not emit trade decisions
  - **Classification:** `OBSERVABILITY`
  - **Confidence:** 100%
  - **Contradiction:** None
- **Path:** `src/nexus_scalp/application/live/inference.py` (Lines: `81-88`)
  - **Symbol:** `validate_70d`
  - **Behavior:** Checks liquidity_governor.causal_state() == VALID; fails loud if invalid
  - **Classification:** `PRODUCTION GATING`
  - **Confidence:** 100%
  - **Contradiction:** None

## FACTS
- 70D is an observability hook.
- Inference requires liquidity state VALID.
- Live production runs strictly on 50D.

## UNKNOWNs
- Real-time availability and packet dropout rate of external news/liquidity data feeds.

## SCOPE
Test 70D feature assembly under simulated packet loss; measure per-tick computation latency; document formal migration checklist; present to operator for decision.

## NON_GOALS
Do not flip ACTIVE_SCHEMA_ID to scalp_v3 before operator approves migration checklist.

## SOURCE_AREAS
- `src/nexus_scalp/features/schema.py`
- `src/nexus_scalp/features/runtime70.py`
- `src/nexus_scalp/features/features70.py`

## FILES_LIKELY_TO_CHANGE
- `tests/unit/test_70d_live_feed_resilience.py`
- `docs/research/70D_FEASIBILITY.md`

## INVESTIGATION_PLAN
Verify how 70D features behave when news API returns HTTP 429/500 or liquidity book is empty.

## IMPLEMENTATION_PLAN
1. Write tests/unit/test_70d_live_feed_resilience.py simulating API downtime.
2. Measure latency overhead of news + liquidity feature extraction (must be < 5ms).
3. Document findings in docs/research/70D_FEASIBILITY.md.
4. STOP for Human Decision: Operator approves or defers 70D production roadmap.

## TEST_PLAN
- `pytest tests/unit/test_70d_live_feed_resilience.py -v`

## BENCHMARK_PLAN
Evaluate 10,000 ticks under simulated 5% feed dropout; measure fallback correctness.

## EVIDENCE_REQUIRED
- Report: docs/research/70D_FEASIBILITY.md
- Passing unit test log
- Recorded human decision

## ACCEPTANCE_CRITERIA
1. 70D failure modes fully mapped.
2. Operator decision recorded on 70D migration schedule.

## ABORT_CONDITIONS
If 70D assembly adds > 50ms latency per tick, declare performance blocker.

## HUMAN_DECISION_REQUIRED
YES (Operator must approve 70D migration criteria)

## EXPECTED_ARTIFACTS
- `docs/research/70D_FEASIBILITY.md`
- `tests/unit/test_70d_live_feed_resilience.py`

## SHARED_FILE_RISK
Low. AGENT-FEATURE owns runtime70.py.
