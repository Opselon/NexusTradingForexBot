/**
 * Handbook — economics, friction, and degradation doctrine (cross-cutting).
 * Compiled from src/nexus_scalp/research/{robustness,oos,splitting,economics,metrics}.py.
 * Constants live where they are enforced: robustness.py (degradation line),
 * oos.py (floors/ceiling) and splitting.py (purge/embargo defaults) — the
 * regression test re-reads each of those files and fails on drift.
 *
 * Single source of the entry (scoring.ts pattern): every user-visible string
 * goes through t() with the English only as fallback; keys live in
 * features/research/i18n.ts under research.hb.economics.*.
 */
import type { HandbookEntry } from "./types";
import type { ScoringTranslate } from "./scoring";

/** Mirrors robustness.py / oos.py / splitting.py (see test for each source). */
export const MAX_ACCEPTABLE_DEGRADATION_R = 0.25;
export const MIN_ECONOMIC_OOS_EXPECTANCY_R = 0.02;
export const MAX_OOS_DEGRADATION = 1.0;
/** Mirrors splitting.py defaults (seconds). */
export const DEFAULT_PURGE_SECONDS = 300;
export const DEFAULT_EMBARGO_SECONDS = 60;

/** Identity translator (en): English fallback with {var} interpolation only. */
const identity: ScoringTranslate = (_key, fallback, vars) =>
  vars
    ? Object.entries(vars).reduce((s, [k, v]) => s.split(`{${k}}`).join(String(v)), fallback)
    : fallback;

