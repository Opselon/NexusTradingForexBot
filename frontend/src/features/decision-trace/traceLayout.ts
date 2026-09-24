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
