/**
 * TraceGraphCanvas — the OBSERVABILITY WINDOW (§10/§11).
 *
 * Renders the runtime-discovered topology from layoutCanvas geometry:
 * every node exists because an EVENT proved it — no hardcoded trading path
 * exists in this component or its view-model. Wheel = zoom, drag = pan
 * (viewport transform only; data never mutated), click a node = focus its
 * evidence. Selected-trace participants are emphasized, non-participants
 * dimmed but never hidden (§69). Honesty tones: terminal / unmapped /
 * provenance-gap come straight from buildGraph flags.
 */

import { memo, useCallback, useMemo, useRef, useState } from "react";
import { describeCanvas } from "../traceCanvas";
import { layoutCanvas } from "../traceLayout";
import type { DerivedGraph } from "../traceGraph";
import type { TraceEvent } from "../types";

interface Props {
  graph: DerivedGraph;
  selectedTraceId: string | null;
  activeEvents: TraceEvent[] | null;
  onSelectStage: (stage: string | null) => void;
  selectedStage: string | null;
}

const MIN_K = 0.4;
const MAX_K = 2.4;

type NodeVisual = ReturnType<typeof describeCanvas>["nodes"][number];
type EdgeVisual = ReturnType<typeof describeCanvas>["edges"][number];

const NodeView = memo(function NodeView({
  v,
  dimmed,
  selected,
  onSelect,
}: {
  v: NodeVisual;
  dimmed: boolean;
  selected: boolean;
  onSelect: (stage: string) => void;
}) {
  return (
    <g
      className={`dt-node ${v.tone} ${selected ? "selected" : ""} ${dimmed ? "dim" : ""}`}
      transform={`translate(${v.node.x - v.width / 2} ${v.node.y - v.height / 2})`}
      onClick={() => onSelect(v.node.stage)}
      role="button"
      tabIndex={0}
      aria-label={`stage ${v.node.stage}, ${v.node.count} events`}
    >
      <rect width={v.width} height={v.height} rx={8} className="dt-node-body" />
      <text x={v.width / 2} y={17} textAnchor="middle" className="dt-node-label">{v.label}</text>
      <text x={v.width / 2} y={32} textAnchor="middle" className="dt-node-sub">{v.sublabel}</text>
      {v.badge ? (
        <g transform={`translate(${v.width + 2} -6)`}>
          <circle r={9} className="dt-badge" />
          <text y={3} textAnchor="middle" className="dt-badge-text">{v.badge}</text>
        </g>
      ) : null}
    </g>
  );
});

const EdgeView = memo(function EdgeView({ v, dimmed }: { v: EdgeVisual; dimmed: boolean }) {
  return (
    <g className={`dt-edge ${v.tone} ${dimmed ? "dim" : ""}`}>
      <path d={v.path} className="dt-edge-hit" />
      <path d={v.path} className="dt-edge-line" style={{ strokeWidth: v.width, strokeDasharray: v.dash }} />
      <title>{`${v.edge.source} → ${v.edge.target} · ${v.edge.count} ×`}</title>
    </g>
  );
});

export function TraceGraphCanvas({
  graph,
  selectedTraceId,
  activeEvents,
  onSelectStage,
  selectedStage,
}: Props) {
  const [vp, setVp] = useState({ x: 0, y: 0, k: 1 });
  const dragRef = useRef<{ x: number; y: number; vx: number; vy: number } | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);

  const layout = useMemo(() => layoutCanvas(graph), [graph]);
  const visuals = useMemo(() => describeCanvas(layout), [layout]);
  const activeStages = useMemo(() => {
    if (!activeEvents) return null;
    return new Set(activeEvents.map((e) => e.stage));
  }, [activeEvents]);

  const onWheel = useCallback((e: React.WheelEvent) => {
    setVp((p) => ({ ...p, k: Math.min(MAX_K, Math.max(MIN_K, p.k * (e.deltaY < 0 ? 1.12 : 0.89))) }));
  }, []);
  const onDown = useCallback(
    (e: React.MouseEvent) => {
      if (e.target !== svgRef.current) return;
      dragRef.current = { x: e.clientX, y: e.clientY, vx: vp.x, vy: vp.y };
    },
    [vp],
  );
  const onMove = useCallback((e: React.MouseEvent) => {
    const d = dragRef.current;
    if (!d) return;
    setVp((p) => ({ ...p, x: d.vx + (e.clientX - d.x), y: d.vy + (e.clientY - d.y) }));
  }, []);
  const onUp = useCallback(() => {
    dragRef.current = null;
  }, []);
  const reset = useCallback(() => setVp({ x: 0, y: 0, k: 1 }), []);
  const zoomBy = useCallback((f: number) => {
    setVp((p) => ({ ...p, k: Math.min(MAX_K, Math.max(MIN_K, p.k * f)) }));
  }, []);

  const dimOf = (stages: string[]) =>
    !!(selectedTraceId && activeStages && !stages.some((s) => activeStages.has(s)));

  return (
    <div className="dt-canvas" role="region" aria-label="Runtime topology">
      <svg
        ref={svgRef}
        className="dt-canvas-svg"
        onWheel={onWheel}
        onMouseDown={onDown}
        onMouseMove={onMove}
        onMouseUp={onUp}
        onMouseLeave={onUp}
      >
        <rect className="dt-bg" width="100%" height="100%" fill="transparent" />
        <g transform={`translate(${vp.x} ${vp.y}) scale(${vp.k})`}>
          <g className="dt-edges">
            {visuals.edges.map((v, i) => (
              <EdgeView key={v.edge.id ?? i} v={v} dimmed={dimOf([v.edge.source, v.edge.target])} />
            ))}
          </g>
          <g className="dt-nodes">
            {visuals.nodes.map((v) => (
              <NodeView
                key={v.node.id}
                v={v}
                selected={selectedStage === v.node.stage}
                dimmed={dimOf([v.node.stage])}
                onSelect={onSelectStage}
              />
            ))}
          </g>
        </g>
      </svg>

      {!layout.nodes.length ? (
        <div className="dt-canvas-empty">
          <div className="dt-canvas-empty-title">OBSERVING RUNTIME — NO DECISION PATH OBSERVED YET</div>
          <div className="dt-canvas-empty-sub">
            The graph derives only from real decision events. Nodes appear exactly when the
            runtime proves each stage ran.
          </div>
        </div>
      ) : null}

      <div className="dt-canvas-zoom" aria-label="Zoom controls">
        <button className="dt-zoom-btn" onClick={() => zoomBy(1.25)} aria-label="Zoom in">+</button>
        <span className="dt-zoom-k">{vp.k.toFixed(1)}x</span>
        <button className="dt-zoom-btn" onClick={() => zoomBy(0.8)} aria-label="Zoom out">-</button>
        <button className="dt-zoom-btn fit" onClick={reset} aria-label="Reset view">R</button>
      </div>

      <div className="dt-canvas-legend" aria-hidden="true">
        <span className="dt-legend-item"><i className="dt-dot observed" />observed stage</span>
        <span className="dt-legend-item"><i className="dt-dot terminal" />terminal (execution evidence)</span>
        <span className="dt-legend-item"><i className="dt-dot unmapped" />UNMAPPED</span>
        <span className="dt-legend-item"><i className="dt-dot gap" />PROVENANCE GAP</span>
      </div>
    </div>
  );
}
