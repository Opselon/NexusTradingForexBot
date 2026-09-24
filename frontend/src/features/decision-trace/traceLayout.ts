/**
 * traceLayout — force-free layered layout for the runtime topology canvas.
 *
 * THE CORE RULE (§13/§51): no hardcoded trading topology. X positions come
 * from `rank` — a visualization hint derived by buildGraph from the ORDER
 * stages actually appeared in the observed stream (RANK in traceGraph is a
 * display order, never business logic; unknown stages land after the known
 * ones by first-seen order). Y positions come from branch separation.
 *
 * Deterministic + incremental: pure function of (nodes, edges), so a live
 * re-render never re-flows the graph unless the topology actually changes
 * (§66).
 */

import type { DerivedGraph, GraphEdge, GraphNode } from "./traceGraph";

export interface PositionedNode extends GraphNode {
  x: number;
  y: number;
}

export interface PositionedEdge extends GraphEdge {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

export interface CanvasLayout {
  nodes: PositionedNode[];
  edges: PositionedEdge[];
  width: number;
  height: number;
  columns: number;
}

const COLUMN_W = 132;
const ROW_H = 96;
const MARGIN_X = 70;
const MARGIN_Y = 56;

function hashSeed(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i += 1) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

/** Column = observed rank; row = branch slot measured from real edges. */
export function layoutCanvas(graph: DerivedGraph): CanvasLayout {
  const nodesIn = graph.nodes;
  if (!nodesIn.length) return { nodes: [], edges: [], width: 0, height: 0, columns: 0 };

  const byId = new Map<string, GraphNode>();
  nodesIn.forEach((n) => byId.set(n.id, n));

  // ---- 1. columns: rank (observed first-appearance order), ties broken
  //         alphabetically for stability.
  const ranks = nodesIn.map((n) => n.rank);
  const minRank = Math.min(...ranks);
  const ordered = nodesIn
    .slice()
    .sort((a, b) => a.rank - b.rank || a.stage.localeCompare(b.stage));
  const colOf = new Map<string, number>();
  ordered.forEach((n, i) => colOf.set(n.id, i - (Number.isFinite(minRank) ? 0 : 0)));

  // ---- 2. rows: BFS from roots; branches take fresh slots, merges average.
  const rowOf = new Map<string, number>();
  const usedSlots = new Set<string>();
  const indeg = new Map<string, number>();
  nodesIn.forEach((n) => indeg.set(n.id, 0));
  graph.edges.forEach((e) => indeg.set(e.target, (indeg.get(e.target) ?? 0) + 1));
  const roots = ordered.filter((n) => (indeg.get(n.id) ?? 0) === 0).map((n) => n.id);
  const firstNode = ordered[0];
  const start = roots.length ? roots : firstNode ? [firstNode.id] : [];

  const outEdges = new Map<string, string[]>();
  graph.edges.forEach((e) => {
    if (!outEdges.has(e.source)) outEdges.set(e.source, []);
    outEdges.get(e.source)!.push(e.target);
  });

  const queue: string[] = [];
  const seen = new Set<string>();
  let nextRow = 0;
  start.forEach((id) => {
    rowOf.set(id, nextRow);
    usedSlots.add(`${colOf.get(id)}:${nextRow}`);
    nextRow += 1;
    queue.push(id);
  });

  while (queue.length) {
    const id = queue.shift()!;
    if (seen.has(id)) continue;
    seen.add(id);
    const kids = outEdges.get(id) ?? [];
    const parentRow = rowOf.get(id) ?? nextRow;
    kids.forEach((kid, idx) => {
      if (rowOf.has(kid)) return; // already slotted (multi-parent merge)
      const col = colOf.get(kid) ?? 0;
      const parents = graph.edges.filter((e) => e.target === kid);
      let row: number;
      if (parents.length > 1) {
        const pRows = parents.map((e) => rowOf.get(e.source)).filter((r): r is number => r !== undefined);
        row = pRows.length
          ? Math.round(pRows.reduce((a, b) => a + b, 0) / pRows.length)
          : nextRow;
      } else {
        row = idx === 0 && !usedSlots.has(`${col}:${parentRow}`) ? parentRow : nextRow;
      }
      while (usedSlots.has(`${col}:${row}`)) row += 1;
      rowOf.set(kid, row);
      usedSlots.add(`${col}:${row}`);
      nextRow = Math.max(nextRow, row + 1);
      queue.push(kid);
    });
  }
  // isolated/unreached nodes (evidence-only, e.g. a stage seen alone)
  ordered.forEach((n) => {
    if (rowOf.has(n.id)) return;
    const col = colOf.get(n.id)!;
    let row = 0;
    while (usedSlots.has(`${col}:${row}`)) row += 1;
    rowOf.set(n.id, row);
    usedSlots.add(`${col}:${row}`);
  });

  const columns = ordered.length;
  const rows = Math.max(...Array.from(rowOf.values())) + 1;

  const posOf = new Map<string, PositionedNode>();
  const positioned: PositionedNode[] = ordered.map((n) => {
    const col = colOf.get(n.id)!;
    const row = rowOf.get(n.id)!;
    const jitter = ((hashSeed(`${n.id}:y`) % 1000) / 1000 - 0.5) * 24;
    const p: PositionedNode = {
      ...n,
      x: MARGIN_X + col * COLUMN_W + COLUMN_W / 2,
      y: MARGIN_Y + row * ROW_H + ROW_H / 2 + jitter,
    };
    posOf.set(n.id, p);
    return p;
  });

  const edgesL: PositionedEdge[] = [];
  graph.edges.forEach((e, i) => {
    const a = posOf.get(e.source);
    const b = posOf.get(e.target);
    if (!a || !b) return;
    edgesL.push({
      ...e,
      // parallel-edge slot so two branches between the same pair separate
      x1: a.x,
      y1: a.y,
      x2: b.x,
      y2: b.y,
      count: e.count,
      weight: e.weight,
      id: e.id || `${e.source}->${e.target}#${i}`,
      source: e.source,
      target: e.target,
      firstSeen: e.firstSeen,
    });
  });

  return {
    nodes: positioned,
    edges: edgesL,
    width: MARGIN_X * 2 + columns * COLUMN_W,
    height: MARGIN_Y * 2 + rows * ROW_H,
    columns,
  };
}

export const CANVAS_CONST = { COLUMN_W, ROW_H, MARGIN_X, MARGIN_Y };

/* ===========================================================================
 * §8/§12/§44 precomputation — §4 fields the LAYOUT layer needs (a node is a
 * failure witness; its external kind). Computed HERE, from graph fields, so
 * this module never needs a sibling VALUE import (Node's TS-stripping gate
 * runner cannot resolve extensionless relative imports, and tsc forbids the
 * `.ts` suffix — see traceCanvas.ts for the same constraint).
 * ======================================================================== */
function isLayoutFailureWord(word: string | null | undefined): boolean {
  if (!word) return false;
  const t = word.toUpperCase();
  // §8: rejected/error/blocked tones — mirror of stateToneOf in traceGraph
  // (kept local for the same import-free reason).
  return (
    t === "REJECTED" ||
    t === "REJECT" ||
    t === "FAILED" ||
    t === "ERROR" ||
    t === "TIMEOUT" ||
    t === "BLOCKED"
  );
}

function isLayoutFailureNode(n: GraphNode): boolean {
  return (
    isLayoutFailureWord(n.state) ||
    isLayoutFailureWord(n.lastStatus) ||
    n.errors > 0
  );
}

export interface Viewport {
  x: number;
  y: number;
  k: number;
}

export const VIEW_K_MIN = 0.4;
export const VIEW_K_MAX = 2.4;

function clampK(k: number): number {
  if (!Number.isFinite(k)) return 1;
  return Math.min(VIEW_K_MAX, Math.max(VIEW_K_MIN, k));
}

export interface ContentBBox {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
}

/** Bounding box of node centers (null for an empty layout). */
export function contentBBox(nodes: readonly PositionedNode[]): ContentBBox | null {
  if (!nodes.length) return null;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const n of nodes) {
    if (n.x < minX) minX = n.x;
    if (n.y < minY) minY = n.y;
    if (n.x > maxX) maxX = n.x;
    if (n.y > maxY) maxY = n.y;
  }
  return { minX, minY, maxX, maxY };
}

