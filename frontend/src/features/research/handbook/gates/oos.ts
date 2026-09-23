/**
 * Handbook entry — OOS gate (chain position 4 of 6).
 * Compiled from src/nexus_scalp/research/{evidence,oos,metrics}.py.
 */
import type { HandbookEntry } from "../types";
import type { ScoringTranslate } from "../scoring";

/** Mirrors oos.py::MIN_OOS_EXPECTANCY_R (legacy zero floor; 0.02R is default). */
export const MIN_OOS_EXPECTANCY_R = 0.0;

/** Identity translator (en): English fallback, no interpolation needed. */
const identity: ScoringTranslate = (_key, fallback) => fallback;

function buildoosEntry(t: ScoringTranslate = identity): HandbookEntry {
  return {
  id: "gate/OOS",
  kind: "gate",
  badge: t("research.hb.gates.oos.badge", "GATE 4 / 6"),
  title: t("research.hb.gates.oos.title", "Out-of-sample gate"),
  subtitle: t("research.hb.gates.oos.subtitle", "The hard wall: excellent in-sample numbers mean nothing if unseen data is negative."),
  source: "src/nexus_scalp/research/oos.py, scoring.py",
  seeAlso: ["gate/ROBUSTNESS", "gate/SCORING", "scoring", "topic/pipeline"],
  keywords: ["oos", "out of sample", "degradation", "bootstrap", "significance", "expectancy floor"],
  sections: [
    {
      heading: t("research.hb.gates.oos.sec1_head", "The rule it enforces"),
      body: [
        t("research.hb.gates.oos.sec1_p1", "The source states it flatly (oos.py module docstring, spec 15/34/38): a candidate can NOT become VALIDATED merely because in-sample performance is excellent; it MUST survive the out-of-sample gate. A strategy whose OOS is negative is REJECTED even if win rate is high."),
        t("research.hb.gates.oos.sec1_p2", "This is the single most consequential gate in the chain. In scoring.py\'s verdict chain, an OOS result whose status is not PASS forces verdict=REJECTED with reason OOS_FAILURE — not INCONCLUSIVE, not \'needs more data\'."),
        t("research.hb.gates.oos.sec1_p3", "The philosophical split matters: walk-forward failure says the evidence is thin; OOS failure says the evidence is against you. Only the latter forecloses evidence-building."),
      ],
    },
    {
      heading: t("research.hb.gates.oos.sec2_head", "Two floors, chosen deliberately"),
      body: [
        t("research.hb.gates.oos.sec2_p1", "MIN_ECONOMIC_OOS_EXPECTANCY_R = 0.02 is the default floor: OOS expectancy must clear +0.02 R after modeled friction. The constructor documents the intent — min_oos_expectancy_r=None means \'use the ECONOMIC floor\'; passing 0.0 EXPLICITLY restores the legacy non-negative contract (oos.py OOSGate.__init__)."),
        t("research.hb.gates.oos.sec2_p2", "The distinction between None and 0.0 exists because \'non-negative OOS\' and \'economically positive OOS\' are different claims. The default asks the economic question: does this edge pay for its friction?"),
        t("research.hb.gates.oos.sec2_p3", "MAX_OOS_DEGRADATION = 1.0 is the hard ceiling on relative drop from the in-sample baseline: a 100% relative collapse is the most tolerance the gate will absorb. Degrading from +0.40R to +0.01R passes the ceiling while still failing the expectancy floor — both conditions must hold."),
      ],
    },
    {
      heading: t("research.hb.gates.oos.sec3_head", "Point estimates are not enough (edge hardening)"),
      body: [
        t("research.hb.gates.oos.sec3_p1", "Since the 2026-09-09 edge hardening, a VALIDATED verdict may no longer rest on a point estimate. When the gate produced a bootstrap significance (the real gate path always does), the 95% confidence interval for mean OOS R must sit ENTIRELY above breakeven (scoring.py::_oos_evidence_is_decisive)."),
        t("research.hb.gates.oos.sec3_p2", "A CI straddling zero, or too few OOS trades to compute one, is evidence-building territory — the candidate lands INCONCLUSIVE with the interval quoted verbatim in the reasons: \'bootstrap 95% CI [low, high] on n=… straddles 0 or sample floor unmet\'."),
        t("research.hb.gates.oos.sec3_p3", "Legacy producers that never wrote the significance field keep their old behavior — the code checks the field\'s presence rather than fabricating a significance it does not have."),
      ],
    },
    {
      heading: t("research.hb.gates.oos.sec4_head", "Multiplicity: best-of-N must survive deflation"),
      body: [
        t("research.hb.gates.oos.sec4_p1", "Mining many candidates and keeping the best inflates apparent skill. scoring.py::_selection_bias_control_passed applies two family-level controls before any verdict may claim real edge (EDGE ROUND-2, 2026-09-09)."),
        t("research.hb.gates.oos.sec4_p2", "Deflated Sharpe (Bailey–de Prado): across n_trials > 1, DSR must clear DSR_CONFIDENCE_FLOOR = 0.95 — the conventional bar for \'real after search\'. The failure reason quotes the exact dsr value and n_trials."),
        t("research.hb.gates.oos.sec4_p3", "Family Reality Check (SPA): when the OOS stage evaluated multiple families, the best-of-N family must be distinguished from luck; the failure reason quotes p and n_families."),
        t("research.hb.gates.oos.sec4_p4", "Both failures route to INCONCLUSIVE with the numbers attached — the operator sees WHICH control failed and by how much, never a bare \'not significant\'."),
      ],
    },
    {
      heading: t("research.hb.gates.oos.sec5_head", "Reading it in the UI"),
      body: [
        t("research.hb.gates.oos.sec5_p1", "Gate ledger row: status + failure_reason carry the backend\'s own wording (CI bounds, DSR value, p-value). Copy that text verbatim when discussing a rejection — it is the evidence record."),
        t("research.hb.gates.oos.sec5_p2", "Registry: an OOS-rejected candidate shows lifecycle REJECTED (terminal) unless a later re-discovery produced a NEW strategy_id — rejection applies to that candidate/version\'s evidence, not to the context family forever."),
        t("research.hb.gates.oos.sec5_p3", "Analytics tab: OOS-class rejections appear in the rejection_reasons distribution — the heatmap answers \'how many candidates died at which wall\', which is the fastest way to see whether discovery is producing plausible-but-fragile families."),
      ],
    },
  ],
  params: [
    {
      name: "MIN_ECONOMIC_OOS_EXPECTANCY_R",
      value: "0.02",
      meaning: t("research.hb.gates.oos.param1_meaning", "Default OOS floor: unseen-data expectancy must be economically positive after friction."),
      ref: "oos.py",
    },
    {
      name: "MIN_OOS_EXPECTANCY_R",
      value: "0.0",
      meaning: t("research.hb.gates.oos.param2_meaning", "Legacy non-negative contract — restored only by explicitly passing 0.0."),
      ref: "oos.py",
    },
    {
      name: "MAX_OOS_DEGRADATION",
      value: "1.0",
      meaning: t("research.hb.gates.oos.param3_meaning", "Hard ceiling: relative drop vs in-sample baseline may not exceed 100%."),
      ref: "oos.py",
    },
    {
      name: "DSR_CONFIDENCE_FLOOR",
      value: "0.95",
      meaning: t("research.hb.gates.oos.param4_meaning", "Deflated-Sharpe floor for a mined candidate to count as real-after-search."),
      ref: "scoring.py",
    },
  ],
  faq: [
    {
      q: t("research.hb.gates.oos.faq1_q", "OOS expectancy +0.05R but verdict says INCONCLUSIVE — how?"),
      a: t("research.hb.gates.oos.faq1_a", "The expectancy floor is only one of several hard gates. Check the reasons list for CI straddling breakeven (not decisive), missing robustness/pass states, sample floor < 20, or a selection-bias control failure (DSR/SPA) — each produces INCONCLUSIVE with its own quoted numbers."),
    },
    {
      q: t("research.hb.gates.oos.faq2_q", "REJECTED after OOS — can promote fix it?"),
      a: t("research.hb.gates.oos.faq2_a", "No. Promotion runs through the lifecycle state machine (lifecycle.py): REJECTED is terminal (no outgoing transitions), and approve_for_live accepts only SHADOW or VALIDATED. The backend refuses regardless of actor or reason text."),
    },
    {
      q: t("research.hb.gates.oos.faq3_q", "What does \'degradation\' mean here versus the robustness gate?"),
      a: t("research.hb.gates.oos.faq3_a", "OOS degradation compares unseen-data performance to the in-sample baseline (did the edge decay out of sample?). Robustness degradation re-prices the SAME data under stressed friction/timing (will small environment changes kill it?). Different questions, different gates, both quoted in R."),
    },
  ],
  };
}

/** Canonical English entry (identity) — TOC, search and tests consume this. */
export const oosEntry: HandbookEntry = buildoosEntry();

/**
 * Translator-aware copy (features/research/model.ts commandVerdict pattern):
 * call during render with the store's current t(); the result changes with the
 * language — never cache it outside render.
 */
export function oosEntryTranslated(t?: ScoringTranslate): HandbookEntry {
  return buildoosEntry(t);
}
