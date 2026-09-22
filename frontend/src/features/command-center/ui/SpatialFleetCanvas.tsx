/**
 * SpatialFleetCanvas — 2.5D strategy-fleet map (port of the legacy Command
 * Center spatial tab, Web/command_center_spatial.js + command_center_ui.js).
 *
 * Data flow (backend-authoritative):
 *   GET /api/command-center/spatial  ->  SpatialFleetEngine (Canvas2D renderer)
 * Selection emits the strategy_id upward; the host page opens the real
 * inspector. The floating card under the selected node shows ONLY fields the
 * spatial payload carries. When nothing is visible, the legacy honest empty
 * overlay ("NO VISIBLE STRATEGIES" + backend total + filter + matching) is
 * kept — never fake entities, never fake counts.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { EmptyState, ErrorState, Skeleton } from "@/components/primitives";
import { formatNumber } from "@/lib/format";
import { commandCenterQueries } from "../useCases";
import { num, spatialEmptyFacts, str, type CcSpatialDto, type CcSpatialNodeDto } from "../model";
import { SpatialFleetEngine } from "./spatialEngine";
import "./spatial.css";

type ViewportStatus = "loading" | "error" | "unavailable" | "empty" | "ready";

export function SpatialFleetCanvas({
  selectedId,
  onSelect,
  onInspect,
}: {
  selectedId: string | null;
  /** node clicked on the canvas (engine -> host). */
  onSelect: (strategyId: string) => void;
  /** explicit "open inspector" from the selection card. */
  onInspect: (strategyId: string) => void;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const engineRef = useRef<SpatialFleetEngine | null>(null);
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const onInspectRef = useRef(onInspect);
  onInspectRef.current = onInspect;

  const [lifecycleFilter, setLifecycleFilter] = useState("");
  const [tipPos, setTipPos] = useState<{ x: number; y: number } | null>(null);

  const spatialQ = useQuery({
    queryKey: ["command-center", "spatial"],
    queryFn: ({ signal }) => commandCenterQueries.spatial(signal),
    refetchInterval: 30_000,
    retry: false,
  });

  const payload = spatialQ.data?.available === true ? spatialQ.data : null;

  const visible = useMemo<CcSpatialDto | null>(() => {
    if (!payload) return null;
    if (!lifecycleFilter || lifecycleFilter === "ALL") return payload;
    return {
      ...payload,
      nodes: (payload.nodes ?? []).filter((n) => (n.zone || "DISCOVERED") === lifecycleFilter),
    };
  }, [payload, lifecycleFilter]);

  /* engine lifecycle: create once, dispose on unmount */
  useEffect(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;
    const engine = new SpatialFleetEngine(canvas, {
      onSelect: (id) => onSelectRef.current(id),
    });
    engineRef.current = engine;
    const ro = new ResizeObserver(() => {
      const r = wrap.getBoundingClientRect();
      engine.resize(r.width, r.height);
    });
    ro.observe(wrap);
    const r0 = wrap.getBoundingClientRect();
    engine.resize(r0.width, r0.height);
    return () => {
      ro.disconnect();
      engine.dispose();
      engineRef.current = null;
    };
  }, []);

  /* feed the engine whenever the (filtered) payload changes; auto-fit once per view */
  const fittedRef = useRef(false);
  useEffect(() => {
    const engine = engineRef.current;
    if (!engine) return;
    engine.update(visible);
    if (!fittedRef.current && visible && (visible.nodes?.length ?? 0) > 0 && engine.fitAll()) {
      fittedRef.current = true;
    }
  }, [visible]);

  /* keep selection truth in sync with the host; a canvas CLICK already set the
     engine selection itself (no camera jump), while an external selection
     (fleet table / stuck list) focuses the camera on the node. */
  useEffect(() => {
    const engine = engineRef.current;
    if (!engine || engine.getSelectedId() === selectedId) return;
    engine.select(selectedId);
    if (selectedId) engine.focusSelected();
  }, [selectedId]);

  /* selection tooltip anchors to the node every frame */
  useEffect(() => {
    if (!selectedId) {
      setTipPos(null);
      return;
    }
    let raf = 0;
    let last = "";
    const tick = () => {
      const p = engineRef.current?.nodeScreenPos(selectedId) ?? null;
      const key = p ? `${Math.round(p.x)}:${Math.round(p.y)}` : "-";
      if (key !== last) {
        last = key;
        setTipPos(p);
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [selectedId, visible]);

  const selNode: CcSpatialNodeDto | null =
    selectedId && payload ? (payload.nodes ?? []).find((n) => str(n.strategy_id) === selectedId) ?? null : null;

  const status: ViewportStatus = spatialQ.isPending
    ? "loading"
    : spatialQ.isError
      ? "error"
      : !payload
        ? "unavailable"
        : (visible?.nodes?.length ?? 0) === 0
          ? "empty"
          : "ready";

  const empty = spatialEmptyFacts(payload ?? undefined, visible?.nodes?.length ?? 0, lifecycleFilter);
  const zoneOptions = useMemo(
    () => (payload?.zones ?? []).map((z) => str(z.zone)).filter((z): z is string => Boolean(z)),
    [payload],
  );

  return (
    <div className="spatial-wrap" ref={wrapRef}>
      <canvas ref={canvasRef} className="spatial-canvas" role="img" aria-label="Spatial fleet map (2.5D)" />

      {/* camera toolbar (legacy scc-fit-all / reset / focus set) */}
      <div className="spatial-tools">
        <select
          className="select spatial-filter"
          value={lifecycleFilter}
          onChange={(e) => {
            setLifecycleFilter(e.target.value);
            fittedRef.current = false;
          }}
          aria-label="lifecycle filter"
        >
          <option value="">zone: all</option>
          {zoneOptions.map((z) => (
            <option key={z} value={z}>
              {z}
            </option>
          ))}
        </select>
        <button className="btn small ghost" onClick={() => engineRef.current?.fitAll()}>fit all</button>
        <button className="btn small ghost" onClick={() => engineRef.current?.resetCamera()}>reset cam</button>
        <button className="btn small ghost" onClick={() => engineRef.current?.focusSelected()}>focus sel.</button>
        <button className="btn small ghost" onClick={() => engineRef.current?.focusActive()}>focus live</button>
        <button className="btn small ghost" onClick={() => engineRef.current?.focusBlocked()}>focus terminal</button>
        <span className="spatial-legend" aria-hidden="true">
          <i style={{ background: "#10b981" }} /> live
          <i style={{ background: "#eab308" }} /> shadow
          <i style={{ background: "#84cc16" }} /> validated
          <i style={{ background: "#f43f5e" }} /> terminal
        </span>
      </div>

      {/* selection card — ONLY real payload fields */}
      {selNode && tipPos && (
        <div className="spatial-tip" style={{ left: tipPos.x + 18, top: tipPos.y - 10 }} role="status">
          <div className="tip-id inline-mono">{str(selNode.strategy_id) ?? "—"}</div>
          <dl className="kv tiny">
            <dt>zone</dt><dd>{str(selNode.zone) ?? "—"}</dd>
            <dt>eligibility</dt><dd>{str(selNode.eligibility_state) ?? "UNKNOWN"}</dd>
            <dt>samples</dt><dd>{selNode.size_hint === undefined || selNode.size_hint === null ? "—" : String(selNode.size_hint)}</dd>
            <dt>confidence</dt><dd>{num(selNode.confidence) === null ? "—" : formatNumber(selNode.confidence, 3)}</dd>
            <dt>health (elev.)</dt><dd>{num(selNode.elevation) === null ? "NOT_MEASURED" : formatNumber(selNode.elevation, 1)}</dd>
            <dt>eval stage</dt><dd>{str(selNode.evaluation?.current_stage) ?? "—"}</dd>
          </dl>
          <button
            className="btn small primary"
            onClick={() => {
              const id = String(selNode.strategy_id ?? "");
              if (id) onInspectRef.current(id);
            }}
          >
            open inspector
          </button>
        </div>
      )}

      {/* honest empty overlay (legacy showSpatialEmptyState — no fabricated counts) */}
      {status === "empty" && (
        <div className="spatial-empty" role="status">
          <p className="sp-empty-title">NO VISIBLE STRATEGIES</p>
          <p className="sp-empty-line">Backend strategies: {empty.backendTotal}</p>
          <p className="sp-empty-line">Current filter: {empty.filter}</p>
          <p className="sp-empty-line">Matching: {empty.matching}</p>
        </div>
      )}
      {status === "loading" && (
        <div className="spatial-state"><Skeleton count={3} /></div>
      )}
      {status === "error" && (
        <div className="spatial-state">
          <ErrorState
            message={spatialQ.error instanceof Error ? spatialQ.error.message : "spatial endpoint failed"}
            onRetry={() => void spatialQ.refetch()}
          />
        </div>
      )}
      {status === "unavailable" && (
        <div className="spatial-state">
          <EmptyState
            message="Research engine unavailable"
            hint={spatialQ.data?.reason ?? "RESEARCH_ENGINE_UNAVAILABLE — /api/command-center/spatial answered available:false"}
          />
        </div>
      )}
    </div>
  );
}
