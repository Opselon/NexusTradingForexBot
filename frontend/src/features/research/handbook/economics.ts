/**
 * Handbook — economics, friction, and degradation doctrine (cross-cutting).
 * Compiled from src/nexus_scalp/research/{robustness,oos,splitting,economics,metrics}.py.
 * Constants live where they are enforced: robustness.py (degradation line),
 * oos.py (floors/ceiling) and splitting.py (purge/embargo defaults) — the
 * regression test re-reads each of those files and fails on drift.
 */
import type { HandbookEntry } from "./types";

/** Mirrors robustness.py / oos.py / splitting.py (see test for each source). */
export const MAX_ACCEPTABLE_DEGRADATION_R = 0.25;
export const MIN_ECONOMIC_OOS_EXPECTANCY_R = 0.02;
export const MAX_OOS_DEGRADATION = 1.0;
/** Mirrors splitting.py defaults (seconds). */
export const DEFAULT_PURGE_SECONDS = 300;
export const DEFAULT_EMBARGO_SECONDS = 60;

export const economicsEntry: HandbookEntry = {
  id: "topic/economics",
  kind: "topic",
  badge: "E1 GUARD · DEGRADATION 0.25R",
  title: "Economics — friction, degradation, splits, stress",
  subtitle: "What 'production-like' means numerically: cost provenance, the zero-friction guard, the 0.25R rule, purge/embargo.",
  source: "src/nexus_scalp/research/{economics,metrics,robustness,splitting,models}.py",
  seeAlso: ["gate/BACKTEST", "gate/ROBUSTNESS", "gate/OOS"],
  keywords: ["friction", "e1", "zero friction", "econ", "degradation", "purge", "embargo", "stress", "spread", "slippage", "latency", "swap"],
  sections: [
    {
      heading: "E1 — the zero-friction guard",
      body: [
        "backtest_economics.py raises ZeroFrictionError on mixed or unset friction provenance: any 'zero friction' study must be an INTENTIONAL, exclusively zero-friction study — never an accident and never mixed with production costs (module header).",
        "The guard exists because a zero-cost backtest quietly inflates expectancy: spreads, swaps and slippage are the difference between a research artifact and a tradable claim. Accidental zero-cost runs are the failure mode E1 exists to stop.",
        "A study's provenance classifies EVERY assumption: canonical costs (production values from configs/execution_assumptions.json) or fallback zero (refused unless explicitly allowed). Mixed provenance = refuse, with reasons, rather than average the two truths.",
      ],
    },
    {
      heading: "Cost provenance — where numbers come from",
      body: [
        "configs/execution_assumptions.json is the canonical cost source: spread, commission/fees, swap, slippage defaults the production executor actually pays. Its 'provenance' key records who published it and when — costs carry receipts too.",
        "Fallback-zero is the refusal path: when canonical costs are unavailable and zero is not explicitly allowed, the run fails instead of silently pricing the world at free (ECON regime in economics.py docstring).",
        "ECON v1 canonical labels (economics.py): 'ECON/PRODUCTION-LIKE - ECON V1' for the production-like engine with 'required-swap validation' and 'sized economic re-valuation' — conservative evidence-derived friction applied and re-sized against real economics.",
        "Legacy ExecutionAssumptions paths keep the 'FRICTIONLESS_RESEARCH - LEGACY' label: they are allowed only as explicitly-frictionless research, never as production evidence (task.md 68).",
      ],
    },
    {
      heading: "Degradation — the 0.25R line",
      body: [
        "MAX_ACCEPTABLE_DEGRADATION_R = 0.25: robustness measures how far stressed expectancy falls BELOW baseline; a drop beyond 0.25R classifies the strategy FRAGILE (docs/gates.md).",
        "Degradation is relational, not absolute: a strategy at +0.60R baseline falling to +0.35R under spread+2 has crossed the line; one at +0.20R falling to +0.10R has not — but the second's thin baseline is its own problem at the OOS floor.",
        "The degradation dimension of the score (weight 0.02) records the signal numerically even when the gate verdict decides the claim — the dashboard shows the drop, the verdict says what it cost.",
        "Two ceilings coexist: the 0.25R robustness line (stress-vs-baseline) and MAX_OOS_DEGRADATION=1.0 (OOS-vs-baseline relative collapse — expectancy wiped out entirely). Different comparisons; both call a strategy degraded.",
      ],
    },
    {
      heading: "OOS economics floors",
      body: [
        "MIN_ECONOMIC_OOS_EXPECTANCY_R = 0.02: the default economic floor — out-of-sample expectancy must clear +0.02R, not merely zero (oos.py).",
        "Legacy floor MIN_OOS_EXPECTANCY_R = 0.0 remains for explicitly-legacy configurations that set zero: 'the legacy floor stays zero only for configs that explicitly set zero'.",
        "MAX_OOS_DEGRADATION = 1.0: relative ceiling — OOS expectancy may fall hard, but if it falls by more than 100% of baseline (i.e. inverted), the candidate is dead regardless of other dims.",
        "These are the DEFAULTS; a config may set different numbers and the config WINS. What the backend reports (gate metrics, scoring reasons) is what happened — this page documents the shipped defaults, not a specific deployment.",
      ],
    },
    {
      heading: "Purged + embargoed evaluation",
      body: [
        "spec 35/36: every split is purged with an embargo so today's labels cannot leak into adjacent train/validation slices — leakage that would let the model peek at the future through label overlap.",
        "splitting.py defaults: DEFAULT_PURGE_SECONDS=300 (5 minutes around the split boundary) and DEFAULT_EMBARGO_SECONDS=60 (1 minute of embargo beyond purge).",
        "Purge removes samples NEAR the boundary whose label windows straddle it; embargo withholds post-boundary samples from training so serial correlation in adjacent bars cannot smuggle the future backward.",
        "Where enforced: walk-forward folds (split_temporal with embargo), OOS slicing, and the offline replay validation gate — same splitter, same defaults, everywhere a boundary is drawn.",
      ],
    },
    {
      heading: "The six-scenario stress battery",
      body: [
        "robustness.py builds the scenario table by perturbing execution costs on the SAME trades: spread_plus_1 / spread_plus_2 (ticks added to entry cost), slippage_plus_1 / slippage_plus_2 (slippage units added), latency_plus_50ms / latency_plus_150ms (fill timing degraded).",
        "Each scenario re-prices the identical trade list — deterministic, comparable, no re-discovery of trades under stress (apples-to-apples is the whole point).",
        "Degradation per scenario = baseline expectancy − stressed expectancy; the WORST scenario drives the FRAGILE/RESILIENT classification against the 0.25R line.",
        "Why these six: they are the costs an executor actually overpays versus the model — wider spread, worse fill, slower reaction. Stress that never maps to a real cost is theater.",
      ],
    },
    {
      heading: "One trade, one economic observation",
      body: [
        "One economic trade = one economic observation (discovery.py header): executed AND closed outcomes only, deduplicated by idempotency key, fills never re-counted.",
        "This underwrites every R-number above: expectancy is per-observation, sample floors count observations, and duplicates cannot inflate either.",
        "Sized economic re-valuation (ECON v1) re-prices the sized result against the economic assumptions after sizing — risk applied AFTER costs are honest, not before.",
      ],
    },
  ],
  params: [
    {
      name: "MAX_ACCEPTABLE_DEGRADATION_R",
      value: "0.25",
      meaning: "Stress-vs-baseline drop that classifies a strategy FRAGILE.",
      ref: "robustness.py / docs/gates.md",
    },
    {
      name: "MIN_ECONOMIC_OOS_EXPECTANCY_R",
      value: "0.02",
      meaning: "Default economic OOS floor (+0.02R, not zero).",
      ref: "oos.py",
    },
    {
      name: "MAX_OOS_DEGRADATION",
      value: "1.0",
      meaning: "Relative OOS collapse ceiling — beyond 100%, dead.",
      ref: "oos.py",
    },
    {
      name: "DEFAULT_PURGE_SECONDS",
      value: "300",
      meaning: "Purge window around every split boundary.",
      ref: "splitting.py",
    },
    {
      name: "DEFAULT_EMBARGO_SECONDS",
      value: "60",
      meaning: "Embargo beyond purge — no post-boundary samples in training.",
      ref: "splitting.py",
    },
    {
      name: "stress scenarios",
      value: "spread+1/+2 · slippage+1/+2 · latency+50ms/+150ms",
      meaning: "Six perturbed re-pricings of the identical trade list.",
      ref: "robustness.py",
    },
    {
      name: "canonical cost source",
      value: "configs/execution_assumptions.json (provenance-keyed)",
      meaning: "Production costs; fallback-zero refused unless explicitly allowed.",
      ref: "configs/execution_assumptions.json",
    },
  ],
  faq: [
    {
      q: "Why is my frictionless backtest higher than the gate's number?",
      a: "Because the gate prices production-like costs (ECON v1: spread, swap, slippage from canonical config) while a legacy frictionless run prices zero (label FRICTIONLESS_RESEARCH - LEGACY). The gap IS the cost of trading — E1 exists so nobody mistakes the second number for the first.",
    },
    {
      q: "Degradation crossed 0.25R but expectancy is still positive — rejected?",
      a: "FRAGILE classification is robustness's verdict on the stress battery; the scoring verdict chain decides the candidate. Positive-but-fragile under stress is exactly the profile that lands INCONCLUSIVE/REJECTED at scoring — check the gate row's failure_reason for which rule fired.",
    },
    {
      q: "What do purge and embargo actually remove?",
      a: "Purge: boundary samples whose label windows straddle the split (default 300 s each side). Embargo: an extra 60 s of post-boundary samples excluded from training so serial correlation can't carry the future backward. Both run at every walk-forward, OOS, and replay split.",
    },
    {
      q: "Can I set my own friction numbers?",
      a: "Yes — via configs/execution_assumptions.json with provenance (who/when). The registry will then quote those costs. What the UI never does is mix provenance: canonical and fallback-zero cannot blend in one study (E1).",
    },
  ],
};
