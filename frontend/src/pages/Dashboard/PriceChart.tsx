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
 * Data discipline:
 *  - Candles come from /api/chart/history (broker-native with explicit
 *    ENGINE_STATE fallback provenance) — NEVER synthesized, NEVER gap-filled.
 *  - Zones / BOS / midlines / liquidity sweeps / order lines render ONLY
 *    from the backend `visual_overlays` payload; the chart computes no SMC.
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

export interface PriceChartProps {
  bars: Bar[];
  digits: number;
  /** Backend provenance word for the bars (BROKER_NATIVE / ENGINE_STATE / UNAVAILABLE). */
  source: string | null;
  symbol: string | null;
  timeframe: string | null;
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
  idx: number;
  x: number;
  y: number;
}

export function PriceChart({
  bars,
  digits,
  source,
  symbol,
  timeframe,
  overlays,
  liveBid,
  cursorIso,
  caption,
  stale,
  busy,
  error,
  onRetry,
}: PriceChartProps) {
  const [visible, setVisible] = useState(180);
  const [hover, setHover] = useState<Hover | null>(null);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const rtVersion = useRealtimeVersion();

  // ---- data window (pure slice of backend bars — no gap fill, no synthesis)
  const shown = useMemo(() => bars.filter((b) => b.time).slice(-visible), [bars, visible]);

  const { timeIndex, indexAtOrBefore } = useMemo(() => {
    const m = new Map<string, number>();
    shown.forEach((b, i) => m.set(b.time, i));
    // index of the LAST bar at-or-before a time string (string compare works
    // on ISO timestamps; mirrors Web/replay_panel.js cursor search)
    const atOrBefore = (iso: string): number => {
      const probe = iso.slice(0, 19);
      for (let i = shown.length - 1; i >= 0; i--) {
        if ((shown[i]?.time ?? "").slice(0, 19) <= probe) return i;
      }
      return -1;
    };
    return { timeIndex: m, indexAtOrBefore: atOrBefore };
  }, [shown]);

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

  // ---- scene consumed by the painter via refs (no RAF restart per frame)
  const sceneRef = useRef<PainterScene | null>(null);
  sceneRef.current = {
    shown,
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

  const headerNote = (
    <span className="l4-chip-row" style={{ marginInlineStart: "auto" }}>
      {[90, 180, 360, 720].map((o) => (
        <button key={o} className={`btn small ${visible === o ? "primary" : "ghost"}`} onClick={() => setVisible(o)} title={`show last ${o} bars`}>
          {o}
        </button>
      ))}
    </span>
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
      <div className="l4-chart__head">
        <span className="l4-chip accent">{symbol ?? "—"}</span>
        <span className="l4-chip">{timeframe ?? "—"}</span>
        <span className={`l4-chip ${source === "BROKER_NATIVE" ? "good" : source === "UNAVAILABLE" ? "bad" : "warn"}`}>
          {source ?? "UNAVAILABLE"}
        </span>
        {stale && <span className="l4-chip warn">TICK STALE</span>}
        {caption && <span className="timestamp-note">{caption}</span>}
        {headerNote}
      </div>
      {shown.length === 0 ? (
        stateBlock
      ) : (
        <div
          ref={stageRef}
          className="mc-stage"
          onMouseMove={(e) => {
            const rect = e.currentTarget.getBoundingClientRect();
            const px = e.clientX - rect.left;
            const plotW = rect.width - AXIS_W - PAD_LEFT;
            if (plotW <= 0) return;
            const idx = Math.max(0, Math.min(shown.length - 1, Math.floor(((px - PAD_LEFT) / plotW) * shown.length)));
            setHover({ idx, x: px, y: e.clientY - rect.top });
          }}
          onMouseLeave={() => setHover(null)}
        >
          <canvas ref={canvasRef} aria-label={`${shown.length} ${timeframe ?? ""} candles for ${symbol ?? "market"}`} role="img" />
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
