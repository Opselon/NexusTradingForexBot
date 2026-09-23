/**
 * PriceChart — DPR-aware Canvas-2D candlestick console (lane M).
 *
 * Port of legacy Web/app.js drawChart (~L4400-5214) + updateCrosshairTooltip
 * (~L5220) with the React quality bar: devicePixelRatio backing store like
 * Web/command_center_spatial.js resize(), requestAnimationFrame eased redraw
 * on every new SSE state_version, ResizeObserver re-layout, hover crosshair
 * with the legacy OHLC tooltip semantics. Frame drawing lives in
 * ./chartPainter.ts (pure Canvas2D, no state).
 *
 * TradingView-style view controls (2026-09-23):
 *  - TIMEFRAME toolbar: 1m/3m/5m/10m/15m/30m/1h/4h/1D/1W request broker-
 *    native bars per timeframe via /api/chart/history?timeframe= (backend
 *    allowlist; the active chip follows the SERVED timeframe, never the
 *    clicked one, so a backend that ignores the request cannot lie here).
 *  - WHEEL ZOOM: continuous slot-count zoom anchored under the cursor
 *    (wheel up = zoom in); shift/trackpad-X pans. Native non-passive
 *    listener (React registers wheel as passive — onWheel cannot
 *    preventDefault).
 *  - DRAG PAN: pointer-capture drag through the fetched history and INTO
 *    the empty future region (bounded to 40% of the view); new bars keep
 *    sliding in only while pinned at the right edge (followLive), and the
 *    LIVE chip jumps back to the latest bar.
 *  - View state is a window model: left = absolute index of the first
 *    slot, count = slot budget; `shown` fills the leading slots and the
 *    tail stays empty (painter marks the data/future boundary).
 *
 * Data discipline (unchanged):
 *  - Candles come from /api/chart/history (broker-native with explicit
 *    ENGINE_STATE fallback provenance) — NEVER synthesized, NEVER gap-filled.
 *  - Zones / BOS / midlines / liquidity sweeps / order lines render ONLY
 *    from the backend `visual_overlays` payload; the chart computes no SMC.
 *    Those overlays are computed by the engine on ITS native timeframe —
 *    when a foreign timeframe is served the header discloses it instead of
 *    implying the markers were computed on these bars.
 *  - EMA/trend overlay lines: the canonical snapshot and chart history carry
 *    NO indicator series (verified: server.py visual_overlays keys are
 *    rectangles/bos_lines/midlines/liq_markers/order_lines). Client-side
 *    indicator math is forbidden (audit lane-09), so none are drawn.
 *  - The replay cursor (KNOWN/UNKNOWN boundary) dims everything right of the
 *    cursor and labels it FUTURE = UNKNOWN: decision-visible vs not.
 *
 * Presentation only: pure props in, canvas out, no fetch, no cache.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import type { Bar } from "@/types/domain";
import type { OverlayLine, OverlayRect, OverlayOrderLines } from "../_shared/contracts";
import { useRealtimeVersion } from "@/hooks/useRealtime";
import { formatPrice } from "@/lib/format";
import { AXIS_W, PAD_LEFT, paintChart, readPalette, type PainterScene } from "./chartPainter";
import "@/pages/_shared/pages.css";
import "./market-console.css";

/** [chip label, MT5 code] — the switcher set (backend allowlist is wider). */
const TF_CHIPS: ReadonlyArray<readonly [string, string]> = [
  ["1m", "M1"],
  ["3m", "M3"],
  ["5m", "M5"],
  ["10m", "M10"],
  ["15m", "M15"],
  ["30m", "M30"],
  ["1h", "H1"],
  ["4h", "H4"],
  ["1D", "D1"],
  ["1W", "W1"],
];

const MIN_SLOTS = 20;
/** Empty slots allowed past the last bar while panning into the future. */
const maxFutureFor = (slots: number) => Math.floor(slots * 0.4);
const clampInt = (v: number, lo: number, hi: number) => Math.min(Math.max(Math.round(v), lo), hi);