/**
 * FIT: zoom+center so the whole graph fits the viewport (§52).
 * Deterministic; empty layout => identity transform.
 */
export function fitViewport(layout: CanvasLayout, vw: number, vh: number, pad = 56): Viewport {
  const box = contentBBox(layout.nodes);
  if (!box || vw <= 0 || vh <= 0) return { x: 0, y: 0, k: 1 };
  const bw = Math.max(box.maxX - box.minX, 1) + pad * 2;
  const bh = Math.max(box.maxY - box.minY, 1) + pad * 2;
  const k = clampK(Math.min(vw / bw, vh / bh));
  const cx = (box.minX + box.maxX) / 2;
  const cy = (box.minY + box.maxY) / 2;
  return { x: vw / 2 - cx * k, y: vh / 2 - cy * k, k };
}

/**
 * FOCUS: center the given nodes (e.g. the selected stage or the active
 * trace's region) at the current zoom (§52 focus, §53 auto-follow). Unknown
 * ids fall back to the whole layout so a stale selection never blanks the
 * canvas. Keeps k unchanged — focusing never yanks the zoom level.
 */
export function focusViewport(
  layout: { nodes: readonly PositionedNode[] },
  nodeIds: ReadonlySet<string> | readonly string[],
  vw: number,
  vh: number,
  k: number,
): Viewport {
  const want = nodeIds instanceof Set ? nodeIds : new Set(nodeIds);
  const targets = layout.nodes.filter((n) => want.has(n.id));
  const box = contentBBox(targets.length ? targets : layout.nodes);
  const kk = clampK(k);
  if (!box || vw <= 0 || vh <= 0) return { x: 0, y: 0, k: kk };
  const cx = (box.minX + box.maxX) / 2;
  const cy = (box.minY + box.maxY) / 2;
  return { x: vw / 2 - cx * kk, y: vh / 2 - cy * kk, k: kk };
}

