/**
 * Research playbook — assembly, lookup, search.
 *
 * All entries are static documentation modules: leaf modules carry only
 * `import type` cross-links (erased before resolution), so tests/js/
 * research_handbook.test.js can import each leaf directly under Node 24
 * type stripping while the app imports this index through Vite.
 *
 * Group order = TOC order: big picture, then the gate chain, then the
 * lifecycle states in state-machine order.
 */
import type { HandbookEntry } from "./types";
import { chainOverviewEntry, pipelineEntries } from "./gates";
import { lifecycleEntries } from "./lifecycle";
import { discoveryEntry } from "./discovery";
import { scoringEntry, scoringEntryTranslated, type ScoringTranslate } from "./scoring";
import { economicsEntry } from "./economics";
import { evidenceEntry } from "./evidence";
import { operationsEntry, pipelineEntry } from "./operations";
import { uiGuideEntry } from "./uiguide";
import { masterFaqEntry } from "./faq";
import { registryEntry } from "./registry";
import { experimentsEntry } from "./experiments";
import { glossaryEntry } from "./glossary";
import { healthEntry } from "./health";

export type { HandbookEntry, Section, ParamRow, FaqItem, HandbookKind } from "./types";
export { GATE_CHAIN, REQUIRED_FOR_VALIDATED, GATE_STATUSES, FAILURE_CLASSES } from "./gates";
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

/** Ordered display groups for the playbook TOC. labelKey feeds t() at render. */
export const HANDBOOK_GROUPS = [
  { key: "topic", label: "Big picture", labelKey: "research.hb.group.topic" },
  { key: "gate", label: "Gate chain", labelKey: "research.hb.group.gate" },
  { key: "state", label: "Lifecycle states", labelKey: "research.hb.group.state" },
] as const;

const topicEntries: HandbookEntry[] = [
  pipelineEntry,
  chainOverviewEntry,
  uiGuideEntry,
  masterFaqEntry,
  discoveryEntry,
  lifecycleEntries.find((e) => e.id === "topic/lifecycle") as HandbookEntry,
  registryEntry,
  scoringEntry,
  economicsEntry,
  evidenceEntry,
  experimentsEntry,
  operationsEntry,
  healthEntry,
  glossaryEntry,
];

/** Gate entries in canonical chain order (overview excluded — it is a topic). */
export const gateEntriesInChainOrder: HandbookEntry[] = pipelineEntries.filter((e) => e.kind === "gate");

const stateEntries: HandbookEntry[] = lifecycleEntries.filter((e) => e.kind === "state");

/** Every entry in TOC order (topics, chain, states). */
export const HANDBOOK_ENTRIES: HandbookEntry[] = [...topicEntries, ...gateEntriesInChainOrder, ...stateEntries];

// Sole-source guard: ids must be unique across the whole handbook.
const seen = new Set<string>();
for (const entry of HANDBOOK_ENTRIES) {
  if (seen.has(entry.id)) throw new Error(`duplicate handbook id: ${entry.id}`);
  seen.add(entry.id);
}

const normalize = (s: string): string => s.toLowerCase().replace(/[_\-/]+/g, " ").replace(/\s+/g, " ").trim();

/** Full-text match across title/subtitle/keywords/section prose (AND terms). */
export function matchesQuery(entry: HandbookEntry, rawQuery: string): boolean {
  const q = normalize(rawQuery);
  if (!q) return true;
  const haystack = normalize(
    [
      entry.id,
      entry.title,
      entry.subtitle,
      entry.badge ?? "",
      entry.source,
      ...(entry.keywords ?? []),
      ...entry.sections.map((s) => `${s.heading} ${s.body.join(" ")} ${(s.bullets ?? []).join(" ")}`),
      ...(entry.params ?? []).map((p) => `${p.name} ${p.value} ${p.meaning} ${p.ref}`),
      ...(entry.faq ?? []).map((f) => `${f.q} ${f.a}`),
    ].join(" "),
  );
  // Every query term must appear (AND semantics) so two words narrow, not sprawl.
  return q.split(" ").every((term) => haystack.includes(term));
}

/**
 * Translator-aware overlay: builders returning the SAME entry ids with
 * translated prose. Lane modules register here as they land; ids absent
 * from the overlay fall back to the static English entry (static pool
 * still feeds search-count tests and the cross-link maps).
 */
function translatedEntries(t: ScoringTranslate): HandbookEntry[] {
  return [
    scoringEntryTranslated(t),
    // V5 slots — gates/*
    // V5 slots — lifecycle, operations, glossary
    // V5 slots — uiguide, health, evidence, discovery
    // V5 slots — economics, faq, experiments, registry
  ];
}

/** Filter + group for the playbook view (groups keep TOC order).
 *  Pass the store's t() to translate entry prose AND search in that language. */
export function queryHandbook(
  rawQuery: string,
  t?: ScoringTranslate,
): Array<{ key: string; label: string; labelKey: string; entries: HandbookEntry[] }> {
  const overlay = t ? new Map(translatedEntries(t).map((e) => [e.id, e])) : null;
  const pick = (e: HandbookEntry): HandbookEntry => overlay?.get(e.id) ?? e;
  return HANDBOOK_GROUPS.map((group) => ({
    key: group.key,
    label: group.label,
    labelKey: group.labelKey,
    entries: HANDBOOK_ENTRIES.map(pick).filter((e) => e.kind === group.key && matchesQuery(e, rawQuery)),
  })).filter((g) => g.entries.length > 0);
}

export function countEntries(rawQuery = ""): number {
  return HANDBOOK_ENTRIES.filter((e) => matchesQuery(e, rawQuery)).length;
}

export function getEntry(id: string | undefined): HandbookEntry | undefined {
  if (!id) return undefined;
  return HANDBOOK_ENTRIES.find((e) => e.id === id);
}

/** Lifecycle-state entries keyed by state id (DISCOVERED -> entry). */
export const STATE_ENTRIES: ReadonlyMap<string, HandbookEntry> = new Map(
  stateEntries.map((e) => [e.id.slice("state/".length), e]),
);

/** Gate entries keyed by gate name (BACKTEST -> entry). */
export const GATE_ENTRIES: ReadonlyMap<string, HandbookEntry> = new Map(
  gateEntriesInChainOrder.map((e) => [e.id.slice("gate/".length), e]),
);
