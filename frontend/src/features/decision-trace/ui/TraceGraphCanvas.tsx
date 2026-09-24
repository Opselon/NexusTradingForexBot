/**
 * TraceGraphCanvas — the LIVE CAUSAL EXECUTION MAP (§3/§5/§6/§7/§8/§12/§44/
 * §52/§53/§56/§63/§71).
 *
 * Renders the runtime-discovered topology from layoutCanvas geometry: every
 * node exists because an EVENT proved it — no hardcoded trading path exists
 * in this component or its view-model.
 *
 * §5 PACKET ANIMATION: packets are derived ONLY from real event arrivals
 * (diffPackets, called from the event effect). There is NO animation timer
 * anywhere in this file — a packet never fires on a clock, only on an
 * observed hop.
 * §6/§7: only the branch the runtime actually took is lit; unused branches
 * are subdued, never removed.
 * §8: failure/rejection paths stay visible (the view layer cannot remove
 * them; selectView enforces it).
 * §12: external nodes (MT5/DB/provider) appear only when real events carry
 * them.
 * §44: mode badges LIVE/PAPER/SHADOW/REPLAY/BACKTEST/TRAINING, distinct;
 * SHADOW can never look EXECUTED.
 * §52: wheel = zoom, drag = pan, click = select stage + focus, FIT button,
 * per-node collapse/expand, branch isolation for the selected stage.
 * §53: smooth auto-follow (ON/OFF toggle) centers on the newest observed
 *   stage; when OFF the user's pan is never yanked away.
 * §56: provenance-gap edges render DASHED with an explicit label, never an
 *   invented solid arrow.
 * §63/§71: all derived sets are bounded and memoized on identity; no
 * unbounded React state growth (packet ring + seen-set are capped).
 *
 * OWNER: lane C, LIVE-CAUSAL-TOPOLOGY wave.
 */

import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  describeCanvas,
  NODE_H,
  NODE_W,
  type EdgeVisual,
  type NodeVisual,
} from "../traceCanvas";
import {
  fitViewport,
  focusViewport,
  layoutCanvas,
  selectView,
  type ViewGroup,
  type Viewport,
} from "../traceLayout";
import {
  diffPackets,
  headStage,
  MAX_PACKETS,
  type Packet,
  type PacketSeen,
} from "../traceGraph";
import type { DerivedGraph } from "../traceGraph";
import type { TraceEvent } from "../types";

/* The frozen seam: exactly these 5 props, additive optional only. */
interface Props {
  graph: DerivedGraph;
  selectedTraceId: string | null;
  activeEvents: TraceEvent[] | null;
  onSelectStage: (stage: string | null) => void;
  selectedStage: string | null;
}

const MIN_K = 0.4;
const MAX_K = 2.4;
/** §71: cap how many packets are kept in React state (ring, not growth). */
const PACKET_RING = MAX_PACKETS;

function clampK(k: number): number {
  if (!Number.isFinite(k)) return 1;
  return Math.min(MAX_K, Math.max(MIN_K, k));
}

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
      className={`dt-node ${v.cls} ${v.tone} ${selected ? "selected" : ""} ${dimmed ? "dim" : ""} ${
        v.pulse ? "dt-pulse" : ""
      }`}
      transform={`translate(${v.node.x - v.width / 2} ${v.node.y - v.height / 2})`}
      onClick={() => onSelect(v.node.stage)}
      role="button"
      tabIndex={0}
      aria-label={`stage ${v.node.stage}, ${v.node.count} events${
        v.node.terminal ? ", terminal" : ""
      }${v.node.provenanceGap ? ", provenance gap" : ""}`}
    >
      <rect width={v.width} height={v.height} rx={8} className="dt-node-body" />
      {v.extCls ? <rect width={v.width} height={v.height} rx={8} className={`dt-ext ${v.extCls}`} /> : null}
      <text x={v.width / 2} y={17} textAnchor="middle" className="dt-node-label">
        {v.label}
      </text>
      <text x={v.width / 2} y={32} textAnchor="middle" className="dt-node-sub">
        {v.sublabel}
      </text>
      {v.modeCls ? (
        <g transform={`translate(${v.width - 2} ${-8})`}>
          <rect x={-34} y={-9} width={36} height={14} rx={7} className={`dt-mode ${v.modeCls}`} />
          <text x={-16} y={2} textAnchor="middle" className="dt-mode-text">
            {v.node.mode}
          </text>
        </g>
      ) : null}
      {v.collapsedCount != null ? (
        <g transform={`translate(${v.width + 2} -6)`}>
          <circle r={9} className="dt-badge collapsed" />
          <text y={3} textAnchor="middle" className="dt-badge-text">
            {`+${v.collapsedCount}`}
          </text>
        </g>
      ) : v.badge ? (
        <g transform={`translate(${v.width + 2} -6)`}>
          <circle r={9} className="dt-badge" />
          <text y={3} textAnchor="middle" className="dt-badge-text">
            {v.badge}
          </text>
        </g>
      ) : null}
      {v.gapLabel ? (
        <text x={v.width / 2} y={-12} textAnchor="middle" className="dt-gap-label">
          {v.gapLabel}
        </text>
      ) : null}
    </g>
  );
});

