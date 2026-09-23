/**
 * SpatialFleetEngine — Canvas2D 2.5D fleet renderer, a faithful React-era port
 * of the legacy Web/command_center_spatial.js (READ-ONLY reference; the React
 * tree never imports Web/).
 *
 * Same contract as legacy:
 *  - Consumes the AUTHORITATIVE spatial payload from /api/command-center/spatial
 *    (research/spatial_layout.SpatialLayout). Node = backend strategy, zone =
 *    backend lifecycle. Positions come from the backend x/y with the legacy
 *    anti-clump distribution on top (layout math is presentation, not data).
 *  - Camera {x, y, zoom} with worldToScreen/screenToWorld around a canvas-centre
 *    offset; wheel zoom-to-cursor, drag pan, click-select with on-select callback.
 *  - DPR-crisp sizing (capped at 2), RAF loop, LOD labels with collision
 *    avoidance, zone strata bands, perspective floor grid, EXECUTION-GATE and
 *    terminal-region dividers, transition trails, evaluation-pipeline internal
 *    indicator (telemetry flash — NEVER relocates a node).
 *  - No fake entities: with zero nodes the canvas draws only the environment and
 *    the React host shows the honest empty overlay (legacy scc-spatial-empty).
 */

import {
  SPATIAL_LIVE_ZONES,
  SPATIAL_TERMINAL_ZONES,
  type CcSpatialDto,
  type CcSpatialEvaluationDto,
  type CcSpatialNodeDto,
} from "../model";

const PIPELINE_ZONES = [
  "DISCOVERED", "INITIAL_TESTING", "EVIDENCE_BUILDING", "WALK_FORWARD_READY",
  "OOS_READY", "ROBUSTNESS_READY", "BACKTESTING", "VALIDATING", "OOS_TESTING",
  "ROBUSTNESS_TESTING", "VALIDATED", "SHADOW", "ACTIVE",
];
const TERMINAL_ZONES = ["REJECTED", "DEGRADED", "RETIRED"];
const ALL_ZONES = PIPELINE_ZONES.concat(TERMINAL_ZONES);

const ZONE_COLORS: Record<string, string> = {
  DISCOVERED: "#64748b",
  INITIAL_TESTING: "#38bdf8",
  EVIDENCE_BUILDING: "#0ea5e9",
  WALK_FORWARD_READY: "#06b6d4",
  OOS_READY: "#14b8a6",
  ROBUSTNESS_READY: "#22c55e",
  BACKTESTING: "#0ea5e9",
  VALIDATING: "#06b6d4",
  OOS_TESTING: "#14b8a6",
  ROBUSTNESS_TESTING: "#22c55e",
  VALIDATED: "#84cc16",
  SHADOW: "#eab308",
  ACTIVE: "#10b981",
  REJECTED: "#f43f5e",
  DEGRADED: "#f97316",
  RETIRED: "#78716c",
};

/** Transient evaluation gates in pipeline order (mirror of legacy constants). */
const EVAL_GATES = ["BACKTEST", "WALK_FORWARD", "OOS", "ROBUSTNESS", "SCORE"];
const EVAL_RESULT_COLOR: Record<string, string> = {
  PASS: "#22c55e",
  FAIL: "#f43f5e",
  RUNNING: "#eab308",
  INCONCLUSIVE: "#a855f7",
  NOT_RUN: "#475569",
  MISSING: "#475569",
};
const evalResultColor = (st: string | undefined): string =>
  (st && EVAL_RESULT_COLOR[st]) || "#64748b";

interface RenderNode {
  strategy_id: string;
  zone: string;
  size_hint: number;
  ring_count: number;
  elevation: number | null;
  confidence: number | null | undefined;
  eligibility_state: string;
  evaluation: CcSpatialNodeDto["evaluation"];
  _tx: number;
  _ty: number;
  _sx?: number;
  _sy?: number;
  _color: string;
  _terminal: boolean;
}

interface CamAnim {
  fromX: number; fromY: number; fromZ: number;
  toX: number; toY: number; toZ: number;
  t0: number; dur: number;
}

const easeInOutCubic = (t: number): number =>
  t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;

/** perf: zone -> rank map for a zone order (pure; rebuilt only when the
 *  payload's zone order changes, never per frame). */
