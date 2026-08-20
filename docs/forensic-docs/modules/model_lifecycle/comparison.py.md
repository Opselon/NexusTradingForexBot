# src/nexus_scalp/model_lifecycle/comparison.py

- **PURPOSE:** Champion vs Challenger multi-dimensional comparison (spec
  17/18/19/38). A Challenger must NOT win on one metric: prediction quality,
  trading quality, strategy quality, robustness and stability are all compared,
  with eligibility requiring improvement WITHOUT critical degradation.
- **ARCHITECTURE LAYER:** Research/ML — evaluation/eligibility, no order
  authority.
- **RESPONSIBILITY:** Compute per-metric deltas + an explainable bounded
  improvement score; decide `eligible` on hard ceilings.
- **DEPENDENCIES:** models (ChampionChallengerComparison), logger.
- **CONNECTS TO:** orchestrator (`compare_against_champion`), gates (GATE10
  consumes eligible/reasons), store (comparison persistence).

- **KEY CONCEPTS:**
  - Hard ceilings relative to the Champion (lines 31-34): MAX_DRAWDOWN_WORSE_R =
    3.0 (challenger may be at most 3R worse in drawdown); MAX_TAIL_WORSE_ABS =
    1.0 (tail-loss count); MIN_EXPECTANCY_IMPROVEMENT_R = 0.05 (expectancy must
    not drop below champion-0.05).
  - `compare()` (line 40) rules — a candidate is INELIGIBLE when: expectancy
    ≤ 0.0 (line 65) or worse than champion by ≥ 0.05R (line 68); drawdown worse
    by > 3R (line 74); OOS negative (line 81) or degraded by > 0.5R from a
    positive champion OOS (line 84); tail losses exceed champion by > 1.0 count
    (line 89); robustness FAIL while champion PASS (line 94); stability <
    champion − 0.3 (line 99).
  - `_improvement_score` (line 136): bounded [0,1] explainable score, weights
    expectancy 50% / drawdown discipline 20% / OOS level 20% / stability 10%.
    Informational — eligibility is decided by the gates/rules, not this score.
  - Inputs are plain dicts with documented keys (expectancy_r, max_drawdown_r,
    oos_expectancy_r, tail_loss_count, robustness_status, stability,
    calibration_score, model_id, model_version) — the orchestrator currently
    supplies mostly DEFAULT/zero champion metrics (see orchestrator issue).

- **HOT PATH / PERFORMANCE:** Trivial float math; runs per validation, never on
  the tick path.

- **EDGE CASES & PITFALLS:**
  - Missing keys default to champion-favorable values (`stability` default 1.0,
    `robustness_status` default "PASS", line 55-58): a dict missing stability
    would make the CHAMPION appear maximally stable and could wrongly reject a
    challenger. Callers must supply real research metrics for both sides.
  - `tail_loss_count` compared by ABSOLUTE count, not normalized by sample size —
  - a larger challenger evaluation set could legitimately have more tail losses;
  - the comparison does not normalize for n.
  - Score uses `stab_t` raw (not delta) for the stability component (line 146) —
  - a 1.0-stable champion's advantage over a 0.7 challenger is captured, but a
  - 0.7 challenger vs 0.6 champion yields a low component despite improvement.