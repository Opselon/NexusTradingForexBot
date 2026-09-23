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

export type { HandbookEntry, Section, ParamRow, FaqItem, HandbookKind } from "./types";
import { chainOverviewEntry, pipelineEntries } from "./gates";
import { lifecycleEntries } from "./lifecycle";
import { discoveryEntry } from "./discovery";
import { scoringEntry } from "./scoring";
import { economicsEntry } from "./economics";
import { evidenceEntry } from "./evidence";
import { operationsEntry, pipelineEntry } from "./operations";
import { uiGuideEntry } from "./uiguide";
import { masterFaqEntry } from "./faq";
import { registryEntry } from "./registry";
import { experimentsEntry } from "./experiments";
import { glossaryEntry } from "./glossary";
import { healthEntry } from "./health";

/** Ordered display groups for the playbook TOC. */
export const HANDBOOK_GROUPS = [
  { key: "topic", label: "Big picture" },
  { key: "gate", label: "Gate chain" },
  { key: "state", label: "Lifecycle states" },
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

/** Filter + group for the playbook view (groups keep TOC order). */
export function queryHandbook(
  rawQuery: string,
): Array<{ key: string; label: string; entries: HandbookEntry[] }> {
  return HANDBOOK_GROUPS.map((group) => ({
    key: group.key,
    label: group.label,
    entries: HANDBOOK_ENTRIES.filter((e) => e.kind === group.key && matchesQuery(e, rawQuery)),
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
