/**
 * Handbook entry — ROBUSTNESS gate (chain position 5 of 6).
 * Compiled from src/nexus_scalp/research/{evidence,robustness}.py.
 */
import type { HandbookEntry } from "../types";
import type { ScoringTranslate } from "../scoring";

/** Identity translator (en): English fallback, no interpolation needed. */
const identity: ScoringTranslate = (_key, fallback) => fallback;

function buildrobustnessEntry(t: ScoringTranslate = identity): HandbookEntry {
  return {
  id: "gate/ROBUSTNESS",
  kind: "gate",
  badge: t("research.hb.gates.robustness.badge", "GATE 5 / 6"),
  title: t("research.hb.gates.robustness.title", "Robustness stress"),
  subtitle: t("research.hb.gates.robustness.subtitle", "Degradation under controlled punishment — not \'still profitable\', but \'still standing\'."),
  source: "src/nexus_scalp/research/robustness.py",
  seeAlso: ["gate/OOS", "topic/economics", "gate/SCORING"],
  keywords: ["robustness", "stress", "fragile", "slippage", "spread", "latency", "perturbation"],
  sections: [
    {
      heading: t("research.hb.gates.robustness.sec1_head", "The doctrine"),
      body: [
        t("research.hb.gates.robustness.sec1_p1", "robustness.py\'s opening is the clearest sentence in the research package: Robustness is NOT \'still profitable\'; it is measured as degradation under stress. A strategy that collapses (+0.44R -> -0.12R) under +1 tick slip is fragile and fails."),
        t("research.hb.gates.robustness.sec1_p2", "The engine takes the baseline backtest result and re-prices the SAME trades under six perturbations, then measures how far expectancy falls. The verdict is about the SHAPE of the decay curve, not the absolute level of any single run."),
        t("research.hb.gates.robustness.sec1_p3", "This gate exists because a strategy tuned to today\'s exact spread and fill timing is a strategy on borrowed time. Market micro-structure moves every session; robustness asks how much movement the edge absorbs before it disappears."),
      ],
    },
    {
      heading: t("research.hb.gates.robustness.sec2_head", "The stress battery"),
      body: [
        t("research.hb.gates.robustness.sec2_p1", "STRESS_SCENARIOS is an explicit list in robustness.py — six named perturbations applied on top of the baseline assumptions:"),
        t("research.hb.gates.robustness.sec2_p2", "spread_plus_1 and spread_plus_2 widen the spread cost by 1 and 2 ticks — the most common real-world deterioration for an XAUUSD scalper."),
        t("research.hb.gates.robustness.sec2_p3", "slippage_plus_1 and slippage_plus_2 add 1 and 2 ticks of fill slippage — models worse execution than the recorded baseline."),
        t("research.hb.gates.robustness.sec2_p4", "latency_plus_50ms and latency_plus_150ms delay decision-to-fill timing — models a slower path from signal to order than the one that produced the recorded trades."),
        t("research.hb.gates.robustness.sec2_p5", "Each scenario re-runs the deterministic backtest and records its own expectancy; the gate compares every scenario\'s drop against the baseline."),
      ],
    },
    {
      heading: t("research.hb.gates.robustness.sec3_head", "The fragility threshold"),
      body: [
        t("research.hb.gates.robustness.sec3_p1", "MAX_ACCEPTABLE_DEGRADATION_R = 0.25 is the headline constant: if the absolute R degradation from baseline exceeds 0.25 R under stress, the strategy is classified FRAGILE and the gate fails."),
        t("research.hb.gates.robustness.sec3_p2", "The threshold is in R, not percent, so it is scale-free: losing a quarter of an R of expectancy under stress is unacceptable regardless of whether the baseline was +0.3R or +1.2R."),
        t("research.hb.gates.robustness.sec3_p3", "A strategy at +0.30R baseline that drops to +0.04R under spread+1 has lost 0.26R — over the line — even though every stressed scenario is still positive. That is the intended reading: brittleness is caught before profitability is."),
      ],
    },
    {
      heading: t("research.hb.gates.robustness.sec4_head", "Friction semantics reuse"),
      body: [
        t("research.hb.gates.robustness.sec4_p1", "The stress engine prices friction with the SAME effective-friction function the backtest pays: min(spread_ticks + slippage_ticks, max_slippage_ticks) (robustness.py::_effective_friction). Consistency matters — a stress test using a different cost model would measure the model, not the strategy."),
        t("research.hb.gates.robustness.sec4_p2", "The E1 zero-friction guard runs here too: RobustnessEngine.evaluate refuses a zero-cost baseline unless explicitly allowed, because \'robust to zero friction\' is a meaningless claim."),
        t("research.hb.gates.robustness.sec4_p3", "Scenarios are constructor-injectable (scenarios=...) for research experiments, but the production path uses the frozen STRESS_SCENARIOS list — a gate whose punishment list can be quietly emptied is not a gate."),
      ],
    },
    {
      heading: t("research.hb.gates.robustness.sec5_head", "Reading the verdict"),
      body: [
        t("research.hb.gates.robustness.sec5_p1", "PASSED: under all six perturbations, expectancy degradation stayed within 0.25 R of baseline. The edge bends but does not break."),
        t("research.hb.gates.robustness.sec5_p2", "FAILED (RESEARCH class, terminal): at least one scenario pushed degradation past the threshold — the strategy is micro-structure fragile. Retrying will reproduce the same verdict; only a strategy definition or dataset change creates new evidence."),
        t("research.hb.gates.robustness.sec5_p3", "In scoring, robustness feeds two places: the robustness weight (0.15) in the composite score AND a hard verdict gate — if robustness is missing or not PASS, the verdict cannot reach VALIDATED (scoring.py verdict chain)."),
        t("research.hb.gates.robustness.sec5_p4", "The drawer\'s raw evidence tab will show per-scenario numbers for the gate\'s evidence artifact — quote those when arguing either direction about a fragile strategy."),
      ],
    },
  ],
  params: [
    {
      name: "MAX_ACCEPTABLE_DEGRADATION_R",
      value: "0.25",
      meaning: t("research.hb.gates.robustness.param1_meaning", "Maximum absolute R drop from baseline under stress before the strategy is FRAGILE."),
      ref: "robustness.py",
    },
    {
      name: "STRESS_SCENARIOS",
      value: "spread_plus_1, spread_plus_2, slippage_plus_1, slippage_plus_2, latency_plus_50ms, latency_plus_150ms",
      meaning: t("research.hb.gates.robustness.param2_meaning", "The frozen six-scenario punishment battery."),
      ref: "robustness.py::STRESS_SCENARIOS",
    },
    {
      name: "friction pricing",
      value: "min(spread + slippage, max_slippage_ticks)",
      meaning: t("research.hb.gates.robustness.param3_meaning", "Same cost semantics as the baseline backtest — comparability by construction."),
      ref: "robustness.py::_effective_friction",
    },
  ],
  faq: [
    {
      q: t("research.hb.gates.robustness.faq1_q", "Strategy is profitable in every stress scenario but still failed — why?"),
      a: t("research.hb.gates.robustness.faq1_a", "The gate measures degradation, not sign. A baseline of +0.44R collapsing to -0.12R under stress is a 0.56R drop — past the 0.25R fragility threshold — and profitability in the milder scenarios does not rescue it. Read the per-scenario evidence artifact for the exact drops."),
    },
    {
      q: t("research.hb.gates.robustness.faq2_q", "Which scenario failed — can I see it?"),
      a: t("research.hb.gates.robustness.faq2_a", "Yes: the gate\'s evidence artifact (evidence tab in the drawer) carries the per-scenario results the backend recorded. The failure_reason names the worst offender in the backend\'s own wording."),
    },
    {
      q: t("research.hb.gates.robustness.faq3_q", "Does a stress failure reject the strategy forever?"),
      a: t("research.hb.gates.robustness.faq3_a", "A FAILED gate terminates that run (RESEARCH class), and scoring will not claim VALIDATED without a passing robustness result. The candidate\'s lifecycle reflects the rejection; new evidence requires a new run with a changed strategy definition or dataset."),
    },
  ],
  };
}

/** Canonical English entry (identity) — TOC, search and tests consume this. */
export const robustnessEntry: HandbookEntry = buildrobustnessEntry();

/**
 * Translator-aware copy (features/research/model.ts commandVerdict pattern):
 * call during render with the store's current t(); the result changes with the
 * language — never cache it outside render.
 */
export function robustnessEntryTranslated(t?: ScoringTranslate): HandbookEntry {
  return buildrobustnessEntry(t);
}
