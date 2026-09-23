/**
 * Handbook entry — WALK_FORWARD gate (chain position 3 of 6).
 * Compiled from src/nexus_scalp/research/{evidence,walkforward,splitting}.py.
 */
import type { HandbookEntry } from "../types";

/** Mirrors walkforward.py::MIN_FOLD_EXPECTANCY_R (a fold must be > 0). */
export const MIN_FOLD_EXPECTANCY_R = 0.0;
/** Mirrors walkforward.py::MIN_PASS_FRACTION (majority of folds must pass). */
export const MIN_PASS_FRACTION = 0.5;

export const walkForwardEntry: HandbookEntry = {
  id: "gate/WALK_FORWARD",
  kind: "gate",
  badge: "GATE 3 / 6",
  title: "Walk-forward validation",
  subtitle: "Repeated temporal re-evaluation over purged folds — one lucky window is not an edge.",
  source: "src/nexus_scalp/research/walkforward.py, splitting.py",
  seeAlso: ["gate/BACKTEST", "gate/OOS", "topic/economics", "topic/pipeline"],
  keywords: ["walk forward", "folds", "purge", "embargo", "stability", "pass fraction"],
  sections: [
    {
      heading: "What it computes",
      body: [
        "The walk-forward engine consumes the purged/embargoed folds that splitting.py::walk_forward_folds constructs, backtests each validation window, and tracks fold expectancy, drawdown, stability and degradation (walkforward.py module docstring, spec 14/38).",
        "The doctrine sentence from the source is the one to memorize: a strategy that succeeds in only one fold is not robust. The gate converts that sentence into arithmetic — per-fold PASS/FAIL plus a pass-fraction requirement.",
        "Each fold is an independent temporal holdout: the strategy logic never sees fold N+1's data while being judged on fold N. The purge and embargo windows keep decisions near a boundary from leaking across it.",
      ],
      bullets: [
        "Fold PASS: validation expectancy strictly positive (expectancy > MIN_FOLD_EXPECTANCY_R = 0.0 R).",
        "Gate PASS: at least MIN_PASS_FRACTION = 0.5 of folds pass — half the windows must stand on their own.",
        "Also tracked per fold: drawdown, stability and relative degradation vs the baseline backtest.",
      ],
    },
    {
      heading: "Why purge and embargo exist",
      body: [
        "Adjacent windows share the real world: a trade opened seconds before a cut would otherwise carry information from one window into the next. splitting.py closes that door with two independent guards.",
        "PURGE removes samples whose label window overlaps the cut — a triple-barrier label that would still be open at the boundary is pulled out of the training side.",
        "EMBARGO skips a quiet period after the cut (default 60 seconds) so early reactions to post-cut data cannot echo backwards into evaluation.",
        "Defaults are conservative and explicit: DEFAULT_PURGE_SECONDS = 300, DEFAULT_EMBARGO_SECONDS = 60. They are parameters of the split, recorded with the run, not hidden constants.",
      ],
    },
    {
      heading: "How the verdict flows",
      body: [
        "walkforward.passed is consumed in two places: the gate row itself, and scoring.py's verdict chain — if walkforward is present and did not pass, the score verdict is INCONCLUSIVE with reason 'Walk-forward did not pass', regardless of how attractive the point estimates look.",
        "Note the word INCONCLUSIVE: the scoring layer reserves REJECTED for hard evidence against the candidate (OOS failure) and uses INCONCLUSIVE for 'the evidence does not yet support VALIDATED'. The distinction drives whether evidence-building can still rescue the candidate.",
        "Relative degradation per fold feeds the risk/stability dimensions of the final score, so a strategy that passes on a knife-edge (positive but collapsing across folds) still scores poorly.",
      ],
    },
    {
      heading: "Walk-forward OOS vs the Forward Test experiment",
      body: [
        "Do not confuse this gate with forward_test.py's FORWARD_TEST experiment. The source draws the line explicitly (forward_test.py module docstring, §46): Walk-Forward OOS = rolling folds where a model is TRAINED before each fold; Forward Test = ONE explicit frozen cutoff with model, scaler, strategy and parameters all frozen at the cutoff, evaluated only on unseen data after it.",
        "Both are legitimate out-of-sample evidence; they answer different questions. Walk-forward asks 'does this keep working as time rolls?' while the forward test asks 'does the artifact I froze on date X generalize forward without any tuning?'",
        "Only the walk-forward gate is part of GATE_CHAIN. The forward test is a separate, opt-in experiment surface and never substitutes for a chain gate.",
      ],
    },
    {
      heading: "Reading the rows in the UI",
      body: [
        "Gate ledger: PASSED/FAILED with fold statistics summarized in result_summary; duration_ms shows how long the fold battery took.",
        "Events: GATE_STARTED/GATE_COMPLETED entries bracket the window — if a walk-forward sits in RUNNING for an unusually long time, compare the event timestamps against the worker heartbeat (a STUCK worker freezes mid-gate by definition).",
        "A BLOCKED walk-forward with DATA class usually means the family partition or dataset vanished (retention), not that the strategy regressed — read failure_reason verbatim; the UI never paraphrases it.",
      ],
    },
  ],
  params: [
    {
      name: "MIN_FOLD_EXPECTANCY_R",
      value: "0.0",
      meaning: "A fold passes when validation expectancy is positive.",
      ref: "walkforward.py",
    },
    {
      name: "MIN_PASS_FRACTION",
      value: "0.5",
      meaning: "Fraction of folds that must pass for the strategy to count as stable.",
      ref: "walkforward.py",
    },
    {
      name: "DEFAULT_PURGE_SECONDS",
      value: "300.0",
      meaning: "Samples whose label window crosses a cut are removed from the boundary.",
      ref: "splitting.py",
    },
    {
      name: "DEFAULT_EMBARGO_SECONDS",
      value: "60.0",
      meaning: "Quiet period enforced after each cut to kill boundary echo.",
      ref: "splitting.py",
    },
  ],
  faq: [
    {
      q: "Half my folds pass exactly — did the gate pass?",
      a: "The requirement is pass_fraction >= 0.5 (MIN_PASS_FRACTION). Exactly half meets the floor; fewer than half fails it. The per-fold bar itself is strictly positive expectancy, so a zero-expectancy fold does not pass.",
    },
    {
      q: "Why did scoring say INCONCLUSIVE instead of REJECTED when walk-forward failed?",
      a: "scoring.py treats walk-forward failure as insufficient evidence (INCONCLUSIVE), while an OOS failure is hard evidence against the candidate (REJECTED + OOS_FAILURE). INCONCLUSIVE candidates can still be routed to evidence-building; REJECTED ones cannot.",
    },
    {
      q: "Can I raise the pass fraction to make validation stricter?",
      a: "The constants live in the backend source, not in the UI. Changing them is a contract change (agents/contracts.md + change_control.md discipline), not a dashboard toggle — the UI renders thresholds, it never owns them.",
    },
  ],
};