function buildEconomicsEntry(t: ScoringTranslate = identity): HandbookEntry {
  return {
  id: "topic/economics",
  kind: "topic",
  badge: t("research.hb.economics.badge", "E1 GUARD · DEGRADATION 0.25R"),
  title: t("research.hb.economics.title", "Economics — friction, degradation, splits, stress"),
  subtitle: t("research.hb.economics.subtitle", "What 'production-like' means numerically: cost provenance, the zero-friction guard, the 0.25R rule, purge/embargo."),
  source: "src/nexus_scalp/research/{economics,metrics,robustness,splitting,models}.py",
  seeAlso: ["gate/BACKTEST", "gate/ROBUSTNESS", "gate/OOS"],
  keywords: ["friction", "e1", "zero friction", "econ", "degradation", "purge", "embargo", "stress", "spread", "slippage", "latency", "swap"],
  sections: [
    {
      heading: t("research.hb.economics.s1_head", "E1 — the zero-friction guard"),
      body: [
        t("research.hb.economics.s1_p1", "backtest_economics.py raises ZeroFrictionError on mixed or unset friction provenance: any 'zero friction' study must be an INTENTIONAL, exclusively zero-friction study — never an accident and never mixed with production costs (module header)."),
        t("research.hb.economics.s1_p2", "The guard exists because a zero-cost backtest quietly inflates expectancy: spreads, swaps and slippage are the difference between a research artifact and a tradable claim. Accidental zero-cost runs are the failure mode E1 exists to stop."),
        t("research.hb.economics.s1_p3", "A study's provenance classifies EVERY assumption: canonical costs (production values from configs/execution_assumptions.json) or fallback zero (refused unless explicitly allowed). Mixed provenance = refuse, with reasons, rather than average the two truths."),
      ],
    },
    {
      heading: t("research.hb.economics.s2_head", "Cost provenance — where numbers come from"),
      body: [
        t("research.hb.economics.s2_p1", "configs/execution_assumptions.json is the canonical cost source: spread, commission/fees, swap, slippage defaults the production executor actually pays. Its 'provenance' key records who published it and when — costs carry receipts too."),
        t("research.hb.economics.s2_p2", "Fallback-zero is the refusal path: when canonical costs are unavailable and zero is not explicitly allowed, the run fails instead of silently pricing the world at free (ECON regime in economics.py docstring)."),
        t("research.hb.economics.s2_p3", "ECON v1 canonical labels (economics.py): 'ECON/PRODUCTION-LIKE - ECON V1' for the production-like engine with 'required-swap validation' and 'sized economic re-valuation' — conservative evidence-derived friction applied and re-sized against real economics."),
        t("research.hb.economics.s2_p4", "Legacy ExecutionAssumptions paths keep the 'FRICTIONLESS_RESEARCH - LEGACY' label: they are allowed only as explicitly-frictionless research, never as production evidence (task.md 68)."),
      ],
    },
    {
      heading: t("research.hb.economics.s3_head", "Degradation — the 0.25R line"),
      body: [
        t("research.hb.economics.s3_p1", "MAX_ACCEPTABLE_DEGRADATION_R = 0.25: robustness measures how far stressed expectancy falls BELOW baseline; a drop beyond 0.25R classifies the strategy FRAGILE (docs/gates.md)."),
        t("research.hb.economics.s3_p2", "Degradation is relational, not absolute: a strategy at +0.60R baseline falling to +0.35R under spread+2 has crossed the line; one at +0.20R falling to +0.10R has not — but the second's thin baseline is its own problem at the OOS floor."),
        t("research.hb.economics.s3_p3", "The degradation dimension of the score (weight 0.02) records the signal numerically even when the gate verdict decides the claim — the dashboard shows the drop, the verdict says what it cost."),
        t("research.hb.economics.s3_p4", "Two ceilings coexist: the 0.25R robustness line (stress-vs-baseline) and MAX_OOS_DEGRADATION=1.0 (OOS-vs-baseline relative collapse — expectancy wiped out entirely). Different comparisons; both call a strategy degraded."),
      ],
    },
    {
      heading: t("research.hb.economics.s4_head", "OOS economics floors"),
      body: [
        t("research.hb.economics.s4_p1", "MIN_ECONOMIC_OOS_EXPECTANCY_R = 0.02: the default economic floor — out-of-sample expectancy must clear +0.02R, not merely zero (oos.py)."),
        t("research.hb.economics.s4_p2", "Legacy floor MIN_OOS_EXPECTANCY_R = 0.0 remains for explicitly-legacy configurations that set zero: 'the legacy floor stays zero only for configs that explicitly set zero'."),
        t("research.hb.economics.s4_p3", "MAX_OOS_DEGRADATION = 1.0: relative ceiling — OOS expectancy may fall hard, but if it falls by more than 100% of baseline (i.e. inverted), the candidate is dead regardless of other dims."),
        t("research.hb.economics.s4_p4", "These are the DEFAULTS; a config may set different numbers and the config WINS. What the backend reports (gate metrics, scoring reasons) is what happened — this page documents the shipped defaults, not a specific deployment."),
      ],
    },
    {
      heading: t("research.hb.economics.s5_head", "Purged + embargoed evaluation"),
      body: [
        t("research.hb.economics.s5_p1", "spec 35/36: every split is purged with an embargo so today's labels cannot leak into adjacent train/validation slices — leakage that would let the model peek at the future through label overlap."),
        t("research.hb.economics.s5_p2", "splitting.py defaults: DEFAULT_PURGE_SECONDS=300 (5 minutes around the split boundary) and DEFAULT_EMBARGO_SECONDS=60 (1 minute of embargo beyond purge)."),
        t("research.hb.economics.s5_p3", "Purge removes samples NEAR the boundary whose label windows straddle it; embargo withholds post-boundary samples from training so serial correlation in adjacent bars cannot smuggle the future backward."),
        t("research.hb.economics.s5_p4", "Where enforced: walk-forward folds (split_temporal with embargo), OOS slicing, and the offline replay validation gate — same splitter, same defaults, everywhere a boundary is drawn."),
      ],
    },
    {
      heading: t("research.hb.economics.s6_head", "The six-scenario stress battery"),
      body: [
        t("research.hb.economics.s6_p1", "robustness.py builds the scenario table by perturbing execution costs on the SAME trades: spread_plus_1 / spread_plus_2 (ticks added to entry cost), slippage_plus_1 / slippage_plus_2 (slippage units added), latency_plus_50ms / latency_plus_150ms (fill timing degraded)."),
        t("research.hb.economics.s6_p2", "Each scenario re-prices the identical trade list — deterministic, comparable, no re-discovery of trades under stress (apples-to-apples is the whole point)."),
        t("research.hb.economics.s6_p3", "Degradation per scenario = baseline expectancy − stressed expectancy; the WORST scenario drives the FRAGILE/RESILIENT classification against the 0.25R line."),
        t("research.hb.economics.s6_p4", "Why these six: they are the costs an executor actually overpays versus the model — wider spread, worse fill, slower reaction. Stress that never maps to a real cost is theater."),
      ],
    },
    {
      heading: t("research.hb.economics.s7_head", "One trade, one economic observation"),
      body: [
        t("research.hb.economics.s7_p1", "One economic trade = one economic observation (discovery.py header): executed AND closed outcomes only, deduplicated by idempotency key, fills never re-counted."),
        t("research.hb.economics.s7_p2", "This underwrites every R-number above: expectancy is per-observation, sample floors count observations, and duplicates cannot inflate either."),
        t("research.hb.economics.s7_p3", "Sized economic re-valuation (ECON v1) re-prices the sized result against the economic assumptions after sizing — risk applied AFTER costs are honest, not before."),
      ],
    },
  ],
  params: [
    {
      name: "MAX_ACCEPTABLE_DEGRADATION_R",
      value: "0.25",
      meaning: t("research.hb.economics.param_degradation", "Stress-vs-baseline drop that classifies a strategy FRAGILE."),
      ref: "robustness.py / docs/gates.md",
    },
    {
      name: "MIN_ECONOMIC_OOS_EXPECTANCY_R",
      value: "0.02",
      meaning: t("research.hb.economics.param_oos_floor", "Default economic OOS floor (+0.02R, not zero)."),
      ref: "oos.py",
    },
    {
      name: "MAX_OOS_DEGRADATION",
      value: "1.0",
      meaning: t("research.hb.economics.param_oos_degradation", "Relative OOS collapse ceiling — beyond 100%, dead."),
      ref: "oos.py",
    },
    {
      name: "DEFAULT_PURGE_SECONDS",
      value: "300",
      meaning: t("research.hb.economics.param_purge", "Purge window around every split boundary."),
      ref: "splitting.py",
    },
    {
      name: "DEFAULT_EMBARGO_SECONDS",
      value: "60",
      meaning: t("research.hb.economics.param_embargo", "Embargo beyond purge — no post-boundary samples in training."),
      ref: "splitting.py",
    },
    {
      name: "stress scenarios",
      value: "spread+1/+2 · slippage+1/+2 · latency+50ms/+150ms",
      meaning: t("research.hb.economics.param_scenarios", "Six perturbed re-pricings of the identical trade list."),
      ref: "robustness.py",
    },
    {
      name: "canonical cost source",
      value: "configs/execution_assumptions.json (provenance-keyed)",
      meaning: t("research.hb.economics.param_cost_source", "Production costs; fallback-zero refused unless explicitly allowed."),
      ref: "configs/execution_assumptions.json",
    },
  ],
  faq: [
    {
      q: t("research.hb.economics.faq1_q", "Why is my frictionless backtest higher than the gate's number?"),
      a: t("research.hb.economics.faq1_a", "Because the gate prices production-like costs (ECON v1: spread, swap, slippage from canonical config) while a legacy frictionless run prices zero (label FRICTIONLESS_RESEARCH - LEGACY). The gap IS the cost of trading — E1 exists so nobody mistakes the second number for the first."),
    },
    {
      q: t("research.hb.economics.faq2_q", "Degradation crossed 0.25R but expectancy is still positive — rejected?"),
      a: t("research.hb.economics.faq2_a", "FRAGILE classification is robustness's verdict on the stress battery; the scoring verdict chain decides the candidate. Positive-but-fragile under stress is exactly the profile that lands INCONCLUSIVE/REJECTED at scoring — check the gate row's failure_reason for which rule fired."),
    },
    {
      q: t("research.hb.economics.faq3_q", "What do purge and embargo actually remove?"),
      a: t("research.hb.economics.faq3_a", "Purge: boundary samples whose label windows straddle the split (default 300 s each side). Embargo: an extra 60 s of post-boundary samples excluded from training so serial correlation can't carry the future backward. Both run at every walk-forward, OOS, and replay split."),
    },
    {
      q: t("research.hb.economics.faq4_q", "Can I set my own friction numbers?"),
      a: t("research.hb.economics.faq4_a", "Yes — via configs/execution_assumptions.json with provenance (who/when). The registry will then quote those costs. What the UI never does is mix provenance: canonical and fallback-zero cannot blend in one study (E1)."),
    },
  ],
  };
}

/** Canonical English entry (identity) — TOC, search and tests consume this. */
export const economicsEntry: HandbookEntry = buildEconomicsEntry();

/** Translator-aware copy: call during render with the store's current t(). */
export function economicsTranslated(t?: ScoringTranslate): HandbookEntry {
  return buildEconomicsEntry(t);
}
