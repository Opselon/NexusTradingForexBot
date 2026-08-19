# TASK-06-70D-LIQUIDITY-OPTIMIZATION — Handoff

> Agent: Hermes-LiqOptAgent6 (AGENT-06) · 2026-08-19
> Branch: `main` · Starting HEAD for evidence: `4455874` (TASK-06 began);
> parallel commits (`8792372` AGENT-10, `5bca9f3` AGENT-02) landed during the
> task; this handoff is additive on the current tree.

---

## 1. Implementation status

```text
IMPLEMENTATION_FOUND
```

TASK-01 (60D liquidity engine, committed `b91b8c9`) + TASK-02 (70D
integration `scalp_v4`/governor) + TASK-04 (validation protocol) + TASK-05
(shadow70) were present and green when TASK-6 began. TASK-03 parity was in
flight (schema decision `scalp_v3 = 70D` recorded; handoff not yet present at
TASK-6 start — AGENT-10's final forensic report (8792372) supersedes: feature
contract `news_state at 59, liquidity 60..69`).

## 2. What was optimized (evidence-based only)

### Proven defects fixed — candidate `liquidity_engine_opt.py` (v1.1)
1. **BUG-106** EQH/EQL strength ignored current price (closeness vs newest
   cluster value) → near-step feature. Fixed: `mid_price` threaded;
   closeness = 1/(1+d/ATR). Real-data: eqh median 0.879 → 0.620.
2. **BUG-107** sweep detector lacked a relevance gate (pool 200 ATR away =
   APPROACHING) → 40% of rows +1. Fixed: `SWEEP_RELEVANCE_ATR=2.0`
   (evidence: p95 nearest-pool distance 2.27 ATR); NO_RELEVANT_LIQUIDITY
   now honest (~7% of rows).
3. **Confluence quantization** (11 unique values, 34% saturation): v1.1
   score = (1+ln(N))·(1+0.5·(distinct_TF−1))·(1/(1+d_zone/ATR)) →
   saturation 4.8%, unique 2,751. Also fixed the v1 off-by-one
   `1+log1p(N)` → `1+log1p(N−1)` (single source now scores 1.0, not 1.693).

### Left UNCHANGED (robust, no evidence to justify edits — §40)
- bsl/ssl_distance_atr (ATR normalization correct; 3.0 = documented
  missing-default, NOT clipping), htf_liquidity_score (causal buckets,
  weights sensible; proximity band parameterized but default 6 ATR = v1),
  internal/external distances, post_sweep_displacement (92% zero = correct
  rare-event property), BSL/SSL ranking, H1/H4/D1 weights.

### Searchable surface added (bounded, defaults = v1)
`LiquidityParams`: eqh_tolerance_atr, confluence_cutoff_atr,
reclaim_fraction_atr, sweep_relevance_atr, htf_proximity_atr,
sweep_window_bars.

## 3. Frozen parameters (result of bounded search — LOCKED after OOS)

```text
eqh_tolerance_atr    = 0.15   (searched 0.10..0.50)
confluence_cutoff_atr= 0.65   (searched 0.40..1.10)
reclaim_fraction_atr = 0.15   (v1 default; no winning delta)
sweep_relevance_atr  = 4.0    (searched 1.0..5.0; evidence p95 dist 2.27 ATR)
htf_proximity_atr    = 6.0    (v1 default; searched 4/6/8)
sweep_window_bars    = 3      (v1 default)
```
Search discipline: coarse 12 cells + narrow 8 cells on TRAIN (8,000 rows) →
VALIDATION (4,000) selection → OOS (4,000) evaluated ONCE and LOCKED.
Scores: TRAIN 1.6850→1.8093; VALIDATION 1.7076→1.8207; OOS 1.7062→1.8178
(+6.5% OOS, consistent with validation — no overfit). Stability under ±5%
perturbation: 0.0008 mean |Δvector| (robust).

## 4. Algorithm version