export interface PriceChartProps {
  bars: Bar[];
  digits: number;
  /** Backend provenance word for the bars (BROKER_NATIVE / ENGINE_STATE / UNAVAILABLE). */
  source: string | null;
  symbol: string | null;
  /** SERVED timeframe from the backend response (the header chip's truth). */
  timeframe: string | null;
  /** Timeframe to highlight NOW (requested while fetching, served when settled). */
  activeTf?: string;
  /** Chip click → parent refetches /api/chart/history?timeframe=. */
  onTfChange?: (timeframeCode: string) => void;
  /** Engine-native timeframe — set when the served one differs (overlay disclosure). */
  engineTimeframe?: string | null;
  overlays?: {
    rectangles?: OverlayRect[];
    bos_lines?: OverlayLine[];
    midlines?: OverlayLine[];
    liq_markers?: OverlayLine[];
    order_lines?: OverlayOrderLines | null;
  };
  /** Live quote line (snapshot bid) — null renders no line, never 0. */
  liveBid?: number | null;
  /** Replay cursor ISO time: right of it is UNKNOWN (dimmed + labeled). */
  cursorIso?: string | null;
  /** Freshness note rendered in the header strip. */
  caption?: string;
  stale?: boolean;
  /** External state (parent loading/error) — the chart stays honest-empty. */
  busy?: boolean;
  error?: string | null;
  onRetry?: () => void;
}

interface Hover {
  /** SLOT index within the window (may exceed shown.length over future space). */
  idx: number;
  x: number;
  y: number;
}

interface View {
  /** Slots spanned by the plot (the zoom level). */
  visible: number;
  /** Absolute index of the leftmost slot (meaningful when not followLive). */
  left: number;
  /** Pinned at the right edge: new bars keep sliding the window in. */
  followLive: boolean;
}