const zoneRankOf = (zones: string[]): Record<string, number> => {
  const rank: Record<string, number> = {};
  zones.forEach((z, i) => { rank[z] = i; });
  return rank;
};

const hexToRgb = (hex: string): [number, number, number] => {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
};
const rgba = (hex: string, a: number): string => {
  const [r, g, b] = hexToRgb(hex);
  return `rgba(${r}, ${g}, ${b}, ${a})`;
};

/** perf: evaluation signature memoized per evaluation-OBJECT identity — the
 *  spatial poll's structural sharing keeps identities stable when a node's
 *  evaluation did not change, so update() compares cached strings instead of
 *  re-running JSON.stringify twice per node on every 30s poll. The signature
 *  string is byte-identical to the previous inline form (same array, same
 *  fields), so flash triggering compares exactly what it did before. */
const evalSigCache = new WeakMap<CcSpatialEvaluationDto, string>();
const evalSig = (ev: CcSpatialNodeDto["evaluation"]): string | null => {
  if (!ev) return null;
  const hit = evalSigCache.get(ev);
  if (hit !== undefined) return hit;
  const sig = JSON.stringify([ev.current_stage, ev.gates, ev.progress]);
  evalSigCache.set(ev, sig);
  return sig;
};

export interface SpatialEngineOptions {
  onSelect?: (strategyId: string) => void;
}

export class SpatialFleetEngine {
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private opts: SpatialEngineOptions;

  private nodes: RenderNode[] = [];
  private zoneOrder: string[] = ALL_ZONES;
  // perf: pure derivations of (nodes, zoneOrder) — recomputed in update()
  // only, so draw() never rebuilds them per RAF frame (values identical).
  private zoneRank: Record<string, number> = zoneRankOf(ALL_ZONES);
  private zoneCounts: Record<string, number> = {};
  private anims: Record<string, { fx: number; fy: number; tx: number; ty: number; t0: number; dur: number }> = {};
  private trails: Record<string, Array<{ x: number; y: number; t: number }>> = {};
  private flashes: Record<string, { t0: number; dur: number }> = {};
  private camera = { x: 0, y: 0, zoom: 1 };
  private camAnim: CamAnim | null = null;
  private selectedId: string | null = null;
  private hoverId: string | null = null;
  private raf: number | null = null;
  private dpr = 1;
  private lastPayload: CcSpatialDto | null = null;
  private pointer: { x: number; y: number } | null = null;

  private dragging = false;
  private dragMoved = false;
  private lastX = 0;
  private lastY = 0;
  private disposed = false;

  constructor(canvas: HTMLCanvasElement, opts: SpatialEngineOptions = {}) {
    this.canvas = canvas;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("Canvas2D unavailable");
    this.ctx = ctx;
    this.opts = opts;
    this.attachControls();
    this.startLoop();
  }

  dispose(): void {
    this.disposed = true;
    if (this.raf !== null) cancelAnimationFrame(this.raf);
    this.raf = null;
    window.removeEventListener("mouseup", this.onWindowUp);
    window.removeEventListener("mousemove", this.onWindowMove);
    this.canvas.removeEventListener("wheel", this.onWheel);
    this.canvas.removeEventListener("mousedown", this.onDown);
    this.canvas.removeEventListener("click", this.onClick);
    this.canvas.removeEventListener("mousemove", this.onHoverMove);
    this.canvas.removeEventListener("mouseleave", this.onLeave);
  }

  /* ------------------------------- sizing -------------------------------- */

  resize(cssW: number, cssH: number): void {
    this.dpr = Math.min(2, window.devicePixelRatio || 1);
    const w = Math.max(400, cssW || 800);
    const h = Math.max(300, cssH || 500);
    this.canvas.width = Math.round(w * this.dpr);
    this.canvas.height = Math.round(h * this.dpr);
    this.canvas.style.width = `${w}px`;
    this.canvas.style.height = `${h}px`;
  }

  /* ------------------------------- payload ------------------------------- */

