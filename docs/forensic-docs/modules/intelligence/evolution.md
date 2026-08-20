# src/nexus_scalp/intelligence/evolution.py

- PURPOSE: Controlled, evidence-based strategy variation discovery — the
  system grows by Historical Experience → Pattern Discovery → Candidate →
  Backtest → Validation → Memory (operator-promoted), never by blindly minting
  new signals.
- ARCHITECTURE LAYER: Application (derived intelligence over the experience
  ledger; persists candidates via the audit queue).
- RESPONSIBILITY (docstring lines 6-17): a discovered candidate is a hypothesis
  with supporting evidence; it NEVER affects live trading until backtested and
  validated; promotion to real strategy memory is a separate, explicit,
  operator-gated action. SAFETY: the engine holds no adapter, no order manager
  and no risk engine — it reads the experience ledger and writes candidate
  rows only.
- DEPENDENCIES: `audit_repository.AuditRepository`,
  `experience.ledger.ExperienceLedger`, `intelligence.models`
  (EvolutionCandidate, EvolutionStatus), stdlib (hashlib, json); numpy imported
  lazily inside `_discover_from_family` (avoid numpy at import time); store.py
  (get_candidate reads).
- CONNECTS TO: worker.py (`_refresh_evolution` → scan each worker cycle),
  store.load_evolution_candidates (read facade), operator tooling that
  promotes candidates (external to this module).
- KEY CONCEPTS:
  - Constants: MIN_SCAN_SAMPLES=12 (history floor per family),
    MIN_QUALITY_GAP=0.20 (material quality shortfall to hypothesize on),
    MIN_BACKTEST_SAMPLES=20 (validation floor).
  - `scan` (lines 83-108): idempotent, bounded discovery pass — for every
    strategy family with ≥12 CLOSED executed experiences, `_discover_from_family`
    proposes at most ONE candidate; persistence is upsert-by-candidate_id so
    re-running converges.
  - `_discover_from_family` (lines 110-161): averages the 5 decomposition
    quality dimensions into "shortfall" axes —
    shortfall = 0.5 − (q+1)/4 ∈ [0,1] (0 at q=+1, 0.5 at q=−1, 0.25 at q=0);
    picks the WEAKEST dimension; gap < 0.20 → no candidate. Candidate id =
    `cand_<sha256(strategy_id|dimension)[:12]>` — deterministic per
    (family, dimension), so scanning twice never mints two candidates for the
    same gap. Status starts BACKTESTING.
  - `_hypothesis_for` (lines 163-198): evidence-backed hypothesis text +
    parameter_delta per dimension:
    - management → tighter trailing, protection_trigger_r 0.6
    - exit → zone_exit with capture_floor 0.35
    - entry → extra confluence token
    - execution → tighter_slippage, max_slippage_r 0.15
    - strategy (default) → narrower context gate (regime/session gating).
  - `validate_candidate` (lines 204-239): records a backtest result; VALIDATED
    iff backtest_expectancy_r > 0.0 AND backtest_sample_count ≥ 20, else
    REJECTED. Validation only earns the right to be CONSIDERED — never live.
    Upserts; returns updated candidate or None.
  - `persist` (lines 245-273): upsert on candidate_id — ON CONFLICT DO UPDATE
    SET status/backtest fields/validated_at/payload (discovered_at preserved).
  - `get_candidate` (lines 275-294): reads via store.load_evolution_candidates
    (bounded 500) and scans for the id — O(n) in candidates, including a
    `json.loads(r["payload"])` whose result is DISCARDED (lines 281, dead
    read); reconstructs the model from row columns (validated_at ignored).
- HOT PATH / PERFORMANCE: worker cycle only (30s interval); scan is bounded by
  per_strategy_limit=500 and the family list; numpy import deferred.
- EDGE CASES & PITFALLS:
  - `get_candidate` does a wasteful full table scan of up to 500 rows per
    lookup (no indexed PK read) plus a dead json.loads — minor, worker-path
    only.
  - Candidate `discovered_at` default now(UTC) — re-persisting an existing
    candidate via ON CONFLICT preserves the FIRST discovered_at (not updated
    in the UPDATE clause), so dedup semantics are correct.
  - `closed[0].symbol/timeframe` serve as the candidate's symbol/timeframe
    (line 138-139) — families are keyed by strategy_id which embeds symbol, so
    closed[0] is representative; safe in practice.
  - The scan does NOT re-validate previously REJECTED candidates (no retry/
    re-open path) — a candidate rejected by one backtest is dead unless
    promoted/recreated externally; documented limitation.