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
 */

import { NOT_OBSERVED, UNMAPPED } from "./traceGraph";
import type { PositionedEdge, PositionedNode } from "./traceLayout";

/**
 * Translator seam: canvas-drawn markers are DISPLAY, so the caller (the
 * React layer) passes `t` from useI18n. Optional keeps this file pure and
 * unit-testable; without it markers render their English fallback.
 */
export type CanvasT = (key: string, fallback: string, vars?: Record<string, string | number>) => string;

export interface NodeVisual {
  node: PositionedNode;
  label: string;
  sublabel: string;
  tone: string;
  pulse: boolean;
  width: number;
  height: number;
  badge: string | null;
}

export interface EdgeVisual {
  edge: PositionedEdge;
  path: string;
  tone: string;
  dash: string;
  width: number;
}

export const NODE_W = 116;
export const NODE_H = 42;

function strOr(x: unknown, fb: string): string {
  return typeof x === "string" && x.trim() ? x : fb;
}
export { strOr };

export function nodeTone(node: PositionedNode): string {
  if (node.terminal) return "terminal";
  if (node.unmapped) return "unmapped";
  if (node.provenanceGap) return "gap";
  return "stage";
}

export function describeNode(node: PositionedNode, t?: CanvasT): NodeVisual {
  const label = node.stage;
  const d = node.lastStatus;
  const mark = (key: string, fb: string): string => (t ? t(key, fb) : fb);
  const bits: string[] = [];
  if (node.count > 1) bits.push(`${node.count}×`);
  if (d) bits.push(d);
  if (node.unmapped) bits.push(mark("trace.marker.unmapped", UNMAPPED));
  return {
    node,
    label,
    sublabel: bits.length ? bits.join(" · ") : mark("trace.marker.not_observed", NOT_OBSERVED),
    tone: nodeTone(node),
    pulse: false,
    width: NODE_W,
    height: NODE_H,
    badge: node.count > 1 ? String(node.count) : null,
  };
}

export function describeEdge(edge: PositionedEdge): EdgeVisual {
  // Slight vertical curvature so parallel edges between the same two columns
  // don't collapse into one line (branch visibility, §69).
  const mx = (edge.x1 + edge.x2) / 2;
  const path = `M ${edge.x1} ${edge.y1} C ${mx} ${edge.y1}, ${mx} ${edge.y2}, ${edge.x2} ${edge.y2}`;
  return {
    edge,
    path,
    tone: "default",
    dash: "none",
    width: Math.max(1, Math.min(3, edge.weight * 3)),
  };
}

import type { CanvasLayout } from "./traceLayout";

export function describeCanvas(layout: CanvasLayout, t?: CanvasT): { nodes: NodeVisual[]; edges: EdgeVisual[] } {
  return {
    nodes: layout.nodes.map((n) => describeNode(n, t)),
    edges: layout.edges.map(describeEdge),
  };
}

export function strOrNull(x: unknown): string | null {
  return typeof x === "string" && x.trim() ? x : null;
}
