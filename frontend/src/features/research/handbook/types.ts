/**
 * Research playbook — content model (type-only module).
 *
 * Every handbook entry is STATIC DOCUMENTATION compiled from the backend
 * sources named in `source`. None of it is live data: the page labels the
 * whole playbook as documentation, and live numbers always come from the
 * backend queries (summary/registry/gates/...). Values quoted inside
 * `params` are the constants that exist in the named source file at the
 * time of writing; tests/js/research_handbook.test.js re-reads those
 * sources and fails if the docs drift from the code.
 *
 * Type-only imports are used across handbook modules on purpose: Node's
 * type stripping erases them before resolution, so each content module
 * stays standalone-loadable from the regression test without a bundler.
 */

/** Coarse grouping used by the playbook TOC. */
export type HandbookKind = "gate" | "state" | "topic";

/** One name/value/meaning row (constants lifted verbatim from source). */
export interface ParamRow {
  /** Constant or knob name as it appears in the source. */
  name: string;
  /** Verbatim value (or faithful rendering of it). */
  value: string;
  /** What it controls, in operator terms. */
  meaning: string;
  /** Source symbol reference, e.g. "discovery.py::MIN_FAMILY_SAMPLES". */
  ref: string;
}

/** One FAQ pair inside an entry. */
export interface FaqItem {
  q: string;
  a: string;
}

/** A prose section inside an entry: heading + paragraphs + optional list. */
export interface Section {
  heading: string;
  /** Paragraph strings — each renders as its own <p>. */
  body: string[];
  /** Optional bullet list rendered under the paragraphs. */
  bullets?: string[];
}

/** One playbook entry (gate, lifecycle state, or cross-cutting topic). */
export interface HandbookEntry {
  /** Unique id: "gate/<GATE_TYPE>", "state/<STATE>", "topic/<slug>". */
  id: string;
  kind: HandbookKind;
  title: string;
  /** One-line deck shown under the title. */
  subtitle: string;
  /** Short chip shown next to the title (e.g. "GATE 3 / 6"). */
  badge?: string;
  /** Backend source file the entry is compiled from. */
  source: string;
  /** Ordered prose sections — the body of the entry. */
  sections: Section[];
  /** Constants table (verbatim values from `source`). */
  params?: ParamRow[];
  /** Operator FAQ. */
  faq?: FaqItem[];
  /** Related entry ids (rendered as cross-link chips). */
  seeAlso?: string[];
  /** Extra search terms (ids, enum words, synonyms). */
  keywords?: string[];
}
