/**
 * chartPainter — pure Canvas-2D painter for the Dashboard price chart
 * (lane M). No React, no state: it takes the current scene + eased price
 * scale and draws one frame. Ported draw order from legacy Web/app.js
 * drawChart (~L4400): backdrop → grid → zones → BOS → midlines → liq
 * markers → candles → quote line → order lines → replay boundary →
 * crosshair → axes. Every drawn value comes from the backend payload; the
 * painter computes coordinates only, never indicators or prices.
 */

import type { Bar } from "@/types/domain";
import type { OverlayLine, OverlayRect, OverlayOrderLines } from "@/pages/_shared/contracts";
import { niceTicks } from "@/components/viz/geometry";
import { formatPrice } from "@/lib/format";
import { useI18n } from "@/stores/i18nStore";

export const PAD_TOP = 10;
export const PAD_BOTTOM = 22;
export const AXIS_W = 60;
export const PAD_LEFT = 6;

export interface Palette {
  bg: string;
  grid: string;
  axisText: string;
  text: string;
  dim: string;
  green: string;
  red: string;
  amber: string;
  violet: string;
  accent: string;
  accentStrong: string;
  panel: string;
  borderStrong: string;
}

export interface PainterScene {
  /** Bars inside the visible window, in order (occupies slots 0..shown.length-1). */
  shown: Bar[];
  /** Window model: ABSOLUTE index of the leftmost slot … */
  left: number;
  /** … and how many slots the plot spans (may exceed shown.length → future space). */
  count: number;
  overlays?: {
    rectangles?: OverlayRect[];
    bos_lines?: OverlayLine[];
    midlines?: OverlayLine[];
    liq_markers?: OverlayLine[];
    order_lines?: OverlayOrderLines | null;
  };
  liveBid?: number | null;
  cursorIso?: string | null;
  digits: number;
  hoverIdx: number;
  /** Mouse Y in CSS px (legacy crosshair followed the pointer, not the bar). */
  hoverY: number | null;
  /** ABSOLUTE index into the full series (not window-relative). */
  timeIndex: Map<string, number>;
  /** ABSOLUTE index of the LAST bar at-or-before a time (-1 = none). */
  indexAtOrBefore: (iso: string) => number;
}

export function readPalette(): Palette {
  const cs = getComputedStyle(document.documentElement);
  const v = (name: string, fallback: string) => cs.getPropertyValue(name).trim() || fallback;
  return {
    bg: v("--bg-inset", "#0a0f16"),
    grid: v("--border", "#1c2836"),
    axisText: v("--text-faint", "#56697d"),
    text: v("--text", "#dbe4ee"),
    dim: v("--text-dim", "#8398ad"),
    green: v("--green", "#3ecf8e"),
    red: v("--red", "#f2564d"),
    amber: v("--amber", "#eba13f"),
    violet: v("--violet", "#9d7bf0"),
    accent: v("--accent", "#41a6f2"),
    accentStrong: v("--accent-strong", "#6cc0ff"),
    panel: v("--bg-panel", "#0d131b"),
    borderStrong: v("--border-strong", "#2b3d51"),
  };
}

function zoneStyle(type: string | undefined): { fill: string; label: string; stroke?: string } {
  // Legacy drawChart palette: bullish FVG green, bearish FVG red, valid OBs
  // white with border + confidence %, stop-hunt gold — verbatim semantics.
  const t = (type ?? "").toUpperCase();
  if (t.includes("STOP_HUNT") || t.includes("SWEEP")) return { fill: "rgba(235,161,63,0.30)", label: t.toLowerCase() };
  if (t.includes("ORDER_BLOCK")) return { fill: "rgba(255,255,255,0.07)", label: "ob", stroke: "rgba(255,255,255,0.35)" };
  if (t.includes("FVG") && t.includes("BEAR")) return { fill: "rgba(242,86,77,0.22)", label: t.toLowerCase() };
  if (t.includes("FVG")) return { fill: "rgba(62,207,142,0.22)", label: t.toLowerCase() };
  return { fill: "rgba(255,255,255,0.06)", label: t.toLowerCase() };
}

