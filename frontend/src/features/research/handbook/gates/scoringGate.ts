/**
 * Handbook entry — SCORING gate (chain position 6 of 6).
 * Compiled from src/nexus_scalp/research/{evidence,scoring,models}.py.
 * The composite-score mathematics has its own cross-cutting entry
 * (topic/scoring); this file covers the GATE as a chain node.
 */
import type { HandbookEntry } from "../types";

export const scoringGateEntry: HandbookEntry = {
  id: "gate/SCORING",
  kind: "gate",
  badge: "GATE 6 / 6",
  title: "Scoring & verdict",
  subtitle: "Decomposable multi-dimensional score plus the hard verdict gates that override it.",
  source: "src/nexus_scalp/research/scoring.py, evidence.py",
  seeAlso: ["topic/scoring", "gate/OOS", "gate/ROBUSTNESS", "topic/lifecycle"],
  keywords: ["scoring", "verdict", "validated", "inconclusive", "rejected", "score payload"],
  sections: [
    {
      heading: "What the gate produces",
      body: [
        "compute_strategy_score assembles every prior gate's result into one StrategyScore: ten bounded [0,1] dimensions, a weighted final in [0,1], a verdict and a reasons list (scoring.py module docstring, spec 17/18/38).",
        "The design rule is anti-single-metric: NOT a single win rate. Each dimension is visible, each weight is fixed in source, and every verdict carries the reasons that produced it — the score is fully explainable by construction.",
        "The persisted score_payload rides in strategy_intelligence_registry (the derived cache the Experience tab and registry list read), stamped with sample counts and lifecycle context.",
      ],
      bullets: [
        "Dimensions: performance, risk, stability, oos, robustness, sample, regime, recency, execution, degradation.",
        "Verdicts: VALIDATED | REJECTED | INCONCLUSIVE — each with machine-readable reasons appended by the checks below.",
        "Small-sample doctrine: 8 trades at +1.2R stays LOW EVIDENCE and never HIGH CONFIDENCE (spec 18, enforced below).",
      ],
    },
    {
      heading: "The verdict chain (order matters)",
      body: [
        "scoring.py evaluates hard gates in a fixed order before the weighted score means anything:",
        "1. OOS present and status != PASS -> REJECTED, reason OOS_FAILURE. Hard evidence against the candidate.",
        "2. n < 8 (SMALL_SAMPLE_FLOOR) -> INCONCLUSIVE. Below the absolute floor nothing is claimable.",
        "3. Missing/failed performance, OOS or robustness inputs -> REJECTED (if OOS failed) else INCONCLUSIVE.",
        "4. n < 20 (MIN_EVIDENCE_SAMPLES) -> INCONCLUSIVE with the floor quoted — never VALIDATED below the evidence floor regardless of other gates (TASK-4 hard small-sample gate).",
        "5. Walk-forward present and not passed -> INCONCLUSIVE, 'Walk-forward did not pass'.",
        "6. OOS evidence not decisive (bootstrap 95% CI not entirely above breakeven, or significance absent) -> INCONCLUSIVE with the CI quoted.",
        "7. Selection-bias control failed (DSR < 0.95 across n_trials > 1, or family Reality Check p-value) -> INCONCLUSIVE with the control's numbers.",
        "8. Otherwise -> VALIDATED, reason 'All evidence gates passed'.",
      ],
    },
    {
      heading: "Why INCONCLUSIVE dominates",
      body: [
        "Read the chain top to bottom and the epistemology is visible: REJECTED is reserved for evidence AGAINST (OOS failure); everything else that blocks VALIDATED is INCONCLUSIVE — the evidence does not yet license the claim.",
        "That taxonomy is what makes the EVIDENCE_BUILDING lifecycle track coherent: an INCONCLUSIVE candidate that failed only on sample size with positive expectancy may wait for more data (lifecycle.py PHASE 25), while a REJECTED candidate is terminal.",
        "For the operator, the practical question after a scoring run is never 'did it pass' but 'WHICH reason stopped it' — every reason string is quoted verbatim in the drawer.",
      ],
    },
    {
      heading: "VALIDATED is not ACTIVE",
      body: [
        "The scoring gate ends the research chain; it does not trade. A VALIDATED strategy enters the eligible set for SHADOW, and only an explicit operator promotion moves it further (lifecycle.py::approve_for_live — 'never automatically; no Candidate -> Auto Live').",
        "REQUIRED_GATES_FOR_VALIDATION (evidence.py) is the checklist VALIDATED stands on: BACKTEST, WALK_FORWARD, OOS, ROBUSTNESS, SCORING — all PASSED, plus a closed evidence artifact per gate (module-header invariant).",
        "The UI's Promote buttons ask for an actor id precisely because this boundary is an accountability boundary: someone's name goes on the lineage record.",
      ],
    },
    {
      heading: "Failure modes to recognize",
      body: [
        "Score present but verdict INCONCLUSIVE with a floor reason — healthy behavior, not a bug: the weights computed fine, the hard gates declined the claim.",
        "verdict REJECTED with only OOS_FAILURE — check whether the OOS run itself is stale (old dataset) before re-discovering; the rejection is real FOR THAT EVIDENCE.",
        "score_payload missing from the registry — the registry is a derived cache (experience/evaluator.py: 'holds no authoritative state'); self-heal rebuilds it from the immutable experience store.",
      ],
    },
  ],
  params: [
    {
      name: "verdict values",
      value: "VALIDATED | REJECTED | INCONCLUSIVE",
      meaning: "Claim status after all hard gates; INCONCLUSIVE = evidence not yet licensing VALIDATED.",
      ref: "scoring.py verdict block",
    },
    {
      name: "MIN_EVIDENCE_SAMPLES",
      value: "20",
      meaning: "Evidence floor — below it the verdict can never be VALIDATED.",
      ref: "models.py",
    },
    {
      name: "SMALL_SAMPLE_FLOOR",
      value: "8",
      meaning: "Absolute floor — below it the verdict is INCONCLUSIVE regardless of expectancy.",
      ref: "models.py",
    },
    {
      name: "required gates",
      value: "BACKTEST + WALK_FORWARD + OOS + ROBUSTNESS + SCORING",
      meaning: "All must be PASSED (with evidence artifacts) for VALIDATED.",
      ref: "evidence.py::REQUIRED_GATES_FOR_VALIDATION",
    },
  ],
  faq: [
    {
      q: "What is the difference between a rejected gate and a REJECTED verdict?",
      a: "A gate row FAILED is one step's result. The verdict is scoring.py's claim about the whole candidate. They usually align (OOS gate FAIL -> verdict REJECTED), but a walk-forward FAIL yields verdict INCONCLUSIVE — taxonomy, not inconsistency.",
    },
    {
      q: "My strategy scores 0.87 — is it VALIDATED?",
      a: "The weighted score never overrides the verdict chain. 0.87 with a CI-straddling OOS result is still INCONCLUSIVE. Read verdict + reasons first; the number is a ranking aid, not a license.",
    },
    {
      q: "Where does the registry number come from?",
      a: "score_payload persisted in strategy_intelligence_registry by the experience evaluator. It is a cache of derived state — authoritative evidence stays in the immutable experience/gate/evidence stores, and self-heal rebuilds the cache from them.",
    },
  ],
};