const EdgeView = memo(function EdgeView({
  v,
  dimmed,
  packet,
}: {
  v: EdgeVisual;
  dimmed: boolean;
  packet: boolean;
}) {
  return (
    <g className={`dt-edge ${v.cls} ${dimmed ? "dim" : ""} ${packet ? "dt-packet-on" : ""}`}>
      <path d={v.path} className="dt-edge-hit" />
      <path
        d={v.path}
        className="dt-edge-line"
        style={{ strokeWidth: v.width, strokeDasharray: v.dash }}
      />
      {v.gapLabel ? (
        <text
          x={(v.edge.x1 + v.edge.x2) / 2}
          y={(v.edge.y1 + v.edge.y2) / 2 - 6}
          textAnchor="middle"
          className="dt-gap-label"
        >
          {v.gapLabel}
        </text>
      ) : null}
      <title>
        {`${v.edge.source} → ${v.edge.target} · ${v.edge.count} ×` +
          (v.stateLabel ? ` · ${v.stateLabel}` : "") +
          (v.edge.provenanceGap ? " · PROVENANCE GAP" : "")}
      </title>
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
  const [vp, setVp] = useState<Viewport>({ x: 0, y: 0, k: 1 });
  const [autoFocus, setAutoFocus] = useState(true);
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(() => new Set());
  const [packets, setPackets] = useState<Packet[]>([]);
  const dragRef = useRef<{ x: number; y: number; vx: number; vy: number } | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const packetSeenRef = useRef<PacketSeen | null>(null);
  const sizeRef = useRef({ vw: 0, vh: 0 });

  const layout = useMemo(() => layoutCanvas(graph), [graph]);

  // §52 branch isolation: the selected stage keeps its 1-hop neighbourhood.
  const view: ViewGroup = useMemo(
    () => selectView(layout, { collapsed, isolateStage: selectedStage }),
    [layout, collapsed, selectedStage],
  );

  const visuals = useMemo(
    () => describeCanvas(layout, { collapsedCounts: view.collapsedCounts }),
    [layout, view],
  );

  /* §5 — packets fire ONLY on real event arrival. No timer: the derivation
   * is a pure diff of previously-seen event ids vs the newly arrived batch,
   * so on mount / trace switch nothing animates (never retroactively seeded)
   * and on every real SSE batch exactly the new observed hops light up. */
  useEffect(() => {
    const events = activeEvents ?? [];
    const { packets: next, next: seen } = diffPackets(packetSeenRef.current, events, graph);
    packetSeenRef.current = seen;
    if (next.length) {
      // §71: ring, never growth — drop the oldest, keep PACKET_RING.
      setPackets((prev) => [...prev, ...next].slice(-PACKET_RING));
    }
  }, [activeEvents, graph]);

  /* §53 — smooth auto-follow: when ON, center on the newest observed stage
   * whenever the observation set moves. When OFF the user keeps the pan. */
  useEffect(() => {
    if (!autoFocus) return;
    const events = activeEvents ?? [];
    if (!events.length) return;
    const head = headStage(events);
    if (!head) return;
    const ids = layout.nodes.filter((n) => n.stage === head.stage).map((n) => n.id);
    if (!ids.length) return;
    const { vw, vh } = sizeRef.current;
    if (!vw || !vh) return;
    setVp((p) => focusViewport(layout, ids, vw, vh, p.k));
  }, [autoFocus, activeEvents, layout]);

  const activeStages = useMemo(() => {
    if (!activeEvents) return null;
    return new Set(activeEvents.map((e) => e.stage));
  }, [activeEvents]);

  const packetEdges = useMemo(() => {
    const out = new Set<string>();
    for (const p of packets) out.add(p.edgeKey);
    return out;
  }, [packets]);

  const onWheel = useCallback((e: React.WheelEvent) => {
    setVp((p) => ({ ...p, k: clampK(p.k * (e.deltaY < 0 ? 1.12 : 0.89)) }));
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

  const measure = useCallback(() => {
    const el = svgRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    sizeRef.current = { vw: r.width, vh: r.height };
  }, []);

  const fit = useCallback(() => {
    measure();
    const { vw, vh } = sizeRef.current;
    setVp(fitViewport(layout, vw, vh));
  }, [layout, measure]);

  const zoomBy = useCallback((f: number) => {
    setVp((p) => ({ ...p, k: clampK(p.k * f) }));
  }, []);

  const toggleCollapse = useCallback((id: string) => {
    setCollapsed((prev) => {
      const out = new Set(prev);
      if (out.has(id)) out.delete(id);
      else out.add(id);
      return out;
    });
  }, []);

  const dimOf = (stages: string[]) =>
    !!(selectedTraceId && activeStages && !stages.some((s) => activeStages.has(s)));

  return (
    <div className="dt-canvas" role="region" aria-label="Runtime causal topology">
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
              <EdgeView
                key={v.edge.id ?? i}
                v={v}
                dimmed={dimOf([v.edge.source, v.edge.target])}
                packet={packetEdges.has(v.edge.id)}
              />
            ))}
          </g>
          <g className="dt-packets">
            {packets.map((p) => (
              <PacketDot key={p.eventId} edgeId={p.edgeKey} view={view} />
            ))}
          </g>
          <g className="dt-nodes">
            {visuals.nodes.map((v) => (
              <NodeView
                key={v.node.id}
                v={v}
                selected={selectedStage === v.node.stage}
                dimmed={dimOf([v.node.stage])}
                onSelect={(st) => {
                  if (collapsed.has(v.node.id)) toggleCollapse(v.node.id);
                  onSelectStage(st);
                }}
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
        <button className="dt-zoom-btn fit" onClick={fit} aria-label="Fit graph to view">FIT</button>
        <button
          className={`dt-zoom-btn ${autoFocus ? "on" : ""}`}
          onClick={() => setAutoFocus((a) => !a)}
          aria-pressed={autoFocus}
          aria-label="Toggle auto-focus"
          title="Auto-follow the newest observed stage"
        >
          AF
        </button>
      </div>

      <div className="dt-canvas-legend" aria-hidden="true">
        <span className="dt-legend-item"><i className="dt-dot observed" />observed stage</span>
        <span className="dt-legend-item"><i className="dt-dot terminal" />terminal (execution evidence)</span>
        <span className="dt-legend-item"><i className="dt-dot unmapped" />UNMAPPED</span>
        <span className="dt-legend-item"><i className="dt-dot gap" />PROVENANCE GAP (dashed)</span>
      </div>
    </div>
  );
}

/* §5 — the packet dot rides a REAL edge the graph already proved exists. It
 * is positioned from the same memoized view geometry, so it can never sit on
 * an invented path. §63: one SVG node per packet in the bounded ring. */
const PacketDot = memo(function PacketDot({
  edgeId,
  view,
}: {
  edgeId: string;
  view: ViewGroup;
}) {
  const edge = view.edges.find((e) => e.id === edgeId);
  if (!edge) return null;
  return <circle cx={edge.x1} cy={edge.y1} r={3.5} className="dt-packet" />;
});

void NODE_W;
void NODE_H;
