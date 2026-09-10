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


## 6. Round-2 additions (2026-09-09, same mission continuation)

### E2 resolution — economic OOS floor (additive contract)
- New constant `MIN_ECONOMIC_OOS_EXPECTANCY_R = 0.02` in `research/oos.py`;
  the OOS gate DEFAULT is now the economic floor. Rationale: the canonical
  friction model consumes ~0.01–0.02R per trade in spread+slippage, so an
  OOS edge below 0.02R is indistinguishable from execution-noise — not a
  tradable edge.
- **Public-contract compatibility:** the legacy constant
  `MIN_OOS_EXPECTANCY_R = 0.0` is preserved and importable; an explicit
  `OOSGate(min_oos_expectancy_r=0.0)` restores the old semantics. The
  boundary rule ("never LOWER the OOS floor") is unchanged — this change
  RAISES the effective default, the documented tightening direction.
- `tests/unit/test_phase27_consistency.py` TEST E updated deliberately to
  pin BOTH constants (documented contract change, not a silent edit).

### Selection-bias control (multiplicity)
- `metrics.deflated_sharpe_ratio(r_values, n_trials, trial_sr_variance=...)`:
  Bailey–de Prado DSR per-trade semantics with higher-moment estimator
  variance; `dsr >= 0.95` is the "real after search" bar. Deterministic.
- `metrics.spa_family_pvalue(family_r_lists, n_boot, seed)`: White Reality
  Check — bootstrap p-value that the BEST mined family's mean R is luck;
  `survivor = p <= 0.05`. Deterministic via seeded RNG.
- `OOSGate.evaluate(..., n_trials=None, family_r_lists=None)` attaches
  `OOSResult.deflated_sharpe` / `OOSResult.spa` when the caller declares the
  multiplicity (fields stay None otherwise — no fabricated metrics).
- `pipeline.validate_candidate(..., n_trials=..., family_r_lists=...)`
  threads the multiplicity through, and `n_trials` lands on the run
  snapshot (provenance/auditability).
- `scoring._selection_bias_control_passed` (+ `DSR_CONFIDENCE_FLOOR=0.95`):
  the verdict chain gains a hard gate — a mined candidate that fails DSR or
  SPA is INCONCLUSIVE with an explicit reason. Absent fields → legacy
  behavior.

### Round-2 evidence
- DSR sanity: strong edge (SR 0.84) → dsr 0.997 @ 200 trials (survives);
  weak edge (SR 0.11) → dsr 0.12 @ 200 trials (deflated away); monotone in
  n_trials.
- SPA sanity: real edge buried in 50 noise families → p=0.0, survivor=True;
  40 pure-noise families → p=0.367, survivor=False.
- Sub-economic gate: an OOS window with +0.01R adjusted expectancy FAILS by
  default and PASSes only under the explicit legacy 0.0 floor.
- 16 new tests in `tests/unit/test_research_selection_bias_20260909.py`.


### Round-2b — automatic multiplicity wiring in the research loop

The multiplicity controls are only effective if the loop feeds them without
operator memory. `research.worker.ResearchWorker` (the production loop) now:

  1. At discovery: captures `n_trials` = number of mined candidates, and the
     per-family per-trade R lists keyed by each candidate's context
     fingerprint (the Reality Check set).
  2. At validation: passes both into `validate_candidate`, so
     `OOSResult.deflated_sharpe` / `.spa` are populated automatically and the
     run snapshot records `n_trials` for after-the-fact audit.

A cycle over an unchanged dataset remains a no-op (the dataset rebuild guard
is untouched); the discovery -> validation boundary (spec 27) is untouched —
OOS evidence never flows back into discovery.


### Round-3 — expectancy-per-regime scoring + archive-only retention

**Regime scoring (scoring.py).** The regime dimension counted distinct
regimes (breadth only). Now `_regime_expectancy_coverage` decomposes per
regime actually traded: coverage = consistency (fraction of traded regimes
with POSITIVE mean R) x breadth (n_regimes / 8 buckets), with UNKNOWN
provenance discounted. A losing regime is penalized AND named in
score.reasons; `StrategyScore.regime_diagnostics` carries the per-regime
means. Optional field: legacy producers unchanged.

**Archive-only retention (research/archive.py + AUDIT-0009 v8->9).**
`research_events` / `research_evidence` previously grew unbounded.
`archive_research_history(conn, older_than_days=365, batch_size=5000)` moves
expired rows into `research_events_archive` / `research_evidence_archive`
(migration AUDIT-0009; same DB, same columns + archived_at). Safety contract:
the DELETE only runs after a count-verified archive copy exists; any
mismatch rolls back and deletes nothing; negative horizon refused; bounded
batch. The worker calls it once per working validation cycle. History is
never destroyed — the archive IS the history.


### Round-4 — archive-aware history surface (API)

Retention is archive-only, so the read path must treat the archive as the
history it is:

  * `ResearchObservabilityStore.list_events` / `.list_evidence` UNION live +
    archive by default (`include_archive=False` = hot-only legacy view).
    Explicit column lists keep the UNION valid (archive carries an extra
    archived_at stamp).
  * `history_counts()` exposes live vs archived row counts.
  * New endpoint `/api/research/history` (retention visibility);
    `/api/research/events` and `/api/research/evidence` gained
    `include_archive` (default true).

**Silent-loss fix found by this round:** `_archive_rows` originally copied
only row IDs into the archive — payload columns were silently lost while row
counts still matched. Round-4 tests pin FULL-ROW moves (message/content must
survive the move); the archiver now copies every live column.
