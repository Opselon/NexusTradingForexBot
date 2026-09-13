/**
 * PriceChart — hand-rolled SVG candle chart for the Dashboard (lane 4).
 *
 * Data discipline:
 *  - Candles come from /api/chart/history (broker-native with explicit
 *    ENGINE_STATE fallback provenance) — NEVER synthesized, NEVER gap-filled.
 *  - Zones / BOS / midlines / liquidity sweeps / order lines render ONLY
 *    from the backend `visual_overlays` payload; the chart computes no SMC.
 *  - The replay cursor (KNOWN/UNKNOWN boundary, ported from
 *    Web/replay_panel.js drawKnownBoundary) dims everything right of the
 *    cursor and labels it FUTURE = UNKNOWN: decision-visible vs not.
 *
 * Presentation only: pure props in, SVG out, no fetch, no cache.
 */

import { useMemo, useState } from "react";
import type { Bar } from "@/types/domain";
import type { OverlayLine, OverlayRect, OverlayOrderLines } from "../_shared/contracts";
import { formatPrice, formatTime } from "@/lib/format";
import "@/pages/_shared/pages.css";

const W = 1000;
const H = 340;
const PAD_TOP = 10;
const PAD_BOTTOM = 26;
const AXIS_W = 62;

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

function zoneClass(type: string | undefined): string {
  const t = (type ?? "").toUpperCase();
  if (t.includes("FVG") && t.includes("BULL")) return "l4-chart__zone--fvg-bull";
  if (t.includes("FVG")) return "l4-chart__zone--fvg-bear";
  if (t.includes("STOP_HUNT") || t.includes("SWEEP")) return "l4-chart__zone--sweep";
  if (t.includes("ORDER_BLOCK")) return "l4-chart__zone--ob";
  return "l4-chart__zone--ob";
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
  const [hover, setHover] = useState<{ idx: number; x: number; y: number } | null>(null);

  const view = useMemo(() => {
    const all = bars.filter((b) => b.time);
    const shown = all.slice(-visible);
    if (shown.length === 0) return null;
    let lo = Infinity;
    let hi = -Infinity;
    for (const b of shown) {
      if (b.high !== null && b.high > hi) hi = b.high;
      if (b.low !== null && b.low < lo) lo = b.low;
      if (b.open !== null) {
        lo = Math.min(lo, b.open);
        hi = Math.max(hi, b.open);
      }
      if (b.close !== null) {
        lo = Math.min(lo, b.close);
        hi = Math.max(hi, b.close);
      }
    }
    if (!Number.isFinite(lo) || !Number.isFinite(hi)) return null;
    if (typeof liveBid === "number" && Number.isFinite(liveBid)) {
      lo = Math.min(lo, liveBid);
      hi = Math.max(hi, liveBid);
    }
    // overlay prices extend the visible range so zones never clip silently
    for (const z of overlays?.rectangles ?? []) {
      if (Number.isFinite(z.price_low)) lo = Math.min(lo, z.price_low);
      if (Number.isFinite(z.price_high)) hi = Math.max(hi, z.price_high);
    }
    const pad = (hi - lo) * 0.08 || 0.5;
    lo -= pad;
    hi += pad;

    const plotW = W - AXIS_W;
    const bw = plotW / shown.length;
    const x = (i: number) => i * bw;
    const y = (p: number) => PAD_TOP + ((hi - p) / (hi - lo)) * (H - PAD_TOP - PAD_BOTTOM);
    const timeIndex = new Map<string, number>();
    shown.forEach((b, i) => timeIndex.set(b.time, i));
    // index of the LAST bar at-or-before a time string (string compare works
    // on ISO timestamps; mirrors Web/replay_panel.js cursor search)
    const indexAtOrBefore = (iso: string): number => {
      const probe = iso.slice(0, 19);
      for (let i = shown.length - 1; i >= 0; i--) {
        const t = shown[i]?.time ?? "";
        if (t.slice(0, 19) <= probe) return i;
      }
      return -1;
    };
    return { shown, lo, hi, bw, x, y, plotW, timeIndex, indexAtOrBefore };
  }, [bars, visible, liveBid, overlays]);

  const headerNote = (
    <span className="l4-chip-row" style={{ marginInlineStart: "auto" }}>
      {[
        { id: 90, label: "90" },
        { id: 180, label: "180" },
        { id: 360, label: "360" },
        { id: 720, label: "720" },
      ].map((o) => (
        <button
          key={o.id}
          className={`btn small ${visible === o.id ? "primary" : "ghost"}`}
          onClick={() => setVisible(o.id)}
          title={`show last ${o.id} bars`}
        >
          {o.label}
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
      <span>no candles yet — the chart renders only real MT5/engine bars, never synthetic ones.</span>
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
      {!view ? (
        stateBlock
      ) : (
        <>
          <svg
            className="l4-chart__canvas"
            viewBox={`0 0 ${W} ${H}`}
            role="img"
            aria-label={`${view.shown.length} ${timeframe ?? ""} candles for ${symbol ?? "market"}`}
            onMouseMove={(e) => {
              const rect = e.currentTarget.getBoundingClientRect();
              const px = ((e.clientX - rect.left) / rect.width) * W;
              const idx = Math.max(0, Math.min(view.shown.length - 1, Math.floor(px / view.bw)));
              setHover({ idx, x: e.clientX - rect.left, y: e.clientY - rect.top });
            }}
            onMouseLeave={() => setHover(null)}
          >
            {/* horizontal price gridlines + axis labels (5 bands) */}
            {Array.from({ length: 5 }, (_, i) => {
              const p = view.lo + ((view.hi - view.lo) * i) / 4;
              const gy = view.y(p);
              return (
                <g key={i}>
                  <line className="l4-chart__grid" x1={0} x2={view.plotW} y1={gy} y2={gy} />
                  <text className="l4-chart__grid-label" x={W - AXIS_W + 6} y={gy + 3}>
                    {formatPrice(p, digits)}
                  </text>
                </g>
              );
            })}

            {/* SMC/ICT zones from the backend overlay payload */}
            {(overlays?.rectangles ?? []).map((z, i) => {
              const startIdx = z.time ? (view.timeIndex.get(z.time) ?? view.indexAtOrBefore(z.time)) : 0;
              if (startIdx < 0) return null;
              const zx = view.x(startIdx);
              const zy = view.y(z.price_high);
              const zh = Math.max(1, view.y(z.price_low) - view.y(z.price_high));
              const label = z.type === "BULLISH_ORDER_BLOCK" || z.type === "BEARISH_ORDER_BLOCK"
                ? `ob ${typeof z.ai_confidence === "number" ? `${Math.round(z.ai_confidence * 100)}%` : ""}`
                : (z.type ?? "").toLowerCase();
              return (
                <g key={z.id ?? `z${i}`}>
                  <rect className={`l4-chart__zone ${zoneClass(z.type)}`} x={zx} y={zy} width={Math.max(2, view.plotW - zx)} height={zh} />
                  <text className="l4-chart__zone-label" x={zx + 3} y={Math.min(Math.max(zy + 9, 9), H - 30)}>
                    {label}
                  </text>
                </g>
              );
            })}

            {/* BOS lines (horizontal, backend-computed break levels) */}
            {(overlays?.bos_lines ?? []).slice(-12).map((l, i) => {
              const ly = view.y(l.price);
              if (ly < PAD_TOP || ly > H - PAD_BOTTOM) return null;
              return <line key={l.id ?? `b${i}`} className="l4-chart__bos" x1={0} x2={view.plotW} y1={ly} y2={ly} />;
            })}

            {/* 50% equilibrium midlines */}
            {(overlays?.midlines ?? []).slice(-4).map((m, i) => {
              const my = view.y(m.price);
              if (my < PAD_TOP || my > H - PAD_BOTTOM) return null;
              const fromIdx = m.time_start ? view.indexAtOrBefore(m.time_start) : 0;
              return (
                <g key={m.id ?? `m${i}`}>
                  <line className="l4-chart__mid" x1={Math.max(0, view.x(fromIdx))} x2={view.plotW} y1={my} y2={my} />
                  <text className="l4-chart__grid-label" x={Math.max(0, view.x(fromIdx)) + 3} y={my - 3}>
                    {m.label ?? "50%"}
                  </text>
                </g>
              );
            })}

            {/* liquidity sweep markers (triangles at the swept extreme) */}
            {(overlays?.liq_markers ?? []).slice(-15).map((m, i) => {
              const idx = m.time ? (view.timeIndex.get(m.time) ?? -1) : -1;
              if (idx < 0) return null;
              const mx = view.x(idx) + view.bw / 2;
              const my = view.y(m.price);
              const up = (m.type ?? "").includes("BUY_SIDE");
              const pts = up
                ? `${mx - 4},${my - 3} ${mx + 4},${my - 3} ${mx},${my + 4}`
                : `${mx - 4},${my + 3} ${mx + 4},${my + 3} ${mx},${my - 4}`;
              return <polygon key={m.id ?? `s${i}`} className="l4-chart__marker" points={pts} />;
            })}

            {/* candles */}
            {view.shown.map((b, i) => {
              if (b.open === null || b.close === null || b.high === null || b.low === null) return null;
              const up = b.close >= b.open;
              const cx = view.x(i) + view.bw / 2;
              const bodyTop = view.y(Math.max(b.open, b.close));
              const bodyH = Math.max(1, Math.abs(view.y(b.open) - view.y(b.close)));
              const cw = Math.max(1, Math.min(view.bw * 0.68, 11));
              return (
                <g key={`${b.time}-${i}`} className={b.is_complete === false ? "l4-chart__candle-forming" : undefined}>
                  <line className={up ? "l4-chart__candle-up" : "l4-chart__candle-down"} x1={cx} x2={cx} y1={view.y(b.high)} y2={view.y(b.low)} strokeWidth={1} />
                  <rect className={up ? "l4-chart__candle-up" : "l4-chart__candle-down"} x={cx - cw / 2} y={bodyTop} width={cw} height={bodyH} />
                </g>
              );
            })}

            {/* live quote line */}
            {typeof liveBid === "number" && Number.isFinite(liveBid) && (
              <g>
                <line className="l4-chart__order l4-chart__order--entry" x1={0} x2={view.plotW} y1={view.y(liveBid)} y2={view.y(liveBid)} strokeDasharray="1 0" opacity={0.75} />
                <text className="l4-chart__grid-label" x={W - AXIS_W + 6} y={view.y(liveBid) + 3} fill="var(--accent-strong)">
                  {formatPrice(liveBid, digits)}
                </text>
              </g>
            )}

            {/* open-position entry/SL/TP from the order_lines overlay */}
            {overlays?.order_lines && (
              <g>
                {(
                  [
                    ["entry", overlays.order_lines.entry, "l4-chart__order--entry"],
                    ["sl", overlays.order_lines.stop_loss, "l4-chart__order--sl"],
                    ["tp", overlays.order_lines.take_profit, "l4-chart__order--tp"],
                  ] as const
                ).map(([k, v, cls]) =>
                  typeof v === "number" && Number.isFinite(v) ? (
                    <g key={k}>
                      <line className={`l4-chart__order ${cls}`} x1={0} x2={view.plotW} y1={view.y(v)} y2={view.y(v)} strokeDasharray="6 4" />
                      <text className="l4-chart__grid-label" x={4} y={view.y(v) - 3}>
                        {k} {formatPrice(v, digits)}
                      </text>
                    </g>
                  ) : null,
                )}
              </g>
            )}

            {/* replay KNOWN/UNKNOWN boundary */}
            {cursorIso && (() => {
              const ci = view.indexAtOrBefore(cursorIso);
              if (ci < 0) return null;
              const cx2 = view.x(ci) + view.bw / 2;
              return (
                <g>
                  <rect className="l4-chart__future" x={cx2} y={0} width={Math.max(0, view.plotW - cx2)} height={H - PAD_BOTTOM} />
                  <line className="l4-chart__cursor" x1={cx2} x2={cx2} y1={0} y2={H - PAD_BOTTOM} />
                  <text className="l4-chart__cursor-label" x={Math.min(cx2 + 4, W - 130)} y={12}>
                    REPLAY CURSOR (KNOWN)
                  </text>
                  <text className="l4-chart__cursor-label" x={Math.min(cx2 + 4, W - 130)} y={24}>
                    FUTURE = UNKNOWN
                  </text>
                </g>
              );
            })()}

            {/* time axis (4 labels) */}
            {Array.from({ length: 4 }, (_, i) => {
              const idx = Math.floor(((view.shown.length - 1) * i) / 3);
              const b = view.shown[idx];
              if (!b) return null;
              return (
                <text key={i} className="l4-chart__tick" x={Math.min(view.x(idx) + 2, W - AXIS_W - 52)} y={H - 8}>
                  {formatTime(b.time)}
                </text>
              );
            })}

            {/* crosshair */}
            {hover && view.shown[hover.idx] && (
              <line className="l4-chart__cross" x1={view.x(hover.idx) + view.bw / 2} x2={view.x(hover.idx) + view.bw / 2} y1={0} y2={H - PAD_BOTTOM} />
            )}
          </svg>
          {hover && view.shown[hover.idx] && (
            <div
              className="l4-chart__tip"
              style={{
                insetInlineStart: Math.min(hover.x + 12, 760),
                insetBlockStart: Math.max(4, hover.y - 60),
              }}
            >
              {(() => {
                const b = view.shown[hover.idx]!;
                return `time  ${b.time.replace("T", " ").slice(0, 19)}
open  ${formatPrice(b.open, digits)}  high ${formatPrice(b.high, digits)}
low   ${formatPrice(b.low, digits)}  close ${formatPrice(b.close, digits)}${b.is_complete === false ? "\n(forming bar)" : ""}`;
              })()}
            </div>
          )}
        </>
      )}
    </section>
  );
}
