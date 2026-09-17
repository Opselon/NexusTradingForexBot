# TASK-010 — 50D vs 70D Production Feasibility Audit & Migration Roadmap

Priority: P2
Status: HUMAN DECISION REQUIRED
Type: Architecture & Feature Engineering
Dependencies: TASK-001, TASK-002
Blocks: None
Human Decision Required: YES (Approve migration criteria and timeline for adopting 70D as live Champion schema)
Risk: High (Affects feature pipelines, real-time news/liquidity dependencies, and model weights)
Estimated Scope: Feasibility audit report, mock live feed test, migration checklist (~150 LOC)

## Objective
Audit the operational stability of real-time News (features 50-59) and Liquidity (features 60-69) feeds, establishing the formal gating criteria required before 70D (scalp_v3) can replace 50D in live trading.

## Problem / Why
70D feature architecture exists in schema contracts and research hooks (Runtime70Hook), but cannot run in live production due to hardcoded 50D schema checks, missing 70D Champion bundles, and strict dependency on liquidity_governor.causal_state() == VALID.

## Current Evidence
- **Path:** `src/nexus_scalp/features/schema.py` (Lines: `12`)
  - **Symbol:** `ACTIVE_SCHEMA_ID`
  - **Behavior:** Hardcoded to 'scalp_v1' (50D)
  - **Classification:** `PRODUCTION`
  - **Confidence:** 100%
  - **Contradiction:** Docs claim 70D is active
- **Path:** `src/nexus_scalp/features/runtime70.py` (Lines: `35-90`)
  - **Symbol:** `Runtime70Hook`
  - **Behavior:** Observability-only hook; explicitly does not emit trade decisions
  - **Classification:** `OBSERVABILITY`
  - **Confidence:** 100%
  - **Contradiction:** None
- **Path:** `src/nexus_scalp/application/live/inference.py` (Lines: `81-88`)
  - **Symbol:** `InferenceService.validate_70d`
  - **Behavior:** Checks liquidity_governor.causal_state() == VALID; fails loud if invalid
  - **Classification:** `PRODUCTION GATING`
  - **Confidence:** 100%
  - **Contradiction:** None

## Scope
1. Audit real-time feed uptime for News and Liquidity engines over 72 hours of simulated live market ticks.
2. Quantify failure rate of liquidity_governor.causal_state() under tick spikes.
3. Formulate formal migration checklist: 1. Feed certification, 2. 70D candidate walk-forward training, 3. 70D OOS economic gate pass, 4. Shadow mode side-by-side run.
4. STOP for Human Decision: Operator approves or defers 70D live migration.

## Non-Goals
Do not switch ACTIVE_SCHEMA_ID to scalp_v3 before migration criteria are certified.

## Preconditions
TASK-001 and TASK-002 complete.

## Dependencies
TASK-001, TASK-002

## Blocks
None

## Source Areas
- `src/nexus_scalp/features/schema.py:1-30`
- `src/nexus_scalp/features/schema_contract.py:50-95`
- `src/nexus_scalp/features/runtime70.py:1-150`
- `src/nexus_scalp/features/features70.py:1-200`

## Investigation
Verify how News features (50-59) behave when external news APIs are unreachable or return HTTP 429/500.

## Implementation Plan
1. Write tests/unit/test_70d_live_feed_resilience.py simulating news API outages and stale liquidity books.
2. Measure fallback behavior: does 70D gracefully degrade or crash the tick pipeline?
3. Document findings in docs/research/70D_PRODUCTION_FEASIBILITY.md.
4. Present migration checklist to operator.
5. STOP for Human Decision: Operator decides timeline for 70D promotion.

## Tests
- `pytest tests/unit/test_70d_live_feed_resilience.py -v`

## Validation / Benchmark
Failure rate and latency of 70D feature assembly under simulated packet loss and API dropouts.

## Evidence Required
- Report: docs/research/70D_PRODUCTION_FEASIBILITY.md
- Test results from test_70d_live_feed_resilience.py
- Signed human decision on migration timeline

## Acceptance Criteria
1. 70D feed failure modes and recovery behaviors fully characterized.
2. Human decision recorded regarding 70D production roadmap.

## Failure / Abort Conditions
If 70D feature calculation adds > 50ms latency per tick, flag performance blocker and abort live migration.

## Human Stop Conditions
HUMAN STOP CONDITION: Operator must approve migration checklist and timeline.

## Expected Output
Certified 70D feasibility report and clear operator decision on migration schedule.
