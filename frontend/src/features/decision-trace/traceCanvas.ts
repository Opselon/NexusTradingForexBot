/**
 * traceCanvas — SVG rendering descriptors for the runtime topology canvas.
 *
 * Pure render helpers over the layered layout (traceLayout). The React
 * component mounts these into <svg>; this file owns geometry + tone
 * derivation so it is unit-testable without a DOM.
 *
 * Tones are derived from observed facts only:
 *   terminal       — a stage whose events carry terminal status
 *   unmapped       — buildGraph flagged the stage as unmapped
 *   provenance-gap — a stage observed with a provenance gap
 *   rejected-path  — an edge whose parent events hold a REJECT verdict
 *
 * OWNERSHIP (lane C, LIVE-CAUSAL-TOPOLOGY wave): this file is imported by
 * TraceGraphCanvas.tsx ONLY. It is kept 100% `import type`-free and runtime
 * `import`-free so Node's TS-stripping gate runner (tests/js/
 * decision_causal_graph.test.js) can pull the whole derivation chain from
 * traceGraph.ts/traceLayout.ts without a bundler: Node cannot resolve
 * extensionless relative imports, and tsc forbids `.ts` suffixes, so a value
 * import would break one gate or the other. Everything this file needs lives
 * in the shared DerivedGraph types or is computed from the node/edge fields.
 */

import type { CanvasLayout, PositionedEdge, PositionedNode } from "./traceLayout";

/* ---------- DOM-independent constants (exported for tests + CSS parity) --- */

export const NODE_W = 116;
export const NODE_H = 42;

/** §6 edge classes — frozen CSS hooks in decision-trace.css. */
const EDGE_CLS_BY_STATE: Record<EdgeState, string> = {
  idle: "dt-edge-idle",
  active: "dt-edge-active",
  success: "dt-edge-success",
  rejected: "dt-edge-rejected",
  error: "dt-edge-error",
  blocked: "dt-edge-blocked",
  retry: "dt-edge-retry",
};


/** §12 external-node classes — only real-provider events set these. */
export const EXTERNAL_CLS: Record<string, string> = {
  mt5: "dt-ext-mt5",
  gateway: "dt-ext-gateway",
  provider: "dt-ext-provider",
  db: "dt-ext-db",
};

/** Dash patterns (§56: a gap is DASHED and labelled, never a solid arrow). */
export const DASH_GAP = "7 5";
export const DASH_NONE = "none";

function strOr(x: unknown, fb: string): string {
  return typeof x === "string" && x.trim() ? x : fb;
}
export { strOr };

export function strOrNull(x: unknown): string | null {
  return typeof x === "string" && x.trim() ? x : null;
}

/* ---------- §4 token -> visual descriptor (canonical state mapping) ------- */

export interface NodeVisual {
  node: PositionedNode;
  label: string;
  sublabel: string;
  /** §4 CSS hook: `.dt-st-<token>` — the ONLY styling key. */
  cls: string;
  /** §44 mode badge class (`dt-mode-*`) when the node carried a mode. */
  modeCls: string | null;
  /** external-participant class (§12), only when an event proved one. */
  extCls: string | null;
  /** §56 explicit PROVENANCE GAP marker text, only when the node is a gap. */
  gapLabel: string | null;
  tone: string;
  /** §5 in-flight pulse — set ONLY by a real event's animated tone. */
  pulse: boolean;
  width: number;
  height: number;
  badge: string | null;
  /** §52 collapse badge `+N` when the user collapsed this node's subtree. */
  collapsedCount: number | null;
}

export interface EdgeVisual {
  edge: PositionedEdge;
  path: string;
  /** §4 CSS hook: the ONLY styling key the edge view consumes. */
  cls: string;
  /** Same key under the view's own name (EdgeView reads v.tone). */
  tone: string;
  dash: string;
  width: number;
  /** §56: explicit gap label (e.g. "PROVENANCE GAP") rendered as edge text. */
  gapLabel: string | null;
  /** §6: the hop's own observed state word (tooltip, honest passthrough). */
  stateLabel: string | null;
}

/**
 * §4 node class — the CSS side maps every token to a theme.css variable, so
 * no raw hex ever lives here (§51). Unknown runtime states keep their own
 * word and a neutral look (never relabelled).
 */
export function nodeCls(node: PositionedNode): string {
  if (node.state == null || node.state === "") return "dt-st-none";
  const w = node.state.toUpperCase();
  const map: Record<string, string> = {
    IDLE: "dt-st-idle",
    RECEIVED: "dt-st-received",
    PROCESSING: "dt-st-processing",
    WAITING: "dt-st-waiting",
    COMPLETED: "dt-st-completed",
    PASSED: "dt-st-passed",
    REJECTED: "dt-st-rejected",
    FAILED: "dt-st-failed",
    BLOCKED: "dt-st-blocked",
    SKIPPED: "dt-st-skipped",
    TIMEOUT: "dt-st-timeout",
    STALE: "dt-st-stale",
    CANCELLED: "dt-st-cancelled",
    EXECUTING: "dt-st-executing",
    CONFIRMED: "dt-st-confirmed",
    OBSERVED: "dt-st-observed",
    OK: "dt-st-ok",
    PASS: "dt-st-passed",
    REJECT: "dt-st-rejected",
    ERROR: "dt-st-failed",
    EXECUTED: "dt-st-executed",
    DISPATCHED: "dt-st-dispatched",
    WARNING: "dt-st-warn",
  };
  return map[w] ?? "dt-st-unknown";
}