export function PriceChart({
  bars,
  digits,
  source,
  symbol,
  timeframe,
  activeTf,
  onTfChange,
  engineTimeframe,
  overlays,
  liveBid,
  cursorIso,
  caption,
  stale,
  busy,
  error,
  onRetry,
}: PriceChartProps) {
  const [view, setView] = useState<View>({ visible: 180, left: 0, followLive: true });
  const [hover, setHover] = useState<Hover | null>(null);
  const [dragging, setDragging] = useState(false);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const dragRef = useRef<DragState | null>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const rtVersion = useRealtimeVersion();

  // ---- data window (pure slice of backend bars — no gap fill, no synthesis)
  const content = useMemo(() => bars.filter((b) => b.time), [bars]);
  const len = content.length;

  // Render-time mirrors for imperative handlers (wheel/drag listeners bind
  // once and must never read a stale closure state).
  const viewRef = useRef(view);
  viewRef.current = view;
  const lenRef = useRef(len);
  lenRef.current = len;

  const leftMax = Math.max(0, len + maxFutureFor(view.visible) - view.visible);
  const effLeft = view.followLive
    ? Math.max(0, len - view.visible)
    : Math.min(Math.max(view.left, 0), leftMax);
  const leftEffRef = useRef(effLeft);
  leftEffRef.current = effLeft;

  const shown = useMemo(
    () => content.slice(effLeft, Math.min(len, effLeft + view.visible)),
    [content, effLeft, len, view.visible],
  );
  const count = view.visible;

  // ---- view mutations (all clamp + recompute followLive at the boundary)
  const applyLeft = (nl: number) => {
    const lenNow = lenRef.current;
    setView((v) => {
      const maxL = Math.max(0, lenNow + maxFutureFor(v.visible) - v.visible);
      const c = Math.min(Math.max(Math.round(nl), 0), maxL);
      return { ...v, left: c, followLive: c === Math.max(0, lenNow - v.visible) };
    });
  };

  /** Zoom / preset: right-anchored while live, cursor-anchored while panned. */
  const applyView = (nextVisible: number, rel = 0.5) => {
    const lenNow = lenRef.current;
    setView((v) => {
      const vis = clampInt(nextVisible, MIN_SLOTS, Math.max(MIN_SLOTS, lenNow));
      const liveNow = leftEffRef.current === Math.max(0, lenNow - v.visible);
      let nl: number;
      if (liveNow) {
        nl = Math.max(0, lenNow - vis); // stay pinned to the latest bar
      } else {
        const abs = leftEffRef.current + rel * v.visible;
        nl = Math.round(abs - rel * vis); // keep the bar under the cursor
      }
      const maxL = Math.max(0, lenNow + maxFutureFor(vis) - vis);
      nl = Math.min(Math.max(nl, 0), maxL);
      return { visible: vis, left: nl, followLive: nl === Math.max(0, lenNow - vis) };
    });
  };

  // Timeframe switch (active chip changed) → snap back to the latest bar,
  // like a TV timeframe change: the historical position of another TF's
  // window is meaningless here.
  useEffect(() => {
    setView((v) => ({ ...v, followLive: true }));
  }, [activeTf]);

  const { timeIndex, indexAtOrBefore } = useMemo(() => {
    const m = new Map<string, number>();
    content.forEach((b, i) => m.set(b.time, i));
    // ABSOLUTE index of the LAST bar at-or-before a time string (string
    // compare works on ISO timestamps; mirrors Web/replay_panel.js cursor
    // search). May point outside the visible window — the painter converts.
    const atOrBefore = (iso: string): number => {
      const probe = iso.slice(0, 19);
      for (let i = content.length - 1; i >= 0; i--) {
        if ((content[i]?.time ?? "").slice(0, 19) <= probe) return i;
      }
      return -1;
    };
    return { timeIndex: m, indexAtOrBefore: atOrBefore };
  }, [content]);

  // ---- target price scale (backend values + live bid + overlay extents only)
  const target = useMemo(() => {
    let lo = Infinity;
    let hi = -Infinity;
    for (const b of shown) {
      for (const p of [b.high, b.low, b.open, b.close]) {
        if (typeof p === "number" && Number.isFinite(p)) {
          if (p > hi) hi = p;
          if (p < lo) lo = p;
        }
      }
    }
    if (typeof liveBid === "number" && Number.isFinite(liveBid)) {
      lo = Math.min(lo, liveBid);
      hi = Math.max(hi, liveBid);
    }
    for (const z of overlays?.rectangles ?? []) {
      if (Number.isFinite(z.price_low)) lo = Math.min(lo, z.price_low);
      if (Number.isFinite(z.price_high)) hi = Math.max(hi, z.price_high);
    }
    if (!Number.isFinite(lo) || !Number.isFinite(hi)) return null;
    const pad = (hi - lo) * 0.08 || 0.5; // legacy padding semantics
    return { lo: lo - pad, hi: hi + pad };
  }, [shown, liveBid, overlays]);

  // ---- ResizeObserver: CSS-pixel stage size drives the backing store
  useEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const r = entries[0]?.contentRect;
      if (r) {
        setSize({ w: Math.max(120, r.width), h: Math.max(120, r.height) });
        kickRef.current?.(60);
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // ---- wheel: non-passive native listener (React registers wheel as
  // passive, so onWheel cannot preventDefault and the page would scroll).
  // Zoom is anchored under the cursor while panned, right-anchored at live;
  // shift (or a dominant trackpad deltaX) pans instead of zooming.
  const stageMounted = shown.length > 0;
  useEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      const plotW = rect.width - AXIS_W - PAD_LEFT;
      if (plotW <= 0) return;
      const v = viewRef.current;
      const base = leftEffRef.current;
      const horiz = Math.abs(e.deltaX) > Math.abs(e.deltaY);
      if (e.shiftKey || horiz) {
        const deltaPx = horiz ? e.deltaX : e.deltaY;
        const slots = Math.round((deltaPx * v.visible) / plotW);
        if (slots !== 0) applyLeft(base + slots);
        return;
      }
      const rel = Math.min(1, Math.max(0, (e.clientX - rect.left - PAD_LEFT) / plotW));
      // wheel up (deltaY < 0) → fewer slots → zoom IN (conventional sign)
      const factor = Math.exp(e.deltaY * 0.0016);
      applyView(Math.round(v.visible * factor), rel);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
    // Rebinds with the stage mount (it renders only when bars exist).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stageMounted]);

  // ---- scene consumed by the painter via refs (no RAF restart per frame)
  const sceneRef = useRef<PainterScene | null>(null);
  sceneRef.current = {
    shown,
    left: effLeft,
    count,
    overlays,
    liveBid,
    cursorIso,
    digits,
    hoverIdx: hover?.idx ?? -1,
    hoverY: hover?.y ?? null,
    timeIndex,
    indexAtOrBefore,
  };
  const scaleRef = useRef<{ lo: number; hi: number } | null>(null);
  const targetRef = useRef(target);
  targetRef.current = target;
  const sizeRef = useRef(size);
  sizeRef.current = size;

  // ---- the RAF loop: eased scale glide (new SSE versions animate the price
  // window like legacy pan easing), idle-when-clean to spare the CPU
  const rafRef = useRef<number | null>(null);
  const dirtyRef = useRef(true);
  const animUntilRef = useRef(0);
  const kickRef = useRef<((ms?: number) => void) | null>(null);

  useEffect(() => {
    const cv = canvasRef.current;
    if (!cv) return;
    const pal = readPalette();
    const mono = getComputedStyle(document.documentElement).getPropertyValue("--mono").trim() || "monospace";

    const draw = () => {
      const { w, h } = sizeRef.current;
      if (w <= 0 || h <= 0) return;
      const dpr = Math.min(2, window.devicePixelRatio || 1); // spatial.js parity
      const bwPix = Math.round(w * dpr);
      const bhPix = Math.round(h * dpr);
      if (cv.width !== bwPix || cv.height !== bhPix) {
        cv.width = bwPix;
        cv.height = bhPix;
        cv.style.width = `${w}px`;
        cv.style.height = `${h}px`;
      }
      const ctx = cv.getContext("2d");
      const sc = sceneRef.current;
      if (!ctx || !sc) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const scale = scaleRef.current;
      if (!scale || sc.shown.length === 0) return;
      paintChart(ctx, { w, h }, scale, sc, pal, mono);
    };

    const step = () => {
      const tgt = targetRef.current;
      if (tgt) {
        const cur = scaleRef.current ?? { lo: tgt.lo, hi: tgt.hi };
        const k = 0.22;
        const nlo = cur.lo + (tgt.lo - cur.lo) * k;
        const nhi = cur.hi + (tgt.hi - cur.hi) * k;
        const span = Math.max(1e-9, tgt.hi - tgt.lo);
        const done = Math.abs(nlo - tgt.lo) / span < 0.0004 && Math.abs(nhi - tgt.hi) / span < 0.0004;
        scaleRef.current = done ? { lo: tgt.lo, hi: tgt.hi } : { lo: nlo, hi: nhi };
      }
      draw();
      const stillAnimating = Date.now() < animUntilRef.current;
      const t2 = targetRef.current;
      const c2 = scaleRef.current;
      const scaleMoving =
        !!t2 &&
        !!c2 &&
        (Math.abs(c2.lo - t2.lo) / Math.max(1e-9, t2.hi - t2.lo) > 0.0004 ||
          Math.abs(c2.hi - t2.hi) / Math.max(1e-9, t2.hi - t2.lo) > 0.0004);
      if (dirtyRef.current || scaleMoving || stillAnimating) {
        dirtyRef.current = false;
        rafRef.current = window.requestAnimationFrame(step);
      } else {
        rafRef.current = null;
      }
    };
    const kick = (ms = 0) => {
      dirtyRef.current = true;
      animUntilRef.current = Math.max(animUntilRef.current, Date.now() + ms);
      if (rafRef.current === null) rafRef.current = window.requestAnimationFrame(step);
    };
    kickRef.current = kick;
    kick(60);
    return () => {
      if (rafRef.current !== null) window.cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
      kickRef.current = null;
    };
    // Rebinds when the canvas mounts/unmounts (empty-state <-> stage swap);
    // palette/mono are read here, size + data flow through refs.
  }, [shown.length > 0]);

  // Monitor / zoom changes flip devicePixelRatio without a resize event —
  // kick a repaint so the backing store stays crisp (draw() rescales itself).
  useEffect(() => {
    let alive = true;
    const arm = () => {
      if (!alive) return;
      const mq = window.matchMedia(`(resolution: ${window.devicePixelRatio || 1}dppx)`);
      const onChange = () => {
        kickRef.current?.(60);
        arm(); // re-arm for the new DPR value
      };
      mq.addEventListener("change", onChange, { once: true });
    };
    arm();
    return () => {
      alive = false;
    };
  }, []);

  // new SSE version or any data change → one smooth redraw pass
  useEffect(() => {
    kickRef.current?.(260);
  }, [rtVersion.version, shown, target, liveBid, overlays, cursorIso, hover]);

  const hovered = hover && hover.idx >= 0 ? shown[hover.idx] : null;

  const statusChips = (
    <>
      <span className="l4-chip accent">{symbol ?? "—"}</span>
      <span className="l4-chip">{timeframe ?? "—"}</span>
      <span className={`l4-chip ${source === "BROKER_NATIVE" || source === "MT5" ? "good" : source === "UNAVAILABLE" ? "bad" : "warn"}`}>
        {source ?? "UNAVAILABLE"}
      </span>
      {stale && <span className="l4-chip warn">TICK STALE</span>}
      {engineTimeframe && timeframe && timeframe !== engineTimeframe && (
        <span
          className="l4-chip warn"
          title="SMC overlays (zones / BOS / liquidity) are computed by the engine on its native timeframe — their time mapping on this chart is approximate"
        >
          overlays {engineTimeframe}-native
        </span>
      )}
      {caption && <span className="timestamp-note">{caption}</span>}
    </>
  );

  const toolRow = (
    <div className="l4-chart__tools" role="toolbar" aria-label="chart timeframe and zoom controls">
      <span className="l4-chip-row mc-tfrow" role="group" aria-label="chart timeframe">
        {TF_CHIPS.map(([lbl, code]) => (
          <button
            key={code}
            className={`btn small ${(activeTf ?? timeframe) === code ? "primary" : "ghost"}`}
            onClick={() => onTfChange?.(code)}
            title={`load broker-native ${lbl} (${code}) candles`}
          >
            {lbl}
          </button>
        ))}
      </span>
      <span className="l4-chip-row" style={{ marginInlineStart: "auto" }}>
        {[90, 180, 360, 720].map((o) => (
          <button
            key={o}
            className={`btn small ${view.visible === o ? "primary" : "ghost"}`}
            onClick={() => applyView(o)}
            title={`show last ${o} bars`}
          >
            {o}
          </button>
        ))}
        <button className="btn small ghost" onClick={() => applyView(Math.round(view.visible / 1.4))} title="zoom out (mouse wheel down)">
          −
        </button>
        <span className="mc-zoomval" title="visible slots (bars per screen width)">
          {view.visible}
        </span>
        <button className="btn small ghost" onClick={() => applyView(Math.round(view.visible * 1.4))} title="zoom in (mouse wheel up)">
          +
        </button>
        <button
          className={`btn small ${view.followLive ? "primary" : "ghost"}`}
          disabled={view.followLive}
          onClick={() => setView((v) => ({ ...v, followLive: true }))}
          title="jump back to the latest bar"
        >
          ◉ LIVE
        </button>
      </span>
    </div>
  );

  const stateBlock = error ? (
    <div className="l4-chart__state">
      <span>chart history failed: {error}</span>
      {onRetry && (
        <button className="btn small" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  ) : busy ? (
    <div className="l4-chart__state">
      <div className="spinner" />
      <span>loading broker history…</span>
    </div>
  ) : (
    <div className="l4-chart__state">
      <span className="glyph">∅</span>
      <span>Awaiting ticks — no candles yet. The chart renders only real MT5/engine bars, never synthetic ones.</span>
    </div>
  );

  return (
    <section className="l4-chart" aria-label="Price chart">
      <div className="l4-chart__head">{statusChips}</div>
      {toolRow}
      {shown.length === 0 ? (
        stateBlock
      ) : (
        <div
          ref={stageRef}
          className={`mc-stage${dragging ? " mc-stage--dragging" : ""}`}
          onPointerDown={(e) => {
            if (e.button !== 0) return;
            e.currentTarget.setPointerCapture(e.pointerId);
            dragRef.current = { x0: e.clientX, left0: leftEffRef.current, active: true };
            setDragging(true);
          }}
          onPointerMove={(e) => {
            const rect = e.currentTarget.getBoundingClientRect();
            const plotW = rect.width - AXIS_W - PAD_LEFT;
            const px = e.clientX - rect.left;
            if (plotW > 0) {
              const idx = Math.max(
                0,
                Math.min(viewRef.current.visible - 1, Math.floor(((px - PAD_LEFT) / plotW) * viewRef.current.visible)),
              );
              setHover({ idx, x: px, y: e.clientY - rect.top });
            }
            const d = dragRef.current;
            if (d?.active && plotW > 0) {
              // drag content right → reveal the past; left → walk into the future
              const slots = Math.round(((e.clientX - d.x0) * viewRef.current.visible) / plotW);
              applyLeft(d.left0 - slots);
            }
          }}
          onPointerUp={(e) => {
            dragRef.current = null;
            setDragging(false);
            if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
          }}
          onPointerCancel={() => {
            dragRef.current = null;
            setDragging(false);
          }}
          onPointerLeave={() => setHover(null)}
        >
          <canvas
            ref={canvasRef}
            aria-label={`${shown.length} ${timeframe ?? ""} candles for ${symbol ?? "market"} (wheel to zoom, drag to pan)`}
            role="img"
          />
          {hovered && hover && (
            <div className="mc-tip" style={{ insetInlineStart: Math.min(hover.x + 15, Math.max(0, size.w - 190)), insetBlockStart: Math.min(hover.y + 15, Math.max(0, size.h - 96)) }}>
              <div className="mc-tip__row">
                <span>{hovered.time.replace("T", " ").slice(0, 19)}</span>
                <span className={hovered.close !== null && hovered.open !== null && hovered.close >= hovered.open ? "up" : "down"}>
                  {hovered.is_complete === false ? "Forming" : "Completed"}
                </span>
              </div>
              <div className="mc-tip__ohlc">
                <span>O <b>{formatPrice(hovered.open, digits)}</b></span>
                <span>H <b>{formatPrice(hovered.high, digits)}</b></span>
                <span>L <b>{formatPrice(hovered.low, digits)}</b></span>
                <span>C <b>{formatPrice(hovered.close, digits)}</b></span>
                <span>V <b>{hovered.tick_volume ?? hovered.volume ?? "—"}</b></span>
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

/** Pointer-capture drag anchor (module-level so handlers never re-bind). */
interface DragState {
  x0: number;
  left0: number;
  active: boolean;
}
