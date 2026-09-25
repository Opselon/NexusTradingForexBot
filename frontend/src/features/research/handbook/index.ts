/**
 * Research handbook — public surface (UI_WAVE_SPEC layering).
 *
 * All entries are static documentation modules: leaf modules carry only
 * `import type` cross-links (erased before resolution), so tests/js/
 * research_handbook.test.js can import each leaf directly under Node 24
 * type stripping while the app imports this index through Vite.
 *
 * LAYERING (wave-7 load work): this module is a THIN FACADE the regression
 * test loads directly; app code imports the specific layer it needs so the
 * playbook prose never rides in the ResearchPage route chunk:
 *  - backend-mirror constants the ResearchPage hero needs synchronously
 *    (GATE_CHAIN, …) come from ./gateChain (prose-free — importing it pulls
 *    no HandbookEntry module);
 *  - the whole corpus — every prose entry plus aggregation/search/lookup
 *    (HANDBOOK_ENTRIES, queryHandbook, getEntry, STATE_ENTRIES, GATE_ENTRIES)
 *    — lives in ./content.ts, re-exported here for the test and any legacy
 *    importer, but app components import ./content directly (StrategyPlaybook)
 *    or are loaded lazily (StrategyDrawer) so the bundler keeps the prose out
 *    of the route chunk: it downloads only when an operator opens the
 *    Playbook tab or drawer, never at /research first paint.
 * New prose belongs in ./content.ts's leaves; new app-visible constants in
 * ./gateChain.ts — pulling a leaf entry back into this file's static import
 * graph would undo the split for anything importing the facade eagerly.
 */
export type { HandbookEntry, Section, ParamRow, FaqItem, HandbookKind } from "./types";
export { GATE_CHAIN, REQUIRED_FOR_VALIDATED, GATE_STATUSES, FAILURE_CLASSES } from "./gateChain";
export { STATE_IDS, TERMINAL_STATES, TRADE_ELIGIBLE, INELIGIBLE_FOR_LIVE } from "./lifecycle";
export { SCORE_WEIGHTS } from "./scoring";
export { MIN_FAMILY_SAMPLES, MIN_DISCOVERY_EXPECTANCY_R, SMALL_SAMPLE_FLOOR, MIN_EVIDENCE_SAMPLES } from "./discovery";
export {
  MAX_ACCEPTABLE_DEGRADATION_R,
  MIN_ECONOMIC_OOS_EXPECTANCY_R,
  MAX_OOS_DEGRADATION,
  DEFAULT_PURGE_SECONDS,
  DEFAULT_EMBARGO_SECONDS,
} from "./economics";

/** Handbook corpus + search/lookup (see LAYERING above). */
export * from "./content";