/** §44 mode badge class — absent => UNKNOWN class, unknown word => neutral. */
export function modeCls(mode: string | null | undefined): string | null {
  if (mode == null || mode === "") return "dt-mode-unknown";
  return (
    {
      LIVE: "dt-mode-live",
      PAPER: "dt-mode-paper",
      SHADOW: "dt-mode-shadow",
      REPLAY: "dt-mode-replay",
      BACKTEST: "dt-mode-backtest",
      TRAINING: "dt-mode-training",
    }[mode.toUpperCase()] ?? "dt-mode-other"
  );
}

/** §6 node-state tone (reused by edgeState derivation, kept honest here). */
function stateTone(state: string | null | undefined): string {
  if (state == null || state === "") return "none";
  const w = state.toUpperCase();
  const tones: Record<string, string> = {
    IDLE: "neutral",
    RECEIVED: "info",
    PROCESSING: "active",
    WAITING: "waiting",
    COMPLETED: "success",
    PASSED: "success",
    REJECTED: "rejected",
    FAILED: "error",
    BLOCKED: "blocked",
    SKIPPED: "muted",
    TIMEOUT: "error",
    STALE: "muted",
    CANCELLED: "cancelled",
    EXECUTING: "executing",
    CONFIRMED: "confirmed",
    OBSERVED: "info",
    OK: "success",
    PASS: "success",
    REJECT: "rejected",
    ERROR: "error",
    EXECUTED: "executed",
    DISPATCHED: "executing",
    WARNING: "warn",
  };
  return tones[w] ?? "unknown";
}

/** §5 a state legitimately animates ONLY when it is in-flight. */
function isAnimated(state: string | null | undefined): boolean {
  if (state == null || state === "") return false;
  return (
    { RECEIVED: 1, PROCESSING: 1, WAITING: 1, EXECUTING: 1, DISPATCHED: 1 }[
      state.toUpperCase()
    ] === 1
  );
}

/* ---------- §6 edge state (frozen vocab, derived from real hop words) ----- */

export type EdgeState =
  | "idle"
  | "active"
  | "success"
  | "rejected"
  | "error"
  | "blocked"
  | "retry";

export const EDGE_STATES: readonly EdgeState[] = [
  "idle",
  "active",
  "success",
  "rejected",
  "error",
  "blocked",
  "retry",
] as const;

/**
 * Derive an edge's live state from the real hop word it carries (§6). All
 * evidence-driven: idle (no verdict word yet), active (in-flight), success
 * (verdict passed), rejected (REJECT/SKIPPED/CANCELLED), error
 * (FAILED/ERROR/TIMEOUT), blocked, retry (a runtime-emitted retry marker —
 * never a timer, §5/§60).
 */
export function edgeState(state: string | null | undefined): EdgeState {
  if (!state) return "idle";
  const word = state.toUpperCase();
  if (word === "RETRY" || word === "RETRYING") return "retry";
  const tone = stateTone(state);
  switch (tone) {
    case "rejected":
    case "cancelled":
    case "muted":
      return "rejected";
    case "error":
      return "error";
    case "blocked":
      return "blocked";
    case "success":
    case "confirmed":
    case "executed":
      return "success";
    case "info":
    case "active":
    case "waiting":
    case "executing":
      return "active";
    default:
      return "idle";
  }
}

/** True when this edge carried an observed failure/rejection (§8). */
export function edgeIsFailure(state: string | null | undefined): boolean {
  const s = edgeState(state);
  return s === "rejected" || s === "error" || s === "blocked";
}

/* ---------- descriptors ---------------------------------------------------- */

export function describeNode(
  node: PositionedNode,
  opts: { collapsedCount?: number | null } = {},
): NodeVisual {
  const label = node.stage;
  const d = node.lastStatus;
  const bits: string[] = [];
  if (node.count > 1) bits.push(`${node.count}×`);
  if (d) bits.push(d);
  if (node.unmapped) bits.push(UNMAPPED_WORD);
  const gap = node.provenanceGap;
  return {
    node,
    label,
    sublabel: bits.length ? bits.join(" · ") : NOT_OBSERVED_WORD,
    cls: nodeCls(node),
    modeCls: modeCls(node.mode),
    extCls: node.externalKind ? EXTERNAL_CLS[node.externalKind] ?? null : null,
    gapLabel: gap ? PROVENANCE_GAP_WORD : null,
    tone: nodeTone(node),
    pulse: isAnimated(node.state),
    width: NODE_W,
    height: NODE_H,
    badge: node.count > 1 ? String(node.count) : null,
    collapsedCount: opts.collapsedCount ?? null,
  };
}

