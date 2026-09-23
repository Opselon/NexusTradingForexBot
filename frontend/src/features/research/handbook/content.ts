/**
 * Research handbook — aggregation, lookup, and full-text search.
 *
 * PURPOSE: the whole handbook corpus (gates, lifecycle states, topics) in one
 * module so `research_handbook.test.js` and the UI can read the same corpus.
 *
 * OWNER: lane D (features/research). This is the lazy-loaded handbook core —
 * the strategy playbook prose lives here, not in the ResearchPage route chunk
 * (wave-7 load work: /research first paint downloads only the pipeline hero's
 * tiny gate-chain list; the playbook fetches on first tab open).
 *
 * CONSUMES: every leaf entry module under ./gates, ./lifecycle and the topic
 * files, plus the leaf type from ./types.
 *
 * PROVIDES: HANDBOOK_ENTRIES, queryHandbook, countEntries, getEntry,
 * STATE_ENTRIES, GATE_ENTRIES and the derived entry lists every consumer
 * (StrategyPlaybook, drawer deep-links, tests) reads.
 *
 * INVARIANTS:
 *  - entry ids are globally unique (the sole-source guard below throws on a
 *    duplicate, at import time, in every environment that loads this module);
 *  - group order = TOC order: big picture, then the gate chain, then the
 *    lifecycle states in state-machine order;
 *  - search is AND-semantics over normalized title/subtitle/keywords/prose —
 *    two query terms narrow, never sprawl;
 *  - this module is documentation only: it never imports a query, never
 *    derives a live number.
 *
 * EXTEND: add a leaf entry module, import it below in the group it belongs to,
 * re-run `node tests/js/research_handbook.test.js` (pins constants against the
 * Python sources) and the Vite build.
 */

import type { HandbookEntry } from "./types";
import type { ScoringTranslate } from "./scoring";
import { gatesTranslated } from "./gates";

export type { HandbookEntry, Section, ParamRow, FaqItem, HandbookKind } from "./types";
import { chainOverviewEntry, pipelineEntries } from "./gates";
import { lifecycleEntries, lifecycleTranslated } from "./lifecycle";
import { discoveryEntry, discoveryTranslated } from "./discovery";
import { scoringEntry, scoringEntryTranslated } from "./scoring";
import { economicsEntry, economicsTranslated } from "./economics";
import { evidenceEntry, evidenceTranslated } from "./evidence";
import { operationsEntry, pipelineEntry, operationsTranslated } from "./operations";
import { uiGuideEntry, uiguideTranslated } from "./uiguide";
import { masterFaqEntry, faqTranslated } from "./faq";
import { registryEntry, registryTranslated } from "./registry";
import { experimentsEntry, experimentsTranslated } from "./experiments";
import { glossaryEntry, glossaryTranslated } from "./glossary";
import { healthEntry, healthTranslated } from "./health";

/** Every handbook entry, translator-aware. Static pool above stays byte-identical
 *  for tests/search; this overlay swaps prose by id at render time. */
function translatedEntries(t: ScoringTranslate): HandbookEntry[] {
  return [
    ...gatesTranslated(t),
    ...lifecycleTranslated(t),
    ...operationsTranslated(t),
    ...glossaryTranslated(t),
    ...uiguideTranslated(t),
    ...healthTranslated(t),
    ...evidenceTranslated(t),
    ...discoveryTranslated(t),
    economicsTranslated(t),
    faqTranslated(t),
    experimentsTranslated(t),
    registryTranslated(t),
    scoringEntryTranslated(t),
  ];
}

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

export function getEntry(id: string | undefined, t?: ScoringTranslate): HandbookEntry | undefined {
  if (!id) return undefined;
  const entry = HANDBOOK_ENTRIES.find((e) => e.id === id);
  if (!entry || !t) return entry;
  return translatedEntries(t).find((e) => e.id === id) ?? entry;
}

/** Lifecycle-state entries keyed by state id (DISCOVERED -> entry). */
export const STATE_ENTRIES: ReadonlyMap<string, HandbookEntry> = new Map(
  stateEntries.map((e) => [e.id.slice("state/".length), e]),
);

/** Gate entries keyed by gate name (BACKTEST -> entry). */
export const GATE_ENTRIES: ReadonlyMap<string, HandbookEntry> = new Map(
  gateEntriesInChainOrder.map((e) => [e.id.slice("gate/".length), e]),
);
