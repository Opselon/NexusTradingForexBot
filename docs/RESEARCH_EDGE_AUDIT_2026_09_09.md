# Research-to-Edge Pipeline Audit — 2026-09-09

Owner: Research & Edge Improvement Lead. Scope: the full evidence chain from
data ingestion to live feedback. Constraint honored throughout: **optimize for
durable OOS edge, not for test pass-rate or metric inflation** — zero gates
weakened, zero thresholds lowered, zero production semantics changed.

## 1. Pipeline audit (ingestion → live feedback)

| Stage | Mechanism | Verdict |
| :-- | :-- | :-- |
| Ingestion | Experience ledger (immutable, idempotency-keyed) | SOUND |
| Dataset build | `ResearchDatasetBuilder` — evidence census + causal ordering | SOUND |
| Feature schema | `scalp_v1`/70D contract with hashes stamped per sample | SOUND |
| Splitting | `split_temporal` purge 300s / embargo 60s defaults (BUG-140/183) | SOUND |
| Backtest | `compute_backtest` friction-capped 0.5R, deterministic | SOUND |
| Walk-forward | fold PASS requires val>0 AND oos>=0 (BUG-244) | SOUND |
| OOS gate | hard gate, empty-population refusal (PHASE 27) | SOUND, see §3 |
| Robustness | 6 stress scenarios, degradation ceiling 0.25R | SOUND |
| Scoring | decomposable, small-sample floor 8 / evidence floor 20 | see §3 |
| Promotion | champion/challenger with paired bootstrap (PHASE 7B) | SOUND |
| Provenance | run snapshots + gates + events + evidence, append-only | SOUND |

## 2. Root causes found (ranked by edge damage)

1. **E1 — No statistical significance at strategy level.** The OOS gate and
   the `VALIDATED` verdict operated on point estimates: +0.01R over 10 OOS
   trades passed. (Champion/challenger already had a bootstrap gate; the
   strategy pipeline did not.)
2. **E2 — Breakeven OOS counts as evidence.** `MIN_OOS_EXPECTANCY_R = 0.0`
   means a 0.00R OOS window PASSES the hard gate; scoring then treats it as
   evidence. A breakeven OOS is not live-survivable edge.
3. **P1 — O(n²) equity curve** in `compute_sized_economic_pnl`
   (`sum(prefix)` per element): 5k trades ≈ 60ms wasted, 50k trades ≈ 6s.
4. **P2 — RiskEngine rebuilt per trade** inside the sized re-valuation
   (5k constructions per backtest).
5. **DB1 — Missing indexes on the observability hot path** (EXPLAIN QUERY
   PLAN: TEMP B-TREE ORDER BY spills on `research_gates` (strategy+run),
   `research_runs` (strategy, executed_at), `research_evidence`
   (strategy, created_at, evidence_id)). These queries run on EVERY
   validation run and every forensic/API replay.

Non-findings (checked, deliberate, left alone): robustness intentionally
stress-tests the full family population (PHASE 27 scoped only BACKTEST+WF+OOS
— commit d9e8ee68); all four engines share one friction baseline
(`default_costs` wiring), so degradation is measured against a consistent world.

## 3. Changes made

- **OOS bootstrap significance** (`metrics.oos_significance`): deterministic
  seeded CI for mean OOS R; `decisive` = n ≥ 12 AND ci_low > 0. `OOSGate`
  attaches it to every result (per-trade R recovered exactly from the equity
  curve — no re-simulation). `scoring` verdict: `VALIDATED` now requires
  decisive OOS evidence; noisy OOS (CI straddling 0) → `INCONCLUSIVE` with an
  explicit reason. Legacy `OOSResult` producers without the field keep the
  old contract (additive, provenance-preserving).
- **P1/P2 perf fixes**: linear equity curve + shared RiskEngine. Identical
  outputs (volumes, sized R, equity curve verified trade-by-trade in tests).
- **AUDIT-0008 migration** (audit domain v7→8): three evidence-based indexes,
  idempotent, table-aware on fresh baselines, `ANALYZE` refresh, rollback
  drops only the new indexes. Full migration-safety checker green.

## 4. Evidence

- End-to-end smoke (real ledger, 260 trades): VALIDATED, score 0.7271,
  OOS +0.239R, significance n=52 CI [0.010, 0.435] decisive.
- Performance: sized re-valuation 205→87ms; full backtest run 144→69ms
  (5k trades) — 2.1× overall.
- Tests: `test_research_edge_hardening_20260909.py` (11) +
  `test_research_loop_smoke.py` (1) + full research/DB battery green.

## 5. Remaining weaknesses (next highest-value first)

1. **E2 unresolved**: breakeven OOS still PASSES the gate itself (only the
   verdict layer blocks promotion). The gate threshold is a public contract
   consumed by tests/docs — tightening requires a coordinated decision.
2. **Regime coverage** is scored as distinct-regime count / 8 — a heuristic,
   not a per-regime expectancy decomposition (model_generation has
   `evaluate_regime_performance` but the strategy pipeline never calls it).
3. **No Deflated Sharpe / SPA-style multiple-testing control** when many
   candidates are mined from one dataset (selection bias across the registry
   is uncorrected).
4. Research tables grow unbounded; evidence + events have no retention policy
   (history is never destroyed — needs an archival contract, not deletion).
5. Heavy gates (WF×folds×stress) are sequential; the fold loop is
   parallelizable (no shared state) once the box allows threads.