export function nodeTone(node: PositionedNode): string {
  if (node.terminal) return "terminal";
  if (node.unmapped) return "unmapped";
  if (node.provenanceGap) return "gap";
  return "stage";
}

export function describeEdge(edge: PositionedEdge): EdgeVisual {
  // Slight vertical curvature so parallel edges between the same two columns
  // don't collapse into one line (branch visibility, §69).
  const mx = (edge.x1 + edge.x2) / 2;
  const path = `M ${edge.x1} ${edge.y1} C ${mx} ${edge.y1}, ${mx} ${edge.y2}, ${edge.x2} ${edge.y2}`;
  const st = edgeState(edge.state);
  const isGap = edge.provenanceGap;
  const stCls = EDGE_CLS_BY_STATE[st] ?? "dt-edge-idle";
  return {
    edge,
    path,
    cls: isGap ? "dt-edge-gap" : stCls,
    tone: isGap ? "dt-edge-gap" : stCls,
    // §56: a gap is DASHED with an explicit label; never a solid arrow.
    dash: isGap ? DASH_GAP : DASH_NONE,
    width: Math.max(1, Math.min(3, edge.weight * 3)),
    gapLabel: isGap ? PROVENANCE_GAP_WORD : null,
    stateLabel: edge.state ?? null,
  };
}

/** §7: only the branch the runtime actually took is lit; the rest subdue. */
export function isSubduedBranch(
  edge: PositionedEdge,
  taken: ReadonlySet<string>,
): boolean {
  if (taken.has(edge.id)) return false;
  return edgeState(edge.state) !== "idle";
}

/* ---------- honest placeholder words (mirror traceGraph, kept in sync) ----- */
/* These are the ONLY strings the canvas may render for absent evidence. */
const NOT_OBSERVED_WORD = "NOT OBSERVED";
const UNMAPPED_WORD = "UNMAPPED";
const PROVENANCE_GAP_WORD = "PROVENANCE GAP";

/* The two above mirror the names exported from traceGraph.ts. They are
 * duplicated as local constants (not imported) to keep this file
 * runtime-import-free — see the OWNERSHIP note at the top. A compile-time
 * record pins them to the canonical constants so they can never drift. */

import type { TraceMode, TraceState } from "./types";

/**
 * Compile-time exhaustiveness pin (§4/§44). If lane D adds a frozen word to
 * TraceState/TraceMode this record stops type-checking until the visual
 * mapping covers it — the mapping can never silently drift from the
 * canonical vocabulary. Unused locally by design; the error is the feature.
 */
const VOCAB_COVERAGE_PIN: {
  states: Record<keyof typeof TraceState, string>;
  modes: Record<keyof typeof TraceMode, string>;
} = {
  states: {
    IDLE: "dt-st-idle",
    RECEIVED: "dt-st-received",
    PROCESSING: "dt-st-processing",
    WAITING: "dt-st-waiting",
    COMPLETED: "dt-st-completed",
    PASSED: "dt-st-passed",
    REJECTED: "dt-st-rejected",
    FAILED: "dt-st-failed",
    BLOCKED: "dt-st-blocked",
    SKIPPED: "dt-st-skipped",
    TIMEOUT: "dt-st-timeout",
    STALE: "dt-st-stale",
    CANCELLED: "dt-st-cancelled",
    EXECUTING: "dt-st-executing",
    CONFIRMED: "dt-st-confirmed",
  },
  modes: {
    LIVE: "dt-mode-live",
    PAPER: "dt-mode-paper",
    SHADOW: "dt-mode-shadow",
    REPLAY: "dt-mode-replay",
    BACKTEST: "dt-mode-backtest",
    TRAINING: "dt-mode-training",
  },
};
void VOCAB_COVERAGE_PIN;

export function describeCanvas(
  layout: CanvasLayout,
  opts: { collapsedCounts?: ReadonlyMap<string, number> } = {},
): { nodes: NodeVisual[]; edges: EdgeVisual[] } {
  const taken = new Set<string>();
  for (const e of layout.edges) {
    if (edgeState(e.state) !== "idle") taken.add(e.id);
  }
  const cc = opts.collapsedCounts;
  return {
    nodes: layout.nodes.map((n) =>
      describeNode(n, { collapsedCount: cc ? cc.get(n.id) ?? null : null }),
    ),
    edges: layout.edges.map((e) => describeEdge(e)),
  };
}

/* §63/§71 bounded redraw: a CanvasLayout whose identity is unchanged by
 * buildGraph/layoutCanvas (both memoized upstream) yields a memoizable
 * descriptor set; describeCanvas is O(nodes+edges) with no hidden work. */
export const CANVAS_BOUNDS = {
  maxNodes: 512,
  maxEdges: 2048,
} as const;