```text
liquidity-v1   = committed engine (b91b8c9) — frozen GOLDEN baseline
liquidity-v1.1 = candidate (src/nexus_scalp/features/liquidity_engine_opt.py)
                 LIQUIDITY_ALGORITHM_VERSION = "liquidity-v1.1"
```
The committed `liquidity_engine.py` is UNTOUCHED. v1.1 is candidate-only and
NOT wired into live_engine/governance/shadow (TEST-LIQ-OPT-22). A model
trained on v1.1 must record `liquidity_algorithm_version=liquidity-v1.1`.

## 5. Golden dataset

- Frozen BEFORE changes: `docs/LIQUIDITY_70D_GOLDEN_BASELINE.json`
  (29,946 real M5 rows, per-feature min/max/mean/median/std/percentiles/
  zero/missing/saturation/unique + latency p50/p95/max).
- A/B diff (v1 → v1.1, §31) included in the search output
  (`scratch/liq_opt_results.json::oos_ab_diff`) and summarized in the report.

## 6. Tests

- NEW: `tests/unit/test_liquidity_optimization_phase19.py`
  (TEST-LIQ-OPT-01..28; 23 currently pass, 5 gated on search completion —
  results file + OOS numbers filled after the run).
- All existing liquidity suites stay green (60 TASK-1 + 30 TASK-2 + 52
  shadow70 + 26 TASK-4 + 9 API).
- Gate fix: `tests/conftest.py` (commit 2a30b14) registers the shadow70
  helper fixtures so pytest discovers `contract`/`tmp_artifacts`.

## 7. Parity / shadow / performance

- Batch=Live=Replay: single canonical producer; structural parity
  (TEST-LIQ-29/30, test_liq_opt_18).
- Shadow: observability-only; v1.1 not attached (Champion-independent).
- Performance (liquidity call only, real M5, 2,445 rows):
  v1 p50 3.33 ms / p95 6.18 ms / max 16.5 ms;
  v1.1 p50 3.46 ms / p95 6.61 ms / max 22.3 ms (p50 +0.13 ms; acceptable).

## 8. Registries updated

- `agents/taskboard.md`: TASK-06-70D-LIQUIDITY-OPTIMIZATION row (IN_PROGRESS
  → VERIFIED after gate).
- `agents/change_control.md`: CHG-0016.
- `agents/bugs.md`: BUG-106, BUG-107 (verified findings; append-only).

## 9. Remaining risks

- PROVEN: causality inheritance (TEST-LIQ-OPT-04), determinism, finiteness.
- NOT PROVEN: OOS predictive usefulness of v1.1 vs v1 — requires the TASK-4
  fair benchmark on a real 70D artifact (TASK-3 parity artifact still
  pending in the swarm).
- UNKNOWN: live M1 behavior of the relevance-gated sweep on real broker
  ticks (validated on real M5 bars).

## 10. EXACT NEXT-AGENT INSTRUCTIONS (TASK-7)

1. Read `docs/LIQUIDITY_70D_OPTIMIZATION_REPORT.md` FIRST, then this handoff,
   then `docs/LIQUIDITY_70D_IMPLEMENTATION_MAP.md`.
2. TASK-7 is Liquidity Feature Attribution / Regime Research: run the
   incremental-information study (Base vs Base+News vs Base+News+Liquidity)
   using the frozen **liquidity-v1.1** candidate ONLY if the OOS numbers in
   the report were accepted; otherwise default to **liquidity-v1** (the
   committed engine) — do NOT mix versions.
3. Freeze the active algorithm version in `agents/contracts.md`
   (FEATURE_SCHEMA_70D row) once TASK-3 parity lands; record which
   `liquidity_algorithm_version` produced the dataset used for research.
4. Do NOT tune parameters on OOS; new experiments need a new version id.
5. The swarm tree is shared: do not reset/stash unknown WIP; verify
   `git diff --cached --name-only` before any commit.
6. If the TASK-4 benchmark shows v1.1 does NOT add OOS information, the
   correct outcome for the optimization task is "UNCHANGED wins" — do not
   force v1.1 into production; keep it as a documented candidate.