  /** Ingest the authoritative spatial payload (legacy updateFromPayload). */
  update(payload: CcSpatialDto | null): void {
    if (!payload || !payload.nodes) return;
    this.lastPayload = payload;
    this.zoneOrder = (payload.zones ?? []).map((z) => z.zone ?? "").filter(Boolean);
    if (!this.zoneOrder.length) this.zoneOrder = ALL_ZONES;
    // perf: rank derives only from zoneOrder — refresh it with the order.
    this.zoneRank = zoneRankOf(this.zoneOrder);
    const incoming = payload.nodes;

    const rank: Record<string, number> = {};
    this.zoneOrder.forEach((z, i) => { rank[z] = i; });
    const zoneRowH = 130;

    const countsByZone: Record<string, number> = {};
    for (const n of incoming) {
      const z = n.zone || "DISCOVERED";
      countsByZone[z] = (countsByZone[z] || 0) + 1;
    }
    const indicesByZone: Record<string, number> = {};

    const prevById: Record<string, RenderNode> = {};
    for (const o of this.nodes) prevById[o.strategy_id] = o;

    const next: RenderNode[] = [];
    for (const n of incoming) {
      const z = n.zone || "DISCOVERED";
      const zi = rank[z] ?? 0;
      const idx = indicesByZone[z] || 0;
      indicesByZone[z] = idx + 1;
      const totalInZone = countsByZone[z] || 1;

      // Anti-clump distribution (legacy): structured columns per zone; the
      // backend x (stable-jittered column offset) is folded in as a small
      // deterministic lateral shift so the layout stays true to the payload.
      const cols = Math.max(1, Math.min(14, Math.ceil(Math.sqrt(totalInZone))));
      const col = idx % cols;
      const row = Math.floor(idx / cols);
      const colWidth = 90;
      const lateralOffset = (col - (cols - 1) / 2) * colWidth + ((n.x || 0) % 30);
      const targetX = lateralOffset;
      const targetY = zi * zoneRowH + 35 + row * 22;

      const sid = String(n.strategy_id ?? `node-${next.length}`);
      const model: RenderNode = {
        strategy_id: sid,
        zone: z,
        size_hint: n.size_hint || 0,
        ring_count: n.ring_count || 0,
        elevation: n.elevation === null || n.elevation === undefined ? null : n.elevation,
        confidence: n.confidence,
        eligibility_state: n.eligibility_state || "UNKNOWN",
        evaluation: n.evaluation || null,
        _tx: targetX,
        _ty: targetY,
        _color: ZONE_COLORS[z] || "#94a3b8",
        _terminal: SPATIAL_TERMINAL_ZONES.has(z),
      };

      const prev = prevById[sid];
      if (prev && prev.zone !== model.zone) {
        this.anims[sid] = {
          fx: prev._sx !== undefined ? prev._sx : prev._tx,
          fy: prev._sy !== undefined ? prev._sy : prev._ty,
          tx: targetX, ty: targetY,
          t0: performance.now(), dur: 900,
        };
        const tr = (this.trails[sid] ??= []);
        tr.push({ x: targetX, y: targetY, t: Date.now() });
        if (tr.length > 12) tr.shift();
      } else {
        const a0 = this.anims[sid];
        if (a0 && (a0.tx !== targetX || a0.ty !== targetY)) {
          a0.tx = targetX; a0.ty = targetY; a0.t0 = performance.now();
        }
      }

      // Evaluation-progress flash: telemetry advanced but lifecycle zone did
      // NOT — brighten the internal ring, never relocate the node.
      if (prev && evalSig(prev.evaluation) !== evalSig(model.evaluation) && model.evaluation) {
        this.flashes[sid] = { t0: performance.now(), dur: 1100 };
      }
      next.push(model);
    }
    this.nodes = next;
    // perf: counts derive only from nodes — refresh them with the node set.
    const counts: Record<string, number> = {};
    for (const n of this.nodes) counts[n.zone] = (counts[n.zone] || 0) + 1;
    this.zoneCounts = counts;
  }

  /* -------------------------------- camera ------------------------------- */

  private setCamera(x: number, y: number, zoom: number, animate: boolean): void {
    const z = Math.min(4, Math.max(0.15, zoom));
    if (animate) {
      this.camAnim = {
        fromX: this.camera.x, fromY: this.camera.y, fromZ: this.camera.zoom,
        toX: x, toY: y, toZ: z,
        t0: performance.now(), dur: 420,
      };
    } else {
      this.camera.x = x; this.camera.y = y; this.camera.zoom = z;
    }
  }