/** Backend order-line keys (entry_price/sl_price/tp_price) with the React
 *  mirror fallbacks — display choice of whichever the payload actually sent. */
function orderLinePrice(ol: OverlayOrderLines, backendKey: string, mirrorKey: string): number | null {
  const a = (ol as Record<string, unknown>)[backendKey];
  const b = (ol as Record<string, unknown>)[mirrorKey];
  for (const c of [a, b]) if (typeof c === "number" && Number.isFinite(c)) return c;
  return null;
}

/** Draw one frame. `scale` is the current (possibly mid-easing) price window;
 *  returns silently when there is nothing real to draw. */
export function paintChart(
  ctx: CanvasRenderingContext2D,
  size: { w: number; h: number },
  scale: { lo: number; hi: number },
  sc: PainterScene,
  pal: Palette,
  mono: string,
): void {
  const t = useI18n.getState().t;
  const { w, h } = size;
  if (w <= 0 || h <= 0 || sc.count <= 0 || sc.shown.length === 0) return;
  const { lo, hi } = scale;
  const plotW = w - AXIS_W - PAD_LEFT;
  const plotH = h - PAD_TOP - PAD_BOTTOM;
  // Slot model (TradingView-style pan/zoom): `count` slots span the plot and
  // `shown` fills the first shown.length of them — the tail is empty future
  // space. x() therefore takes a slot index RELATIVE to sc.left.
  const x = (i: number) => PAD_LEFT + (i * plotW) / sc.count;
  const bw = plotW / sc.count;
  const slotOf = (abs: number) => abs - sc.left;
  const y = (p: number) => PAD_TOP + ((hi - p) / (hi - lo)) * plotH;

  // backdrop: flat inset + subtle top gradient (legacy #090d16, modernized)
  ctx.fillStyle = pal.bg;
  ctx.fillRect(0, 0, w, h);
  const grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, "rgba(65,166,242,0.06)");
  grad.addColorStop(0.45, "rgba(65,166,242,0)");
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, w, h);

  // grid — nice price ticks + vertical bands (time axis is LTR always)
  ctx.strokeStyle = pal.grid;
  ctx.lineWidth = 1;
  ctx.fillStyle = pal.axisText;
  ctx.font = `9px ${mono}`;
  ctx.textAlign = "left";
  for (const tv of niceTicks(lo, hi, 5)) {
    const gy = Math.round(y(tv)) + 0.5;
    if (gy < PAD_TOP || gy > h - PAD_BOTTOM) continue;
    ctx.beginPath();
    ctx.moveTo(0, gy);
    ctx.lineTo(w - AXIS_W, gy);
    ctx.stroke();
    ctx.fillText(formatPrice(tv, sc.digits), w - AXIS_W + 6, gy + 3);
  }
  const vstep = Math.max(60, bw * 30);
  for (let gx = PAD_LEFT + plotW; gx > 0; gx -= vstep) {
    ctx.beginPath();
    ctx.moveTo(Math.round(gx) + 0.5, 0);
    ctx.lineTo(Math.round(gx) + 0.5, h - PAD_BOTTOM);
    ctx.stroke();
  }

  ctx.save();
  ctx.beginPath();
  ctx.rect(0, 0, w - AXIS_W, h - PAD_BOTTOM);
  ctx.clip();

  // SMC zones — ONLY from the backend overlay payload (verbatim prices)
  for (const z of sc.overlays?.rectangles ?? []) {
    let zx = PAD_LEFT;
    if (z.time) {
      const zi = sc.timeIndex.get(z.time) ?? sc.indexAtOrBefore(z.time);
      if (zi >= 0) zx = x(Math.max(0, slotOf(zi)));
    }
    const zy = y(z.price_high);
    const zh = Math.max(1, y(z.price_low) - zy);
    const st = zoneStyle(z.type);
    ctx.fillStyle = st.fill;
    const zw = Math.max(2, w - AXIS_W - zx);
    ctx.fillRect(zx, zy, zw, zh);
    if (st.stroke) {
      ctx.strokeStyle = st.stroke;
      ctx.lineWidth = 1;
      ctx.strokeRect(zx + 0.5, zy + 0.5, zw - 1, zh - 1);
    }
    ctx.fillStyle = "rgba(226,232,240,0.9)";
    ctx.font = `bold 9px ${mono}`;
    const ob = (z.type ?? "").toUpperCase().includes("ORDER_BLOCK");
    const label = ob && typeof z.ai_confidence === "number" ? `ob (${Math.round(z.ai_confidence * 100)}%)` : st.label;
    ctx.fillText(label, zx + 4, Math.min(Math.max(zy + 10, 10), h - PAD_BOTTOM - 4));
  }

  // BOS lines (backend-computed break levels)
  ctx.setLineDash([4, 2]);
  ctx.lineWidth = 1.5;
  ctx.strokeStyle = "rgba(235,161,63,0.85)";
  for (const l of sc.overlays?.bos_lines ?? []) {
    const ly = y(l.price);
    if (ly < 0 || ly > h - PAD_BOTTOM) continue;
    ctx.beginPath();
    ctx.moveTo(0, ly);
    ctx.lineTo(w - AXIS_W, ly);
    ctx.stroke();
    ctx.fillStyle = pal.amber;
    ctx.font = `bold 9px ${mono}`;
    ctx.fillText("BOS", 6, ly - 4);
  }
  // 50% equilibrium midlines
  ctx.strokeStyle = "rgba(148,163,184,0.75)";
  ctx.setLineDash([6, 4]);
  ctx.lineWidth = 1.2;
  for (const m of sc.overlays?.midlines ?? []) {
    const my = y(m.price);
    if (my < 0 || my > h - PAD_BOTTOM) continue;
    const from = m.time_start ? Math.max(0, x(Math.max(0, slotOf(sc.indexAtOrBefore(m.time_start))))) : 0;
    ctx.beginPath();
    ctx.moveTo(from, my);
    ctx.lineTo(w - AXIS_W, my);
    ctx.stroke();
    ctx.fillStyle = pal.dim;
    ctx.fillText(m.label ?? "50%", from + 3, my - 3);
  }
  ctx.setLineDash([]);

  // liquidity sweep markers (triangles at the swept extreme)
  for (const m of sc.overlays?.liq_markers ?? []) {
    const mi = m.time ? (sc.timeIndex.get(m.time) ?? -1) : -1;
    const mslot = slotOf(mi);
    if (mslot < 0 || mslot >= sc.count) continue;
    const mx = x(mslot) + bw / 2;
    const my = y(m.price);
    if (my < 0 || my > h - PAD_BOTTOM) continue;
    const up = (m.type ?? "").includes("BUY_SIDE");
    ctx.fillStyle = pal.amber;
    ctx.beginPath();
    ctx.moveTo(mx - 4, my + (up ? -3 : 3));
    ctx.lineTo(mx + 4, my + (up ? -3 : 3));
    ctx.lineTo(mx, my + (up ? 4 : -4));
    ctx.closePath();
    ctx.fill();
    ctx.font = `bold 8px ${mono}`;
    ctx.fillText("liq", mx + 6, up ? my - 4 : my + 11);
  }

  // candles — green/red per backend OHLC, forming bar dashed accent border
  const shownBars = sc.shown;
  for (let i = 0; i < shownBars.length; i++) {
    const c = shownBars[i];
    if (!c || c.open === null || c.close === null || c.high === null || c.low === null) continue; // honest gap
    const up = c.close >= c.open;
    const color = up ? pal.green : pal.red;
    const cx = x(i) + bw / 2;
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = Math.max(1, bw * 0.15);
    ctx.beginPath();
    ctx.moveTo(cx, y(c.high));
    ctx.lineTo(cx, y(c.low));
    ctx.stroke();
    const bodyTop = Math.min(y(c.open), y(c.close));
    const bodyH = Math.max(1, Math.abs(y(c.close) - y(c.open)));
    const cw = Math.max(1, Math.min(bw * 0.68, 11));
    ctx.fillRect(cx - cw / 2, bodyTop, cw, bodyH);
    if (c.is_complete === false) {
      ctx.strokeStyle = pal.accentStrong;
      ctx.lineWidth = 1;
      ctx.setLineDash([2, 2]);
      ctx.strokeRect(cx - cw / 2, bodyTop, cw, bodyH);
      ctx.setLineDash([]);
    }
  }

  // live quote line (snapshot bid — never drawn when null)
  if (typeof sc.liveBid === "number" && Number.isFinite(sc.liveBid)) {
    const qy = y(sc.liveBid);
    if (qy >= 0 && qy < h - PAD_BOTTOM) {
      ctx.strokeStyle = pal.accent;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(0, qy);
      ctx.lineTo(w - AXIS_W, qy);
      ctx.stroke();
    }
  }

  // order lines: ENTRY solid, SL red dashed, TP green dashed (legacy palette
  // + labels; every value passthrough from order_lines)
  const ol = sc.overlays?.order_lines;
  if (ol && ol.active !== false) {
    const rows: Array<[string, number | null, string, boolean]> = [
      ["ENTRY", orderLinePrice(ol, "entry_price", "entry"), pal.accentStrong, false],
      ["SL", orderLinePrice(ol, "sl_price", "stop_loss"), pal.red, true],
      ["TP", orderLinePrice(ol, "tp_price", "take_profit"), pal.green, true],
    ];
    let entryY: number | null = null;
    for (const [tag, val, color, dashed] of rows) {
      if (val === null) continue;
      const oy = y(val);
      if (tag === "ENTRY") entryY = oy;
      if (oy < 0 || oy > h - PAD_BOTTOM) continue;
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.6;
      ctx.setLineDash(dashed ? [5, 4] : []);
      ctx.beginPath();
      ctx.moveTo(0, oy);
      ctx.lineTo(w - AXIS_W, oy);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = color;
      ctx.font = `bold 9px ${mono}`;
      ctx.fillText(`${tag}: ${val.toFixed(sc.digits)}`, 8, oy - 4);
    }
    // legacy order-evaluation bubble — only fields the backend actually sent
    const rr = typeof ol.risk_reward_ratio === "number" ? ol.risk_reward_ratio : null;
    const risk = typeof ol.risk_usd === "number" ? ol.risk_usd : null;
    const profit = typeof ol.profit_usd === "number" ? ol.profit_usd : null;
    const zone = typeof ol.zone_score === "number" ? ol.zone_score : null;
    const lines = [
      typeof ol.direction === "string" ? t("dash.chart.order_evaluated", "Order Evaluated: {d}", { d: ol.direction }) : null,
      rr !== null || risk !== null
        ? t("dash.chart.order_rr", "Target RR: {rr} | Dollar Risk: {risk}", { rr: rr !== null ? `1:${rr.toFixed(2)}` : "—", risk: risk !== null ? `$${risk.toFixed(2)}` : "—" })
        : null,
      profit !== null || zone !== null
        ? t("dash.chart.order_profit", "Potential Profit: {p} | Zone Score: {z}", { p: profit !== null ? `$${profit.toFixed(2)}` : "—", z: zone !== null ? `${zone.toFixed(0)}%` : "—" })
        : null,
    ].filter((s): s is string => s !== null);
    if (lines.length > 0 && entryY !== null) {
      const tw = 268;
      const th = 14 + lines.length * 12;
      const tx = Math.max(10, w - AXIS_W - tw - 12);
      const ty = Math.min(Math.max(entryY + 15, 30), Math.max(0, h - PAD_BOTTOM - th - 4));
      ctx.fillStyle = "rgba(13,19,27,0.94)";
      ctx.strokeStyle = pal.borderStrong;
      ctx.lineWidth = 1;
      ctx.beginPath();
      if (typeof ctx.roundRect === "function") ctx.roundRect(tx, ty, tw, th, 6);
      else ctx.rect(tx, ty, tw, th);
      ctx.fill();
      ctx.stroke();
      ctx.textAlign = "left";
      lines.forEach((ln, i) => {
        ctx.fillStyle = i === 0 ? pal.text : pal.dim;
        ctx.font = `${i === 0 ? "bold " : ""}9px ${mono}`;
        ctx.fillText(ln, tx + 8, ty + 14 + i * 12);
      });
    }
  }

  // replay KNOWN/UNKNOWN boundary (Web/replay_panel.js drawKnownBoundary port)
  if (sc.cursorIso) {
    const ci = sc.indexAtOrBefore(sc.cursorIso);
    if (ci >= 0) {
      const cx2 = x(slotOf(ci)) + bw / 2;
      ctx.fillStyle = "rgba(2,6,23,0.62)";
      ctx.fillRect(cx2, 0, Math.max(0, w - AXIS_W - cx2), h - PAD_BOTTOM);
      ctx.strokeStyle = pal.accentStrong;
      ctx.lineWidth = 1.4;
      ctx.setLineDash([4, 3]);
      ctx.beginPath();
      ctx.moveTo(cx2, 0);
      ctx.lineTo(cx2, h - PAD_BOTTOM);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = pal.accentStrong;
      ctx.font = `bold 9px ${mono}`;
      ctx.fillText(t("dash.chart.cursor_label", "REPLAY CURSOR (KNOWN)"), Math.min(cx2 + 4, w - AXIS_W - 150), 12);
      ctx.fillText(t("dash.chart.future_label", "FUTURE = UNKNOWN"), Math.min(cx2 + 4, w - AXIS_W - 150), 24);
    }
  }

  // data/future boundary — empty slots right of the last real bar (scroll-
  // into-future space) get an explicit edge so the emptiness reads as
  // "no data yet", never "the feed stopped".
  if (sc.shown.length < sc.count) {
    const edgeX = x(sc.shown.length);
    if (edgeX < w - AXIS_W - 46) {
      ctx.strokeStyle = "rgba(148,163,184,0.35)";
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(edgeX, 0);
      ctx.lineTo(edgeX, h - PAD_BOTTOM);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = pal.axisText;
      ctx.font = `9px ${mono}`;
      ctx.fillText(t("dash.chart.future_edge", "future"), edgeX + 5, h - PAD_BOTTOM - 6);
    }
  }

  // crosshair guides (legacy dashed slate) — vertical snaps to the hovered
  // bar center, horizontal follows the pointer Y like app.js crosshairX/Y
  if (sc.hoverIdx >= 0 && sc.hoverIdx < sc.count) {
    const chx = x(sc.hoverIdx) + bw / 2;
    ctx.strokeStyle = "rgba(148,163,184,0.35)";
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 3]);
    ctx.beginPath();
    ctx.moveTo(chx, 0);
    ctx.lineTo(chx, h - PAD_BOTTOM);
    ctx.stroke();
    if (typeof sc.hoverY === "number" && sc.hoverY >= 0 && sc.hoverY < h - PAD_BOTTOM) {
      ctx.beginPath();
      ctx.moveTo(0, sc.hoverY);
      ctx.lineTo(w - AXIS_W, sc.hoverY);
      ctx.stroke();
    }
    ctx.setLineDash([]);
  }
  ctx.restore();

  // price axis frame + live quote tag + time axis (4 labels)
  ctx.strokeStyle = pal.grid;
  ctx.beginPath();
  ctx.moveTo(w - AXIS_W + 0.5, 0);
  ctx.lineTo(w - AXIS_W + 0.5, h - PAD_BOTTOM);
  ctx.stroke();
  if (typeof sc.liveBid === "number" && Number.isFinite(sc.liveBid)) {
    const qy = y(sc.liveBid);
    if (qy >= 0 && qy < h - PAD_BOTTOM) {
      ctx.fillStyle = pal.accent;
      ctx.fillRect(w - AXIS_W + 2, qy - 7, AXIS_W - 4, 14);
      ctx.fillStyle = "#04121d";
      ctx.font = `bold 9px ${mono}`;
      ctx.textAlign = "center";
      ctx.fillText(formatPrice(sc.liveBid, sc.digits), w - AXIS_W / 2 + 1, qy + 3);
      ctx.textAlign = "left";
    }
  }
  ctx.fillStyle = pal.axisText;
  ctx.font = `9px ${mono}`;
  for (let i = 0; i < 4; i++) {
    const idx = Math.floor(((shownBars.length - 1) * i) / 3);
    const b = shownBars[idx];
    if (!b) continue;
    const lx = Math.min(Math.max(PAD_LEFT, x(idx)), w - AXIS_W - 58);
    ctx.fillText(b.time.replace("T", " ").slice(5, 16), lx, h - 8);
  }
}
