/**
 * Handbook entry — WALK_FORWARD gate (chain position 3 of 6).
 * Compiled from src/nexus_scalp/research/{evidence,walkforward,splitting}.py.
 */
import type { HandbookEntry } from "../types";
import type { ScoringTranslate } from "../scoring";

/** Mirrors walkforward.py::MIN_FOLD_EXPECTANCY_R (a fold must be > 0). */
export const MIN_FOLD_EXPECTANCY_R = 0.0;
/** Mirrors walkforward.py::MIN_PASS_FRACTION (majority of folds must pass). */
export const MIN_PASS_FRACTION = 0.5;

/** Identity translator (en): English fallback, no interpolation needed. */
const identity: ScoringTranslate = (_key, fallback) => fallback;

function buildwalkForwardEntry(t: ScoringTranslate = identity): HandbookEntry {
  return {
  id: "gate/WALK_FORWARD",
  kind: "gate",
  badge: t("research.hb.gates.walkForward.badge", "GATE 3 / 6"),
  title: t("research.hb.gates.walkForward.title", "Walk-forward validation"),
  subtitle: t("research.hb.gates.walkForward.subtitle", "Repeated temporal re-evaluation over purged folds — one lucky window is not an edge."),
  source: "src/nexus_scalp/research/walkforward.py, splitting.py",
  seeAlso: ["gate/BACKTEST", "gate/OOS", "topic/economics", "topic/pipeline"],
  keywords: ["walk forward", "folds", "purge", "embargo", "stability", "pass fraction"],
  sections: [
    {
      heading: t("research.hb.gates.walkForward.sec1_head", "What it computes"),
      body: [
        t("research.hb.gates.walkForward.sec1_p1", "The walk-forward engine consumes the purged/embargoed folds that splitting.py::walk_forward_folds constructs, backtests each validation window, and tracks fold expectancy, drawdown, stability and degradation (walkforward.py module docstring, spec 14/38)."),
        t("research.hb.gates.walkForward.sec1_p2", "The doctrine sentence from the source is the one to memorize: a strategy that succeeds in only one fold is not robust. The gate converts that sentence into arithmetic — per-fold PASS/FAIL plus a pass-fraction requirement."),
        t("research.hb.gates.walkForward.sec1_p3", "Each fold is an independent temporal holdout: the strategy logic never sees fold N+1\'s data while being judged on fold N. The purge and embargo windows keep decisions near a boundary from leaking across it."),
      ],
      bullets: [
        t("research.hb.gates.walkForward.sec1_b1", "Fold PASS: validation expectancy strictly positive (expectancy > MIN_FOLD_EXPECTANCY_R = 0.0 R)."),
        t("research.hb.gates.walkForward.sec1_b2", "Gate PASS: at least MIN_PASS_FRACTION = 0.5 of folds pass — half the windows must stand on their own."),
        t("research.hb.gates.walkForward.sec1_b3", "Also tracked per fold: drawdown, stability and relative degradation vs the baseline backtest."),
      ],
    },
    {
      heading: t("research.hb.gates.walkForward.sec2_head", "Why purge and embargo exist"),
      body: [
        t("research.hb.gates.walkForward.sec2_p1", "Adjacent windows share the real world: a trade opened seconds before a cut would otherwise carry information from one window into the next. splitting.py closes that door with two independent guards."),
        t("research.hb.gates.walkForward.sec2_p2", "PURGE removes samples whose label window overlaps the cut — a triple-barrier label that would still be open at the boundary is pulled out of the training side."),
        t("research.hb.gates.walkForward.sec2_p3", "EMBARGO skips a quiet period after the cut (default 60 seconds) so early reactions to post-cut data cannot echo backwards into evaluation."),
        t("research.hb.gates.walkForward.sec2_p4", "Defaults are conservative and explicit: DEFAULT_PURGE_SECONDS = 300, DEFAULT_EMBARGO_SECONDS = 60. They are parameters of the split, recorded with the run, not hidden constants."),
      ],
    },
    {
      heading: t("research.hb.gates.walkForward.sec3_head", "How the verdict flows"),
      body: [
        t("research.hb.gates.walkForward.sec3_p1", "walkforward.passed is consumed in two places: the gate row itself, and scoring.py\'s verdict chain — if walkforward is present and did not pass, the score verdict is INCONCLUSIVE with reason \'Walk-forward did not pass\', regardless of how attractive the point estimates look."),
        t("research.hb.gates.walkForward.sec3_p2", "Note the word INCONCLUSIVE: the scoring layer reserves REJECTED for hard evidence against the candidate (OOS failure) and uses INCONCLUSIVE for \'the evidence does not yet support VALIDATED\'. The distinction drives whether evidence-building can still rescue the candidate."),
        t("research.hb.gates.walkForward.sec3_p3", "Relative degradation per fold feeds the risk/stability dimensions of the final score, so a strategy that passes on a knife-edge (positive but collapsing across folds) still scores poorly."),
      ],
    },
    {
      heading: t("research.hb.gates.walkForward.sec4_head", "Walk-forward OOS vs the Forward Test experiment"),
      body: [
        t("research.hb.gates.walkForward.sec4_p1", "Do not confuse this gate with forward_test.py\'s FORWARD_TEST experiment. The source draws the line explicitly (forward_test.py module docstring, §46): Walk-Forward OOS = rolling folds where a model is TRAINED before each fold; Forward Test = ONE explicit frozen cutoff with model, scaler, strategy and parameters all frozen at the cutoff, evaluated only on unseen data after it."),
        t("research.hb.gates.walkForward.sec4_p2", "Both are legitimate out-of-sample evidence; they answer different questions. Walk-forward asks \'does this keep working as time rolls?\' while the forward test asks \'does the artifact I froze on date X generalize forward without any tuning?\'"),
        t("research.hb.gates.walkForward.sec4_p3", "Only the walk-forward gate is part of GATE_CHAIN. The forward test is a separate, opt-in experiment surface and never substitutes for a chain gate."),
      ],
    },
    {
      heading: t("research.hb.gates.walkForward.sec5_head", "Reading the rows in the UI"),
      body: [
        t("research.hb.gates.walkForward.sec5_p1", "Gate ledger: PASSED/FAILED with fold statistics summarized in result_summary; duration_ms shows how long the fold battery took."),
        t("research.hb.gates.walkForward.sec5_p2", "Events: GATE_STARTED/GATE_COMPLETED entries bracket the window — if a walk-forward sits in RUNNING for an unusually long time, compare the event timestamps against the worker heartbeat (a STUCK worker freezes mid-gate by definition)."),
        t("research.hb.gates.walkForward.sec5_p3", "A BLOCKED walk-forward with DATA class usually means the family partition or dataset vanished (retention), not that the strategy regressed — read failure_reason verbatim; the UI never paraphrases it."),
      ],
    },
  ],
  params: [
    {
      name: "MIN_FOLD_EXPECTANCY_R",
      value: "0.0",
      meaning: t("research.hb.gates.walkForward.param1_meaning", "A fold passes when validation expectancy is positive."),
      ref: "walkforward.py",
    },
    {
      name: "MIN_PASS_FRACTION",
      value: "0.5",
      meaning: t("research.hb.gates.walkForward.param2_meaning", "Fraction of folds that must pass for the strategy to count as stable."),
      ref: "walkforward.py",
    },
    {
      name: "DEFAULT_PURGE_SECONDS",
      value: "300.0",
      meaning: t("research.hb.gates.walkForward.param3_meaning", "Samples whose label window crosses a cut are removed from the boundary."),
      ref: "splitting.py",
    },
    {
      name: "DEFAULT_EMBARGO_SECONDS",
      value: "60.0",
      meaning: t("research.hb.gates.walkForward.param4_meaning", "Quiet period enforced after each cut to kill boundary echo."),
      ref: "splitting.py",
    },
  ],
  faq: [
    {
      q: t("research.hb.gates.walkForward.faq1_q", "Half my folds pass exactly — did the gate pass?"),
      a: t("research.hb.gates.walkForward.faq1_a", "The requirement is pass_fraction >= 0.5 (MIN_PASS_FRACTION). Exactly half meets the floor; fewer than half fails it. The per-fold bar itself is strictly positive expectancy, so a zero-expectancy fold does not pass."),
    },
    {
      q: t("research.hb.gates.walkForward.faq2_q", "Why did scoring say INCONCLUSIVE instead of REJECTED when walk-forward failed?"),
      a: t("research.hb.gates.walkForward.faq2_a", "scoring.py treats walk-forward failure as insufficient evidence (INCONCLUSIVE), while an OOS failure is hard evidence against the candidate (REJECTED + OOS_FAILURE). INCONCLUSIVE candidates can still be routed to evidence-building; REJECTED ones cannot."),
    },
    {
      q: t("research.hb.gates.walkForward.faq3_q", "Can I raise the pass fraction to make validation stricter?"),
      a: t("research.hb.gates.walkForward.faq3_a", "The constants live in the backend source, not in the UI. Changing them is a contract change (agents/contracts.md + change_control.md discipline), not a dashboard toggle — the UI renders thresholds, it never owns them."),
    },
  ],
  };
}

/** Canonical English entry (identity) — TOC, search and tests consume this. */
export const walkForwardEntry: HandbookEntry = buildwalkForwardEntry();

/**
 * Translator-aware copy (features/research/model.ts commandVerdict pattern):
 * call during render with the store's current t(); the result changes with the
 * language — never cache it outside render.
 */
export function walkForwardEntryTranslated(t?: ScoringTranslate): HandbookEntry {
  return buildwalkForwardEntry(t);
}
