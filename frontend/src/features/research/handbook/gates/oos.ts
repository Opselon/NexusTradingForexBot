/**
 * Handbook entry — OOS gate (chain position 4 of 6).
 * Compiled from src/nexus_scalp/research/{evidence,oos,metrics}.py.
 */
import type { HandbookEntry } from "../types";

/** Mirrors oos.py::MIN_OOS_EXPECTANCY_R (legacy zero floor; 0.02R is default). */
export const MIN_OOS_EXPECTANCY_R = 0.0;

export const oosEntry: HandbookEntry = {
  id: "gate/OOS",
  kind: "gate",
  badge: "GATE 4 / 6",
  title: "Out-of-sample gate",
  subtitle: "The hard wall: excellent in-sample numbers mean nothing if unseen data is negative.",
  source: "src/nexus_scalp/research/oos.py, scoring.py",
  seeAlso: ["gate/ROBUSTNESS", "gate/SCORING", "scoring", "topic/pipeline"],
  keywords: ["oos", "out of sample", "degradation", "bootstrap", "significance", "expectancy floor"],
  sections: [
    {
      heading: "The rule it enforces",
      body: [
        "The source states it flatly (oos.py module docstring, spec 15/34/38): a candidate can NOT become VALIDATED merely because in-sample performance is excellent; it MUST survive the out-of-sample gate. A strategy whose OOS is negative is REJECTED even if win rate is high.",
        "This is the single most consequential gate in the chain. In scoring.py's verdict chain, an OOS result whose status is not PASS forces verdict=REJECTED with reason OOS_FAILURE — not INCONCLUSIVE, not 'needs more data'.",
        "The philosophical split matters: walk-forward failure says the evidence is thin; OOS failure says the evidence is against you. Only the latter forecloses evidence-building.",
      ],
    },
    {
      heading: "Two floors, chosen deliberately",
      body: [
        "MIN_ECONOMIC_OOS_EXPECTANCY_R = 0.02 is the default floor: OOS expectancy must clear +0.02 R after modeled friction. The constructor documents the intent — min_oos_expectancy_r=None means 'use the ECONOMIC floor'; passing 0.0 EXPLICITLY restores the legacy non-negative contract (oos.py OOSGate.__init__).",
        "The distinction between None and 0.0 exists because 'non-negative OOS' and 'economically positive OOS' are different claims. The default asks the economic question: does this edge pay for its friction?",
        "MAX_OOS_DEGRADATION = 1.0 is the hard ceiling on relative drop from the in-sample baseline: a 100% relative collapse is the most tolerance the gate will absorb. Degrading from +0.40R to +0.01R passes the ceiling while still failing the expectancy floor — both conditions must hold.",
      ],
    },
    {
      heading: "Point estimates are not enough (edge hardening)",
      body: [
        "Since the 2026-09-09 edge hardening, a VALIDATED verdict may no longer rest on a point estimate. When the gate produced a bootstrap significance (the real gate path always does), the 95% confidence interval for mean OOS R must sit ENTIRELY above breakeven (scoring.py::_oos_evidence_is_decisive).",
        "A CI straddling zero, or too few OOS trades to compute one, is evidence-building territory — the candidate lands INCONCLUSIVE with the interval quoted verbatim in the reasons: 'bootstrap 95% CI [low, high] on n=… straddles 0 or sample floor unmet'.",
        "Legacy producers that never wrote the significance field keep their old behavior — the code checks the field's presence rather than fabricating a significance it does not have.",
      ],
    },
    {
      heading: "Multiplicity: best-of-N must survive deflation",
      body: [
        "Mining many candidates and keeping the best inflates apparent skill. scoring.py::_selection_bias_control_passed applies two family-level controls before any verdict may claim real edge (EDGE ROUND-2, 2026-09-09).",
        "Deflated Sharpe (Bailey–de Prado): across n_trials > 1, DSR must clear DSR_CONFIDENCE_FLOOR = 0.95 — the conventional bar for 'real after search'. The failure reason quotes the exact dsr value and n_trials.",
        "Family Reality Check (SPA): when the OOS stage evaluated multiple families, the best-of-N family must be distinguished from luck; the failure reason quotes p and n_families.",
        "Both failures route to INCONCLUSIVE with the numbers attached — the operator sees WHICH control failed and by how much, never a bare 'not significant'.",
      ],
    },
    {
      heading: "Reading it in the UI",
      body: [
        "Gate ledger row: status + failure_reason carry the backend's own wording (CI bounds, DSR value, p-value). Copy that text verbatim when discussing a rejection — it is the evidence record.",
        "Registry: an OOS-rejected candidate shows lifecycle REJECTED (terminal) unless a later re-discovery produced a NEW strategy_id — rejection applies to that candidate/version's evidence, not to the context family forever.",
        "Analytics tab: OOS-class rejections appear in the rejection_reasons distribution — the heatmap answers 'how many candidates died at which wall', which is the fastest way to see whether discovery is producing plausible-but-fragile families.",
      ],
    },
  ],
  params: [
    {
      name: "MIN_ECONOMIC_OOS_EXPECTANCY_R",
      value: "0.02",
      meaning: "Default OOS floor: unseen-data expectancy must be economically positive after friction.",
      ref: "oos.py",
    },
    {
      name: "MIN_OOS_EXPECTANCY_R",
      value: "0.0",
      meaning: "Legacy non-negative contract — restored only by explicitly passing 0.0.",
      ref: "oos.py",
    },
    {
      name: "MAX_OOS_DEGRADATION",
      value: "1.0",
      meaning: "Hard ceiling: relative drop vs in-sample baseline may not exceed 100%.",
      ref: "oos.py",
    },
    {
      name: "DSR_CONFIDENCE_FLOOR",
      value: "0.95",
      meaning: "Deflated-Sharpe floor for a mined candidate to count as real-after-search.",
      ref: "scoring.py",
    },
  ],
  faq: [
    {
      q: "OOS expectancy +0.05R but verdict says INCONCLUSIVE — how?",
      a: "The expectancy floor is only one of several hard gates. Check the reasons list for CI straddling breakeven (not decisive), missing robustness/pass states, sample floor < 20, or a selection-bias control failure (DSR/SPA) — each produces INCONCLUSIVE with its own quoted numbers.",
    },
    {
      q: "REJECTED after OOS — can promote fix it?",
      a: "No. Promotion runs through the lifecycle state machine (lifecycle.py): REJECTED is terminal (no outgoing transitions), and approve_for_live accepts only SHADOW or VALIDATED. The backend refuses regardless of actor or reason text.",
    },
    {
      q: "What does 'degradation' mean here versus the robustness gate?",
      a: "OOS degradation compares unseen-data performance to the in-sample baseline (did the edge decay out of sample?). Robustness degradation re-prices the SAME data under stressed friction/timing (will small environment changes kill it?). Different questions, different gates, both quoted in R.",
    },
  ],
};
