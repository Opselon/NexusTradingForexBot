/**
 * Handbook — the composite strategy score (cross-cutting topic).
 * Compiled from src/nexus_scalp/research/scoring.py + models.py.
 * SCORE_WEIGHTS mirrors scoring.py::weights; the test re-reads the source.
 */
import type { HandbookEntry } from "./types";

/** Mirrors scoring.py final-score weights (sums to 1.00). */
export const SCORE_WEIGHTS = {
  performance: 0.2,
  risk: 0.15,
  stability: 0.1,
  oos: 0.2,
  robustness: 0.15,
  sample: 0.08,
  regime: 0.04,
  recency: 0.04,
  execution: 0.02,
  degradation: 0.02,
} as const;

/** Mirrors scoring.py confidence logistic parameters. */
export const SAMPLE_MID = 60.0;
export const SAMPLE_STEEPNESS = 0.06;
/** Mirrors scoring.py::DSR_CONFIDENCE_FLOOR (deflated-Sharpe honesty bar). */
export const DSR_CONFIDENCE_FLOOR = 0.95;

export type ScoringTranslate = (
  key: string,
  fallback: string,
  vars?: Record<string, string | number>,
) => string;

/** Identity translator (en): English fallback with {var} interpolation only. */
const identity: ScoringTranslate = (_key, fallback, vars) =>
  vars
    ? Object.entries(vars).reduce((s, [k, v]) => s.split(`{${k}}`).join(String(v)), fallback)
    : fallback;

/**
 * Single source of the entry: every user-visible string goes through t() with
 * the English only as fallback; keys live in features/research/i18n.ts.
 */