  private worldToScreen(wx: number, wy: number): [number, number] {
    return [
      (wx - this.camera.x) * this.camera.zoom + this.canvas.width / (2 * this.dpr),
      (wy - this.camera.y) * this.camera.zoom + this.canvas.height / (2 * this.dpr),
    ];
  }

  /** Node world position → canvas CSS px (used by the React host for overlays). */
  nodeScreenPos(sid: string): { x: number; y: number } | null {
    const n = this.nodes.find((o) => o.strategy_id === sid);
    if (!n || n._sx === undefined || n._sy === undefined) return null;
    const [x, y] = this.worldToScreen(n._sx, n._sy);
    return { x, y };
  }

  private screenToWorld(sx: number, sy: number): [number, number] {
    return [sx / this.camera.zoom + this.camera.x, sy / this.camera.zoom + this.camera.y];
  }

  private boundsOf(list: RenderNode[]): { minX: number; maxX: number; minY: number; maxY: number } | null {
    if (!list.length) return null;
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const n of list) {
      minX = Math.min(minX, n._tx); maxX = Math.max(maxX, n._tx);
      minY = Math.min(minY, n._ty); maxY = Math.max(maxY, n._ty);
    }
    return { minX, maxX, minY, maxY };
  }

  private computeFit(b: { minX: number; maxX: number; minY: number; maxY: number } | null) {
    if (!b) return null;
    const vw = this.canvas.width / this.dpr;
    const vh = this.canvas.height / this.dpr;
    const w = Math.max(1, b.maxX - b.minX + 100);
    const h = Math.max(1, b.maxY - b.minY + 100);
    const zoom = Math.min(1.6, Math.max(0.2, Math.min(vw / w, vh / h)));
    const cx = (b.minX + b.maxX) / 2;
    const cy = (b.minY + b.maxY) / 2;
    return { x: cx - vw / 2 / zoom, y: cy - vh / 2 / zoom, zoom };
  }

  fitAll(): boolean {
    const fit = this.computeFit(this.boundsOf(this.nodes));
    if (!fit) return false;
    this.setCamera(fit.x, fit.y, fit.zoom, true);
    return true;
  }

  resetCamera(): void { this.setCamera(0, 0, 1, true); }

  focusSelected(): void {
    const n = this.nodes.find((o) => o.strategy_id === this.selectedId);
    if (!n) return;
    const vw = this.canvas.width / this.dpr;
    const vh = this.canvas.height / this.dpr;
    const z = 1.4;
    this.setCamera(n._tx - vw / 2 / z, n._ty - vh / 2 / z, z, true);
  }

  focusActive(): void {
    const b = this.boundsOf(this.nodes.filter((n) => SPATIAL_LIVE_ZONES.has(n.zone)));
    const fit = this.computeFit(b);
    if (fit) this.setCamera(fit.x, fit.y, fit.zoom, true);
  }

  focusBlocked(): void {
    const b = this.boundsOf(this.nodes.filter((n) => SPATIAL_TERMINAL_ZONES.has(n.zone)));
    const fit = this.computeFit(b);
    if (fit) this.setCamera(fit.x, fit.y, fit.zoom, true);
  }

  select(id: string | null): void { this.selectedId = id; }
  getSelectedId(): string | null { return this.selectedId; }
  hasPayload(): boolean { return !!this.lastPayload; }
  nodeCount(): number { return this.nodes.length; }

  /* -------------------------------- input -------------------------------- */

  private onWheel = (ev: WheelEvent): void => {
    ev.preventDefault();
    const factor = ev.deltaY < 0 ? 1.12 : 1 / 1.12;
    const rect = this.canvas.getBoundingClientRect();
    const sx = ev.clientX - rect.left;
    const sy = ev.clientY - rect.top;
    const [wx, wy] = this.screenToWorld(sx, sy);
    const newZoom = Math.min(4, Math.max(0.15, this.camera.zoom * factor));
    this.camera.x = wx - sx / newZoom;
    this.camera.y = wy - sy / newZoom;
    this.camera.zoom = newZoom;
    this.camAnim = null;
  };

  private onDown = (ev: MouseEvent): void => {
    this.dragging = true;
    this.dragMoved = false;
    this.lastX = ev.clientX;
    this.lastY = ev.clientY;
  };

  private onWindowUp = (): void => { this.dragging = false; };

  private onWindowMove = (ev: MouseEvent): void => {
    if (!this.dragging) return;
    const dx = ev.clientX - this.lastX;
    const dy = ev.clientY - this.lastY;
    if (Math.abs(dx) + Math.abs(dy) > 3) this.dragMoved = true;
    this.camera.x -= dx / this.camera.zoom;
    this.camera.y -= dy / this.camera.zoom;
    this.camAnim = null;
    this.lastX = ev.clientX;
    this.lastY = ev.clientY;
  };

  private onHoverMove = (ev: MouseEvent): void => {
    const rect = this.canvas.getBoundingClientRect();
    this.pointer = { x: ev.clientX - rect.left, y: ev.clientY - rect.top };
  };

  private onLeave = (): void => { this.pointer = null; this.hoverId = null; };

  private pick(wx: number, wy: number): RenderNode | null {
    let best: RenderNode | null = null;
    let bestDist = 20 / this.camera.zoom;
    for (const n of this.nodes) {
      if (n._sx === undefined || n._sy === undefined) continue;
      const d = Math.hypot(n._sx - wx, n._sy - wy);
      if (d < bestDist) { best = n; bestDist = d; }
    }
    return best;
  }

  private onClick = (ev: MouseEvent): void => {
    if (this.dragMoved) return;
    const rect = this.canvas.getBoundingClientRect();
    const [wx, wy] = this.screenToWorld(ev.clientX - rect.left, ev.clientY - rect.top);
    const hit = this.pick(wx, wy);
    if (hit) {
      this.selectedId = hit.strategy_id;
      this.opts.onSelect?.(hit.strategy_id);
    }
  };

  private attachControls(): void {
    this.canvas.addEventListener("wheel", this.onWheel, { passive: false });
    this.canvas.addEventListener("mousedown", this.onDown);
    this.canvas.addEventListener("click", this.onClick);
    this.canvas.addEventListener("mousemove", this.onHoverMove);
    this.canvas.addEventListener("mouseleave", this.onLeave);
    window.addEventListener("mouseup", this.onWindowUp);
    window.addEventListener("mousemove", this.onWindowMove);
  }

  /* ------------------------------- drawing ------------------------------- */

  private stepCameraAnim(now: number): void {
    const p = this.camAnim;
    if (!p) return;
    const raw = (now - p.t0) / p.dur;
    if (raw >= 1) {
      this.camera.x = p.toX; this.camera.y = p.toY; this.camera.zoom = p.toZ;
      this.camAnim = null;
      return;
    }
    const t = easeInOutCubic(raw);
    this.camera.x = p.fromX + (p.toX - p.fromX) * t;
    this.camera.y = p.fromY + (p.toY - p.fromY) * t;
    this.camera.zoom = p.fromZ + (p.toZ - p.fromZ) * t;
  }

  private countForZone(zone: string): number {
    // perf: memoized in update() — nodes/zone never mutate between payloads.
    return this.zoneCounts[zone] || 0;
  }

  private draw(now: number): void {
    const ctx = this.ctx;
    this.stepCameraAnim(now);

    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.fillStyle = "#0b1220";
    ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);

    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.translate(this.canvas.width / (2 * this.dpr), this.canvas.height / (2 * this.dpr));
    ctx.scale(this.camera.zoom, this.camera.zoom);
    ctx.translate(-this.camera.x, -this.camera.y);

    const zoneRowH = 130;
    // perf: rank is a pure derivation of zoneOrder — memoized in update(),
    // which is the only place zoneOrder changes (values identical).
    const rank = this.zoneRank;

    // Perspective floor grid — vertical depth rails + fading horizontal struts.
    const gridBottom = this.zoneOrder.length * zoneRowH + 600;
    for (let gx = -3500; gx <= 3500; gx += 250) {
      ctx.strokeStyle = "rgba(30, 41, 59, 0.28)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(gx, -600);
      ctx.lineTo(gx, gridBottom);
      ctx.stroke();
    }
    for (let gy = -400; gy <= gridBottom; gy += 65) {
      const depth = Math.max(0, Math.min(1, gy / gridBottom));
      ctx.strokeStyle = `rgba(35, 48, 66, ${0.35 - depth * 0.18})`;
      ctx.lineWidth = gy % 130 < 1 ? 1.4 : 0.7;
      ctx.beginPath();
      ctx.moveTo(-3500, gy);
      ctx.lineTo(3500, gy);
      ctx.stroke();
    }

    // Zone bands (strata) + headers + gates.
    const firstTerminalRank = PIPELINE_ZONES.length;
    this.zoneOrder.forEach((z) => {
      const i = rank[z] ?? 0;
      const y = i * zoneRowH;
      const isTerminal = TERMINAL_ZONES.includes(z);
      const color = ZONE_COLORS[z] || "#94a3b8";

      const band = ctx.createLinearGradient(0, y, 0, y + zoneRowH);
      if (isTerminal) {
        band.addColorStop(0, "rgba(50, 24, 32, 0.45)");
        band.addColorStop(1, "rgba(30, 14, 20, 0.30)");
      } else {
        band.addColorStop(0, i % 2 === 0 ? "rgba(15, 23, 42, 0.5)" : "rgba(30, 41, 59, 0.35)");
        band.addColorStop(1, i % 2 === 0 ? "rgba(12, 19, 35, 0.38)" : "rgba(24, 34, 50, 0.26)");
      }
      ctx.fillStyle = band;
      ctx.fillRect(-4500, y + 4, 9000, zoneRowH - 8);
      ctx.strokeStyle = "rgba(51, 65, 85, 0.75)";
      ctx.lineWidth = 1;
      ctx.strokeRect(-4500, y, 9000, zoneRowH);

      ctx.fillStyle = color;
      ctx.font = "bold 11px ui-sans-serif, system-ui, sans-serif";
      ctx.fillText(`${z}  (${this.countForZone(z)})`, -4470, y + 20);

      if (z === "ACTIVE") {
        ctx.strokeStyle = "rgba(16, 185, 129, 0.9)";
        ctx.setLineDash([10, 6]);
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(-4500, y + 2);
        ctx.lineTo(4500, y + 2);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = "rgba(16, 185, 129, 0.95)";
        ctx.font = "bold 10px ui-monospace, monospace";
        ctx.fillText("── EXECUTION GATE (shadow → live boundary) ──", -4470, y - 6);
      }

      if (i === firstTerminalRank && firstTerminalRank < this.zoneOrder.length) {
        ctx.strokeStyle = "rgba(244, 63, 94, 0.6)";
        ctx.setLineDash([4, 4]);
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(-4500, y);
        ctx.lineTo(4500, y);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = "rgba(244, 63, 94, 0.85)";
        ctx.font = "bold 10px ui-monospace, monospace";
        ctx.fillText("── INACTIVE / TERMINAL (never live) ──", -4470, y - 6);
      }
    });

    // Transition trails (zone moves only).
    const cutoff = Date.now() - 15000;
    for (const sid in this.trails) {
      const trail = this.trails[sid];
      if (!trail) continue;
      const pts = trail.filter((p) => p.t > cutoff || sid === this.selectedId);
      if (pts.length < 2) continue;
      ctx.strokeStyle = "rgba(56, 189, 248, 0.35)";
      ctx.lineWidth = 1.5;
      const p0 = pts[0];
      if (!p0) continue;
      ctx.beginPath();
      ctx.moveTo(p0.x, p0.y);
      for (let k = 1; k < pts.length; k++) {
        const p = pts[k];
        if (p) ctx.lineTo(p.x, p.y);
      }
      ctx.stroke();
    }

    // Hover pick (pre-pass so the highlight is frame-accurate).
    if (this.pointer && !this.dragging) {
      const [hwx, hwy] = this.screenToWorld(this.pointer.x, this.pointer.y);
      this.hoverId = this.pick(hwx, hwy)?.strategy_id ?? null;
      this.canvas.style.cursor = this.hoverId ? "pointer" : "grab";
    }

    const z = this.camera.zoom;
    const lod = z < 0.6 ? "low" : z < 1.35 ? "mid" : "high";
    const drawnLabels: Array<{ x: number; y: number; w: number; h: number }> = [];

    for (const n of this.nodes) {
      let x = n._tx, y = n._ty;
      const a = this.anims[n.strategy_id];
      if (a) {
        const rawT = (now - a.t0) / a.dur;
        if (rawT >= 1) delete this.anims[n.strategy_id];
        else {
          const t = easeInOutCubic(Math.max(0, rawT));
          x = a.fx + (a.tx - a.fx) * t;
          y = a.fy + (a.ty - a.fy) * t;
        }
      }
      n._sx = x; n._sy = y;

      const baseR = 5 + Math.min(8, (n.size_hint || 0) / 70);
      const r = baseR;
      const isSelected = n.strategy_id === this.selectedId;
      const isHovered = n.strategy_id === this.hoverId;
      const isLive = SPATIAL_LIVE_ZONES.has(n.zone);
      const isShadow = n.zone === "SHADOW";
      const pulse = isLive || isShadow ? 0.5 + 0.5 * Math.sin(now / 320 + n._tx) : 0;

      // Glow + drop shadow for depth.
      ctx.shadowColor = isSelected || isHovered ? rgba(n._color, 0.9) : "rgba(0, 0, 0, 0.6)";
      ctx.shadowBlur = isSelected ? 16 : isHovered ? 12 : 7;
      ctx.shadowOffsetY = 3;

      if (pulse > 0 && lod !== "low") {
        ctx.beginPath();
        ctx.arc(x, y, r + 4 + pulse * 5, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(16, 185, 129, ${0.12 + pulse * 0.18})`;
        ctx.fill();
      }

      if (lod !== "low") {
        const rings = Math.min(4, n.ring_count || 0);
        for (let k = 0; k < rings; k++) {
          ctx.beginPath();
          ctx.arc(x, y, r + 3 + k * 3, 0, Math.PI * 2);
          ctx.strokeStyle = "rgba(132, 204, 22, 0.35)";
          ctx.lineWidth = 1;
          ctx.stroke();
        }
      }

      const body = ctx.createRadialGradient(x - r * 0.4, y - r * 0.4, r * 0.2, x, y, r);
      body.addColorStop(0, "rgba(255,255,255,0.35)");
      body.addColorStop(0.35, n._color);
      body.addColorStop(1, n._color);
      ctx.fillStyle = body;
      ctx.beginPath();
      if (n._terminal) ctx.rect(x - r, y - r, r * 2, r * 2);
      else ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fill();
      ctx.lineWidth = isSelected ? 2.5 : isHovered ? 1.8 : 1;
      ctx.strokeStyle = isSelected ? "#ffffff" : isHovered ? "rgba(203, 213, 225, 0.9)" : "rgba(15, 23, 42, 0.9)";
      ctx.stroke();

      // Animated selection ring (dashed, rotating).
      if (isSelected) {
        ctx.save();
        ctx.translate(x, y);
        ctx.rotate((now / 1400) % (Math.PI * 2));
        ctx.beginPath();
        ctx.arc(0, 0, r + 8, 0, Math.PI * 2);
        ctx.setLineDash([6, 5]);
        ctx.strokeStyle = "rgba(108, 192, 255, 0.95)";
        ctx.lineWidth = 1.6;
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.restore();
      }

      // Internal evaluation-pipeline indicator (transient telemetry — never
      // relocates the node between lifecycle zones).
      const ev = n.evaluation;
      const flash = this.flashes[n.strategy_id];
      if (ev && ev.gates && lod !== "low") {
        const prog = Number(ev.progress) || 0;
        if (prog > 0) {
          ctx.beginPath();
          ctx.arc(x, y, r + 1.5, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * prog);
          ctx.strokeStyle = "rgba(56, 189, 248, 0.85)";
          ctx.lineWidth = 2;
          ctx.stroke();
        }
        const dotN = EVAL_GATES.length;
        const dotR = 2.1;
        const arcR = r + 7;
        for (let k = 0; k < dotN; k++) {
          const gk = EVAL_GATES[k];
          const st = (gk && ev.gates[gk]) || "NOT_RUN";
          const ang = Math.PI * 0.5 + (k / (dotN - 1)) * Math.PI;
          const dx = x + Math.cos(ang) * arcR;
          const dy = y + Math.sin(ang) * arcR;
          ctx.beginPath();
          ctx.arc(dx, dy, dotR, 0, Math.PI * 2);
          ctx.fillStyle = evalResultColor(st);
          ctx.fill();
          if (st === "RUNNING") {
            const rp = 0.5 + 0.5 * Math.sin(now / 160);
            ctx.beginPath();
            ctx.arc(dx, dy, dotR + 2 + rp * 2, 0, Math.PI * 2);
            ctx.strokeStyle = `rgba(234, 179, 8, ${0.4 + rp * 0.4})`;
            ctx.lineWidth = 1.2;
            ctx.stroke();
          }
        }
        if (ev.is_running && ev.running_stage) {
          const bx = x - r - 6, by = y - r - 6;
          ctx.beginPath();
          ctx.arc(bx, by, 3, 0, Math.PI * 2);
          const rp = 0.5 + 0.5 * Math.sin(now / 160);
          ctx.fillStyle = `rgba(234, 179, 8, ${0.5 + rp * 0.5})`;
          ctx.fill();
        }
        if (flash) {
          const rawT = (now - flash.t0) / flash.dur;
          if (rawT >= 1) delete this.flashes[n.strategy_id];
          else {
            const fa = (1 - rawT) * 0.6;
            ctx.beginPath();
            ctx.arc(x, y, r + 5 + rawT * 6, 0, Math.PI * 2);
            ctx.strokeStyle = `rgba(56, 189, 248, ${fa})`;
            ctx.lineWidth = 2;
            ctx.stroke();
          }
        }
      }

      ctx.shadowColor = "transparent";
      ctx.shadowBlur = 0;
      ctx.shadowOffsetY = 0;

      // Elevation stem (null → literal "?" — NOT_MEASURED, never guessed).
      if (lod !== "low") {
        if (n.elevation === null) {
          ctx.fillStyle = "rgba(148,163,184,0.85)";
          ctx.font = "8px ui-monospace, monospace";
          ctx.fillText("?", x - 2, y - r - 4);
        } else {
          const eh = Math.max(3, n.elevation * 16);
          ctx.strokeStyle = "rgba(56, 189, 248, 0.55)";
          ctx.lineWidth = 1.5;
          ctx.beginPath();
          ctx.moveTo(x, y - r);
          ctx.lineTo(x, y - r - eh);
          ctx.stroke();
        }
      }

      if (lod === "high" || isSelected) {
        ctx.fillStyle = isSelected ? "#ffffff" : isHovered ? "#e2e8f0" : "#cbd5e1";
        ctx.font = `${isSelected ? "bold " : ""}9px ui-monospace, monospace`;
        const label = String(n.strategy_id).substring(0, 14);
        const lw = ctx.measureText(label).width;
        const lh = 10;
        const lx = x + r + 4;
        const ly = y + 3;
        let collides = false;
        for (const b of drawnLabels) {
          if (lx < b.x + b.w && lx + lw > b.x && ly - lh < b.y && ly > b.y - b.h) { collides = true; break; }
        }
        if (!collides || isSelected) {
          drawnLabels.push({ x: lx, y: ly, w: lw, h: lh });
          ctx.fillText(label, lx, ly);
        }
      }
    }

    // Depth fog toward the far edge of the strata (2.5D cue, screen space).
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    const fogH = Math.min(this.canvas.height, this.canvas.height * 0.22);
    const fog = ctx.createLinearGradient(0, 0, 0, fogH);
    fog.addColorStop(0, "rgba(11, 18, 32, 0.85)");
    fog.addColorStop(1, "rgba(11, 18, 32, 0)");
    ctx.fillStyle = fog;
    ctx.fillRect(0, 0, this.canvas.width, fogH);
  }

  private startLoop(): void {
    const frame = (now: number): void => {
      if (this.disposed) return;
      this.draw(now);
      this.raf = requestAnimationFrame(frame);
    };
    this.raf = requestAnimationFrame(frame);
  }
}
