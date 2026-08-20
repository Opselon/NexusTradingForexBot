# src/nexus_scalp/strategies/factory/provider.py + ranking.py + summarizer.py

# provider.py
- **PURPOSE:** The strategy-factory provider registry — resolves strategy
  implementations/providers by id and mediates access to the factory's
  strategy pool (content-addressed candidates + their providers).
- **RESPONSIBILITY:** (a) provider lookup (strategy_id → implementation);
  (b) validation that a requested provider exists before orchestration
  uses it; (c) the factory's import surface for providers.
- **KEY CONCEPTS:** provider ids must match the research registry
  vocabulary; unknown ids fail loudly (never a silent no-op strategy).
- **EDGE CASES:** a provider whose module failed to import must be reported
  as unavailable (not silently skipped in ranking).

# ranking.py
- **PURPOSE:** The factory-level ranking & score engine (2026-08-20):
  multi-dimensional ranking (OVERALL / OOS / ROBUSTNESS / RISK_ADJUSTED /
  CONSISTENCY / REGIME / LOW_DRAWDOWN / HIGH_EXPECTANCY / DIVERSITY).
  The authoritative per-strategy validation score remains
  research/scoring.py (compute_strategy_score with hard OOS/robustness/
  sample gates); THIS module adds the FACTORY ranking layer: selection
  score = research score + robustness + OOS degradation + walk-forward
  consistency − complexity penalty + sample-size confidence.
- **RESPONSIBILITY:** produce the leaderboards the factory UI/Telegram
  summaries consume; deterministic ordering (stable sort + tie-breakers).
- **KEY CONCEPTS:** ranking NEVER overrides the hard gates — a strategy
  that fails OOS/robustness cannot be ranked HIGH regardless of its
  selection score (gate-then-rank).
- **EDGE CASES:** ties resolved deterministically (id alphabetical last
  resort); insufficient samples → sample-size confidence drags the rank
  down (honest uncertainty).

# summarizer.py
- **PURPOSE:** Renders factory results as human/Telegram summaries —
  converts the ranked strategy pool + per-strategy evidence into the
  concise report templates the factory Telegram reports and UI panels
  display.
- **RESPONSIBILITY:** deterministic, escaping-safe rendering (the
  telegram_html escaping discipline); bounded output (top-N, not the
  whole registry).
- **KEY CONCEPTS:** summaries carry the evidence links (OOS score,
  robustness, sample count) so a reader can verify, not just trust a
  rank.
- **EDGE CASES:** empty registry → explicit empty summary (never a fake
  "no strategies" from a crash).