function buildScoringEntry(t: ScoringTranslate = identity): HandbookEntry {
  return {
  id: "topic/scoring",
  kind: "topic",
  badge: t("research.scoring.badge", "10 DIMENSIONS · WEIGHTS 1.00"),
  title: t("research.scoring.title", "The score — ten dimensions, fixed weights, explainable verdicts"),
  subtitle: t("research.scoring.subtitle", "How performance, risk, OOS, robustness and evidence quality combine — and where the hard gates override the sum."),
  source: "src/nexus_scalp/research/scoring.py, models.py (StrategyScore)",
  seeAlso: ["gate/SCORING", "gate/OOS", "topic/lifecycle"],
  keywords: ["score", "weights", "dimensions", "confidence", "dsr", "bootstrap", "regime coverage"],
  sections: [
    {
      heading: t("research.scoring.sec1_head", "Design rule: decomposable or it doesn\'t ship"),
      body: [
        t("research.scoring.sec1_p1", "scoring.py\'s header: a decomposable multi-dimensional score, NOT a single win rate. Each dimension is bounded [0,1], the final is their fixed-weight combination, and every dimension and reason is exposed — \'the score is fully explainable\' is a spec requirement, not an aspiration (spec 17/18/38)."),
        t("research.scoring.sec1_p2", "The final is rounded to 4 decimals and clamped to [0,1] — no NaN, no >1 bragging, no precision theater beyond four places."),
        t("research.scoring.sec1_p3", "Why decomposition matters operationally: two candidates at 0.61 can be completely different animals (strong performance/weak OOS vs the reverse), and the dimensions tell you which one you\'re looking at before you read the verdict."),
      ],
    },
    {
      heading: t("research.scoring.sec2_head", "The weights (verbatim from source)"),
      body: [
        t("research.scoring.sec2_p1", "performance 0.20 — in-sample expectancy/statistics from the backtest baseline."),
        t("research.scoring.sec2_p2", "oos 0.20 — out-of-sample behavior; tied for the largest single weight with performance, deliberately: unseen data matters as much as seen data."),
        t("research.scoring.sec2_p3", "risk 0.15 — drawdown/loss-profile dimension of the backtest result."),
        t("research.scoring.sec2_p4", "robustness 0.15 — degradation under the six-scenario stress battery."),
        t("research.scoring.sec2_p5", "stability 0.10 — consistency across walk-forward folds."),
        t("research.scoring.sec2_p6", "sample 0.08 — evidence quantity, via a logistic in sample count with a hard floor below SMALL_SAMPLE_FLOOR=8."),
        t("research.scoring.sec2_p7", "regime 0.04 — regime expectancy coverage across the common regime buckets (_regime_expectancy_coverage normalizes breadth so a one-regime specialist doesn\'t fake uniformity)."),
        t("research.scoring.sec2_p8", "recency 0.04 — how recent the qualifying evidence is."),
        t("research.scoring.sec2_p9", "execution 0.02 — execution-resolution quality of the underlying observations."),
        t("research.scoring.sec2_p10", "degradation 0.02 — recorded deterioration signal."),
        t("research.scoring.sec2_p11", "All ten sum to 1.00 — the weight table is the model; there is no hidden term."),
      ],
    },
    {
      heading: t("research.scoring.sec3_head", "Small-sample doctrine in the math"),
      body: [
        t("research.scoring.sec3_p1", "spec 18, restated in the header: 8 trades at +1.2R stays LOW EVIDENCE and never HIGH CONFIDENCE. The sample dimension implements it as a logistic in sample count (_SAMPLE_MID=60, _SAMPLE_STEEPNESS=0.06) with a hard floor below SMALL_SAMPLE_FLOOR=8."),
        t("research.scoring.sec3_p2", "The logistic saturates around 60 samples — evidence keeps counting well past 20 but with diminishing returns, so no candidate can farm confidence by an unbounded sample pump."),
        t("research.scoring.sec3_p3", "Independently of the dimension, the VERDICT enforces floors: n<8 -> INCONCLUSIVE outright; n<20 -> INCONCLUSIVE below the evidence floor regardless of every other gate (TASK-4 hard small-sample gate). The dimension rewards; the verdict forbids."),
      ],
    },
    {
      heading: t("research.scoring.sec4_head", "Statistical honesty controls"),
      body: [
        t("research.scoring.sec4_p1", "Bootstrap significance (EDGE HARDENING): VALIDATED may not rest on a point estimate — the 95% CI for mean OOS R must sit ENTIRELY above breakeven (_oos_evidence_is_decisive). CI straddling 0 or too few OOS trades is evidence-building, not tradable edge."),
        t("research.scoring.sec4_p2", "Deflated Sharpe (EDGE ROUND-2): across n_trials>1 mined candidates, DSR must clear DSR_CONFIDENCE_FLOOR=0.95 — the conventional \'real after search\' bar (Bailey–de Prado). Failure quotes dsr and n_trials verbatim."),
        t("research.scoring.sec4_p3", "Family Reality Check (SPA): best-of-N families must be distinguished from luck — failure quotes p_value and n_families."),
        t("research.scoring.sec4_p4", "Legacy producers without these fields keep old behavior — checks test field presence rather than fabricating significance the run did not compute."),
      ],
    },
    {
      heading: t("research.scoring.sec5_head", "Verdict precedence (read before the number)"),
      body: [
        t("research.scoring.sec5_p1", "The weighted final is computed FIRST and matters LAST: the verdict chain (see gate/SCORING) can end the claim at REJECTED/INCONCLUSIVE regardless of the sum."),
        t("research.scoring.sec5_p2", "Precedence in order: OOS failure -> REJECTED; sample/absence/walk-forward/CI/DSR-SPA blocks -> INCONCLUSIVE; only then -> VALIDATED (\'All evidence gates passed\')."),
        t("research.scoring.sec5_p3", "Practical reading order for a registry row: verdict + reasons -> dimension profile -> final. Inverting that order is how a beautiful 0.87 gets over-trusted."),
      ],
    },
    {
      heading: t("research.scoring.sec6_head", "Where the score lives"),
      body: [
        t("research.scoring.sec6_p1", "StrategyScore persists as score_payload in strategy_intelligence_registry — an upserted CACHE (experience/evaluator.py: the registry \'holds no authoritative state\'; derived rows are rebuildable from the immutable experience store)."),
        t("research.scoring.sec6_p2", "Self-heal (POST /api/experience/self-heal) rebuilds derived strategy intelligence when the cache is suspect: \'success, rebuilt_strategies, reason: ENGINE_UNAVAILABLE\' shapes — the cache never outranks the store it was derived from."),
        t("research.scoring.sec6_p3", "confidence and sample_count shown in the registry table come from this payload; if the payload is absent the UI shows what the backend reported — \'—\', never an invented default."),
      ],
    },
  ],
  params: [
    {
      name: "weights",
      value: "perf .20 · oos .20 · risk .15 · rob .15 · stab .10 · sample .08 · regime .04 · rec .04 · exec .02 · degr .02",
      meaning: t("research.scoring.param_weights", "Fixed ten-dimension weight table (sums to 1.00)."),
      ref: "scoring.py weights dict",
    },
    {
      name: "_SAMPLE_MID / _SAMPLE_STEEPNESS",
      value: "60.0 / 0.06",
      meaning: t("research.scoring.param_sample", "Logistic midpoint/steepness of the sample-confidence dimension."),
      ref: "scoring.py",
    },
    {
      name: "DSR_CONFIDENCE_FLOOR",
      value: "0.95",
      meaning: t("research.scoring.param_dsr", "Deflated-Sharpe bar for mined candidates (n_trials>1)."),
      ref: "scoring.py",
    },
    {
      name: "final clamp",
      value: "round(max(0, min(1, final)), 4)",
      meaning: t("research.scoring.param_clamp", "Every score lands in [0,1] with 4-decimal precision."),
      ref: "scoring.py",
    },
  ],
  faq: [
    {
      q: t("research.scoring.faq1_q", "Two strategies have the same score — which is better?"),
      a: t("research.scoring.faq1_a", "Read the dimensions and verdict first: identical sums hide opposite profiles (in-sample hero vs OOS hero). If verdicts also match, prefer the one whose evidence (sample, regime coverage, recency) is stronger — that is what the sample/regime/recency dimensions are for."),
    },
    {
      q: t("research.scoring.faq2_q", "Why is execution only 0.02?"),
      a: t("research.scoring.faq2_a", "Execution-resolution quality of recorded observations is a data-hygiene signal — important as a tripwire, minor as a driver. The heavy weights (performance, OOS, robustness, risk = 0.70 combined) carry the claim; hygiene weights nudge, they don\'t decide."),
    },
    {
      q: t("research.scoring.faq3_q", "Can the score be gamed by more trades?"),
      a: t("research.scoring.faq3_a", "Sample weight saturates (logistic mid=60) and the verdict floors (8/20) bind the claim. Adding trades that don\'t hold expectancy moves performance/risk/OOS against you — the score has no term where volume alone helps."),
    },
    {
      q: t("research.scoring.faq4_q", "score_payload is empty in the registry — is data lost?"),
      a: t("research.scoring.faq4_a", "No — the registry is a derived cache. Authoritative evidence lives in the immutable experience/gate/evidence stores; run the self-heal command to rebuild derived intelligence from them."),
    },
  ],
  };
}

/** Canonical English entry (identity) — TOC, search and tests consume this. */
export const scoringEntry: HandbookEntry = buildScoringEntry();

/**
 * Translator-aware copy (features/research/model.ts commandVerdict pattern):
 * call during render with the store's current t(); the result changes with the
 * language — never cache it outside render.
 */
export function scoringEntryTranslated(t?: ScoringTranslate): HandbookEntry {
  return buildScoringEntry(t);
}