/** A filtered render view: nodes/edges kept for drawing after user focus. */
export interface ViewGroup {
  nodes: PositionedNode[];
  edges: PositionedEdge[];
  /** node ids excluded from this render (collapse/isolation) — never deleted. */
  hidden: ReadonlySet<string>;
  /** collapsed node -> number of DIRECT children it swallowed (badge `+N`). */
  collapsedCounts: ReadonlyMap<string, number>;
}

export interface ViewOptions {
  /** node ids the user collapsed (their subtrees hide, §8 carve-outs stay). */
  collapsed?: ReadonlySet<string>;
  /** stage name to branch-isolate to (its 1-hop neighbourhood stays, §52). */
  isolateStage?: string | null;
}

const EMPTY_IDS: ReadonlySet<string> = new Set<string>();

function outAdj(layout: CanvasLayout): Map<string, string[]> {
  const m = new Map<string, string[]>();
  for (const e of layout.edges) {
    const arr = m.get(e.source) ?? [];
    arr.push(e.target);
    m.set(e.source, arr);
  }
  return m;
}

function inAdj(layout: CanvasLayout): Map<string, string[]> {
  const m = new Map<string, string[]>();
  for (const e of layout.edges) {
    const arr = m.get(e.target) ?? [];
    arr.push(e.source);
    m.set(e.target, arr);
  }
  return m;
}

/** §8 hard rule — a failure witness and the path that led to it stay visible. */
function preserveFailurePaths(layout: CanvasLayout, hidden: Set<string>): void {
  const fails = layout.nodes.filter((n) => isLayoutFailureNode(n));
  if (!fails.length) return;
  const parents = inAdj(layout);
  for (const f of fails) {
    hidden.delete(f.id);
    const queue = [...(parents.get(f.id) ?? [])];
    const seen = new Set<string>(queue);
    while (queue.length) {
      const id = queue.pop()!;
      hidden.delete(id);
      for (const p of parents.get(id) ?? []) {
        if (!seen.has(p)) {
          seen.add(p);
          queue.push(p);
        }
      }
    }
  }
}

/**
 * COLLAPSE: hides the downstream subtree of the collapsed nodes.
 * Failure/rejection witnesses are never hidden, and neither is any node on a
 * path from the root to one (§8) — collapsing a clean branch cannot make a
 * failure disappear. Expand = collapse with an empty set (identity).
 */
export function selectView(layout: CanvasLayout, opts: ViewOptions = {}): ViewGroup {
  const collapsed = opts.collapsed ?? EMPTY_IDS;
  const hidden = new Set<string>();
  const counts = new Map<string, number>();

  if (collapsed.size) {
    const outs = outAdj(layout);
    const present = new Set(layout.nodes.map((n) => n.id));
    const fails = new Set(layout.nodes.filter((n) => isLayoutFailureNode(n)).map((n) => n.id));
    const visited = new Set<string>();
    for (const root of collapsed) {
      if (!present.has(root)) continue;
      const queue = [...(outs.get(root) ?? [])];
      while (queue.length) {
        const id = queue.shift()!;
        if (visited.has(id)) continue;
        visited.add(id);
        if (fails.has(id)) continue; // §8: keep it AND its subtree visible
        hidden.add(id);
        for (const t of outs.get(id) ?? []) queue.push(t);
      }
      counts.set(root, (outs.get(root) ?? []).filter((t) => hidden.has(t)).length);
    }
  }

  if (opts.isolateStage) {
    const stageNodes = layout.nodes.filter((n) => n.stage === opts.isolateStage);
    if (stageNodes.length) {
      const keep = new Set<string>();
      const stageIds = new Set(stageNodes.map((n) => n.id));
      for (const id of stageIds) keep.add(id);
      for (const e of layout.edges) {
        // 1-hop neighbourhood of the selected stage (§52 branch isolation)
        if (stageIds.has(e.source)) keep.add(e.target);
        if (stageIds.has(e.target)) keep.add(e.source);
      }
      for (const n of layout.nodes) if (!keep.has(n.id)) hidden.add(n.id);
    }
  }

  preserveFailurePaths(layout, hidden);

  const nodes = layout.nodes.filter((n) => !hidden.has(n.id));
  const edges = layout.edges.filter((e) => !hidden.has(e.source) && !hidden.has(e.target));
  return { nodes, edges, hidden, collapsedCounts: counts };
}
