import type { Palette, PainterScene, PlotGeom } from "../chartPainter";

/**
 * Wave-3 LANE A seam: cache the STATIC layers of the chart.
 *
 * Everything that depends only on (bars geometry + scale + overlays + kind) is
 * static per frame unless the view/price window moved: backdrop, grid, zones,
 * BOS, midlines, liq markers, candles/series, volume. The hover crosshair,
 * live quote line + tag, replay cursor dim, order lines and the axis tags are
 * DYNAMIC and stay in paintChart.
 *
 * Correctness contract (a stale static layer is a lie to the operator):
 * invalidate on ANY of bars identity, slot geometry, price scale, digits,
 * kind, overlay contents, canvas size — plus palette/font (a theme switch
 * must not leave yesterday's colours on screen). The cache key function is
 * exported so a test can assert it changes exactly when it must.
 *
 * Module discipline: this file has ZERO non-`import type` imports so the
 * node --test suite (tests/js/w3_paint_cache.test.mjs) can exercise the REAL
 * production code directly (repo pattern: vizMath/pro_dependency suites).
 * The stage painters below therefore MIRROR chartPainter.ts / volumeOverlay.ts
 * / renderers/seriesRender.ts — the parity tests in that suite pin volume,
 * series and grid output byte-for-byte against the originals and fail if
 * those originals drift. paintStaticLayers blits the layer inside paintChart's
 * plot clip, so the layer is fully opaque (backdrop + grid included): one
 * source-over drawImage reproduces the exact same pixels paintChart already
 * painted underneath — no double-composited translucent zones.
 */

/** Layer cache handle. `canvas` is created lazily by paintStaticLayers (the
 *  caller only owns the ref — see the PriceChart cacheRef hunk). */
export interface LayerCache {
  key: string;
  canvas?: HTMLCanvasElement;
}

/** Geometry constants mirrored from chartPainter.ts (exported there as
 *  PAD_TOP/PAD_BOTTOM/AXIS_W/PAD_LEFT — unreachable here without a value
 *  import, which would break the node test's direct module load). */
const PAD_TOP = 10;
const PAD_BOTTOM = 22;
const AXIS_W = 60;
const PAD_LEFT = 6;

// ---------------------------------------------------------------------------
// Perf counters (reported into chart/chartPerf.ts — the badge reads them).
// ---------------------------------------------------------------------------

export interface PaintFrameCounters {
  /** Frames handed to the seam (staticRepaints + cachedFrames). */
  frames: number;
  /** Cold frames: the static layer was repainted. */
  staticRepaints: number;
  /** Warm frames: the cached layer was reused (blit only). */
  cachedFrames: number;
}

const counters: PaintFrameCounters = { frames: 0, staticRepaints: 0, cachedFrames: 0 };

/** Snapshot for the perf hook (copy — callers must not mutate the store). */
export function readPaintCounters(): PaintFrameCounters {
  return { ...counters };
}

function noteStaticFrame(repaint: boolean): void {
  counters.frames += 1;
  if (repaint) counters.staticRepaints += 1;
  else counters.cachedFrames += 1;
}

// ---------------------------------------------------------------------------
// Cache key — EVERY invalidator the contract lists, nothing less.
// ---------------------------------------------------------------------------

/** FNV-1a over a string (bar identity digest — detects an in-place OHLC edit
 *  even when length/left/count/digits are unchanged). */
function fnv1a(h: number, s: string): number {
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

function numSig(v: unknown): string {
  return typeof v === "number" && Number.isFinite(v) ? String(v) : "n";
}

/** Digest of the visible bars: length + every (time, close, open, high, low,
 *  completeness) — bars identity, robust to same-length in-place updates. */
function barsDigest(sc: PainterScene): string {
  let h = 2166136261;
  for (const b of sc.shown) {
    h = fnv1a(h, b.time);
    h = fnv1a(h, numSig(b.close));
    h = fnv1a(h, numSig(b.open));
    h = fnv1a(h, numSig(b.high));
    h = fnv1a(h, numSig(b.low));
    h = fnv1a(h, b.is_complete === false ? "f" : "c");
  }
  return sc.shown.length.toString(36) + "." + h.toString(36);
}

/** Digest of overlay CONTENT (not just counts — a zone that moved without a
 *  count change must still invalidate). Few items per payload: full walk. */
function overlaysDigest(ov: PainterScene["overlays"]): string {
  if (!ov) return "-";
  let h = 2166136261;
  for (const z of ov.rectangles ?? []) h = fnv1a(fnv1a(fnv1a(fnv1a(fnv1a(h, z.type ?? ""), z.time ?? ""), numSig(z.price_low)), numSig(z.price_high)), String(z.ai_confidence ?? ""));
  for (const l of ov.bos_lines ?? []) h = fnv1a(fnv1a(fnv1a(h, l.time ?? ""), numSig(l.price)), l.label ?? "");
  for (const m of ov.midlines ?? []) h = fnv1a(fnv1a(fnv1a(fnv1a(h, m.time ?? ""), numSig(m.price)), m.time_start ?? ""), m.label ?? "");
  for (const m of ov.liq_markers ?? []) h = fnv1a(fnv1a(fnv1a(h, m.time ?? ""), numSig(m.price)), m.type ?? "");
  const ol = ov.order_lines;
  h = fnv1a(
    h,
    `o${numSig(ol?.entry_price ?? null)}${numSig(ol?.entry ?? null)}${numSig(ol?.sl_price ?? null)}${numSig(ol?.stop_loss ?? null)}${numSig(ol?.tp_price ?? null)}${numSig(ol?.take_profit ?? null)}`,
  );
  return h.toString(36);
}

/**
 * The contract key: bars identity | slot geometry | price scale | digits |
 * kind | overlay contents | canvas size.
 *
 * The price scale reaches the key through geom.y() probes — y(0) and y(1)
 * together pin the affine window (lo/hi/priceH), so an eased scale glide
 * invalidates on EVERY frame it moves (the layer must move with it) and
 * settles to warm once the glide lands.
 */
export function cacheKeyFor(sc: PainterScene, geom: PlotGeom): string {
  return [
    barsDigest(sc),
    sc.left,
    sc.count,
    sc.digits,
    sc.kind ?? "candles",
    geom.w,
    geom.h,
    geom.priceH,
    geom.volH,
    geom.bw,
    geom.plotW,
    geom.y(0),
    geom.y(1),
    overlaysDigest(sc.overlays),
  ].join("|");
}

// ---------------------------------------------------------------------------
// Static stage painters (mirror of the paintChart static half — see the
// module header for the sync discipline and the parity tests that pin it).
// ---------------------------------------------------------------------------

/** Same signature/semantics as @/lib/format formatPrice (parity-tested). */
function formatPrice(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toFixed(digits);
}

/** Byte-for-byte mirror of @/components/viz/geometry niceTicks (parity-tested
 *  in w3_paint_cache.test.mjs against the real module). */
export function staticPriceTicks(min: number, max: number, count = 4): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max) || max <= min) return [min];
  const span = max - min;
  const raw = span / Math.max(1, count);
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  const stepUnit = norm >= 5 ? 10 : norm >= 2 ? 5 : norm >= 1 ? 2 : 1;
  const step = stepUnit * mag;
  const start = Math.ceil(min / step) * step;
  const out: number[] = [];
  for (let v = start; v <= max + step * 0.001 && out.length < 32; v += step) out.push(Number(v.toFixed(10)));
  return out;
}

/** Recover the (possibly mid-easing) price window from the y() probes — the
 *  seam does not receive `scale`, and y(p) = PAD_TOP + (hi-p)/(hi-lo)*priceH. */
function scaleFromGeom(geom: PlotGeom): { lo: number; hi: number } {
  const y0 = geom.y(0);
  const y1 = geom.y(1);
  const diff = y0 - y1;
  if (!Number.isFinite(diff) || diff === 0) return { lo: 0, hi: 1 };
  const span = geom.priceH / diff;
  const hi = ((y0 - PAD_TOP) / geom.priceH) * span;
  return { lo: hi - span, hi };
}

/** Backdrop + grid + price labels — mirrors paintChart's pre-clip statics
 *  (an OPAQUE layer is required: it must cover the identical inline statics
 *  paintChart paints beneath the blit without double-compositing zones). */
export function paintStaticBackdropGrid(
  ctx: CanvasRenderingContext2D,
  sc: PainterScene,
  geom: PlotGeom,
  pal: Palette,
  mono: string,
): void {
  const { w, h, priceH, plotW, y } = geom;

  // backdrop: flat inset + subtle top gradient (legacy #090d16, modernized)
  ctx.fillStyle = pal.bg;
  ctx.fillRect(0, 0, w, h);
  const grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, "rgba(65,166,242,0.06)");
  grad.addColorStop(0.45, "rgba(65,166,242,0)");
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, w, h);

  // grid — nice price ticks + vertical bands (time axis is LTR always)
  const { lo, hi } = scaleFromGeom(geom);
  ctx.strokeStyle = pal.grid;
  ctx.lineWidth = 1;
  ctx.fillStyle = pal.axisText;
  ctx.font = `9px ${mono}`;
  ctx.textAlign = "left";
  for (const tv of staticPriceTicks(lo, hi, 5)) {
    const gy = Math.round(y(tv)) + 0.5;
    if (gy < PAD_TOP || gy > PAD_TOP + priceH) continue;
    ctx.beginPath();
    ctx.moveTo(0, gy);
    ctx.lineTo(w - AXIS_W, gy);
    ctx.stroke();
    ctx.fillText(formatPrice(tv, sc.digits), w - AXIS_W + 6, gy + 3);
  }
  const vstep = Math.max(60, geom.bw * 30);
  for (let gx = PAD_LEFT + plotW; gx > 0; gx -= vstep) {
    ctx.beginPath();
    ctx.moveTo(Math.round(gx) + 0.5, 0);
    ctx.lineTo(Math.round(gx) + 0.5, h - PAD_BOTTOM);
    ctx.stroke();
  }
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

/** SMC overlays: zones, BOS, midlines, liq markers — mirrors paintChart's
 *  clipped static overlay block (backend payload passthrough, lane-09 safe). */
export function paintStaticOverlays(
  ctx: CanvasRenderingContext2D,
  sc: PainterScene,
  geom: PlotGeom,
  pal: Palette,
  mono: string,
): void {
  const { w, h, x, bw, y } = geom;
  const slotOf = (abs: number) => abs - sc.left;

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
}

/** Candles (kind === "candles") — mirror of paintChart's candle loop,
 *  including the honest-gap skip and the forming-bar dashed accent border. */
export function paintStaticCandles(
  ctx: CanvasRenderingContext2D,
  sc: PainterScene,
  geom: PlotGeom,
  pal: Palette,
): void {
  const { x, bw, y } = geom;
  for (let i = 0; i < sc.shown.length; i++) {
    const c = sc.shown[i];
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
}

/** Hex / rgb(a) color string → the same color at a fixed alpha (mirror of
 *  renderers/seriesRender withAlpha — parity-tested). */
function withAlpha(color: string, alpha: number): string {
  const c = color.trim();
  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(c)?.[1];
  if (hex) {
    const full = hex.length === 3 ? hex.split("").map((ch) => ch + ch).join("") : hex;
    const n = parseInt(full, 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
  }
  const rgb = /^rgba?\(([^)]+)\)$/i.exec(c)?.[1];
  if (rgb) {
    const parts = rgb.split(",").map((s) => s.trim());
    if (parts.length >= 3) return `rgba(${parts[0]}, ${parts[1]}, ${parts[2]}, ${alpha})`;
  }
  return c;
}

interface Pt {
  x: number;
  y: number;
}

/** Close-price points per slot (mirror of seriesRender closeSegments). */
function closeSegments(sc: PainterScene, geom: PlotGeom): Pt[][] {
  const segs: Pt[][] = [];
  let cur: Pt[] | null = null;
  for (let i = 0; i < sc.shown.length; i++) {
    const close = sc.shown[i]?.close ?? null;
    if (close === null) {
      cur = null; // honest gap: next valid point starts a fresh subpath
      continue;
    }
    const p: Pt = { x: geom.x(i) + geom.bw / 2, y: geom.y(close) };
    if (cur) cur.push(p);
    else {
      cur = [p];
      segs.push(cur);
    }
  }
  return segs;
}

/** Hollow candles mirror of seriesRender paintHollow. */
function paintHollow(ctx: CanvasRenderingContext2D, sc: PainterScene, geom: PlotGeom, pal: Palette): void {
  for (let i = 0; i < sc.shown.length; i++) {
    const b = sc.shown[i];
    if (!b || b.open === null || b.close === null || b.high === null || b.low === null) continue; // honest gap
    const up = b.close >= b.open;
    const color = up ? pal.green : pal.red;
    const cx = geom.x(i) + geom.bw / 2;
    ctx.strokeStyle = color;
    ctx.lineWidth = Math.max(1, geom.bw * 0.15);
    ctx.beginPath();
    ctx.moveTo(cx, geom.y(b.high));
    ctx.lineTo(cx, geom.y(b.low));
    ctx.stroke();
    const bodyTop = Math.min(geom.y(b.open), geom.y(b.close));
    const bodyH = Math.max(1, Math.abs(geom.y(b.close) - geom.y(b.open)));
    const cw = Math.max(1, Math.min(geom.bw * 0.68, 11));
    ctx.lineWidth = 1;
    if (up) {
      ctx.strokeStyle = color;
      ctx.strokeRect(cx - cw / 2, bodyTop, cw, bodyH); // hollow: stroke-only
    } else {
      ctx.fillStyle = color;
      ctx.fillRect(cx - cw / 2, bodyTop, cw, bodyH); // down: filled
    }
    if (b.is_complete === false) {
      ctx.strokeStyle = pal.accentStrong;
      ctx.setLineDash([2, 2]);
      ctx.strokeRect(cx - cw / 2, bodyTop, cw, bodyH);
      ctx.setLineDash([]);
    }
  }
}

/** Non-candle presentation (line / area / hollow) — mirror of
 *  renderers/seriesRender paintSeries (parity-tested). */
export function paintStaticSeries(
  kind: string,
  ctx: CanvasRenderingContext2D,
  sc: PainterScene,
  geom: PlotGeom,
  pal: Palette,
): void {
  if (sc.shown.length === 0) return;
  ctx.save();
  if (kind === "hollow") {
    paintHollow(ctx, sc, geom, pal);
  } else {
    const segs = closeSegments(sc, geom);
    if (kind === "area") {
      const topY = geom.volY0 - geom.priceH;
      const grad = ctx.createLinearGradient(0, topY, 0, geom.volY0);
      grad.addColorStop(0, withAlpha(pal.accent, 0.3));
      grad.addColorStop(1, withAlpha(pal.accent, 0.02));
      ctx.fillStyle = grad;
      for (const seg of segs) {
        const first = seg[0];
        const last = seg[seg.length - 1];
        if (!first || !last || seg.length < 2) continue;
        ctx.beginPath();
        ctx.moveTo(first.x, first.y);
        for (let k = 1; k < seg.length; k++) {
          const p = seg[k];
          if (p) ctx.lineTo(p.x, p.y);
        }
        ctx.lineTo(last.x, geom.volY0);
        ctx.lineTo(first.x, geom.volY0);
        ctx.closePath();
        ctx.fill();
      }
    }
    ctx.strokeStyle = pal.accent;
    ctx.lineWidth = 1.6;
    ctx.lineJoin = "round";
    ctx.beginPath();
    for (const seg of segs) {
      seg.forEach((p, k) => {
        if (k === 0) ctx.moveTo(p.x, p.y);
        else ctx.lineTo(p.x, p.y);
      });
    }
    ctx.stroke();
  }
  ctx.restore();
}

/** Tick-volume histogram in the reserved band — mirror of chart/volumeOverlay
 *  paintVolume (parity-tested). Data passthrough only: no indicators. */
export function paintStaticVolume(
  ctx: CanvasRenderingContext2D,
  sc: PainterScene,
  geom: PlotGeom,
  pal: Palette,
  mono: string,
): void {
  const { volY0, volH, bw } = geom;
  if (!(volH > 0) || sc.shown.length === 0) return;

  // Pass 1 — read tick_volume (payload fallback: volume). Honest gaps.
  const vols: Array<number | null> = new Array(sc.shown.length).fill(null);
  let max = 0;
  for (let i = 0; i < sc.shown.length; i++) {
    const b = sc.shown[i];
    if (!b) continue;
    const raw = b.tick_volume ?? b.volume;
    if (typeof raw !== "number" || !Number.isFinite(raw) || raw < 0) continue;
    vols[i] = raw;
    if (raw > max) max = raw;
  }
  if (max <= 0) return;

  const baseY = volY0 + volH;
  const usable = Math.max(1, volH - 6);
  const barW = Math.max(1, bw * 0.6);
  const tall = volH >= 70;
  const rightX = geom.x(0) + geom.plotW - 4;

  if (tall) {
    ctx.save();
    ctx.globalAlpha = 0.9;
    ctx.strokeStyle = pal.grid;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, Math.round(volY0) + 0.5);
    ctx.lineTo(rightX, Math.round(volY0) + 0.5);
    ctx.stroke();
    ctx.restore();
  }

  ctx.save();
  ctx.globalAlpha = 0.35;
  for (let i = 0; i < vols.length; i++) {
    const v = vols[i];
    if (v == null) continue;
    const barH = (v / max) * usable;
    if (!(barH > 0)) continue;
    const b = sc.shown[i];
    let color = pal.dim;
    if (
      b &&
      typeof b.open === "number" &&
      Number.isFinite(b.open) &&
      typeof b.close === "number" &&
      Number.isFinite(b.close)
    ) {
      color = b.close >= b.open ? pal.green : pal.red;
    }
    ctx.fillStyle = color;
    const cx = geom.x(i) + bw / 2;
    ctx.fillRect(cx - barW / 2, baseY - barH, barW, barH);
  }
  ctx.restore();

  if (!tall) return;
  ctx.save();
  ctx.globalAlpha = 1;
  ctx.fillStyle = pal.dim;
  ctx.font = `8px ${mono}`;
  ctx.textAlign = "left";
  ctx.fillText("volume", 6, volY0 + 10);
  ctx.textAlign = "right";
  ctx.fillText(String(max), rightX, volY0 + 10);
  ctx.restore();
}

/** Compose the full static layer: backdrop+grid, then (clipped) overlays,
 *  candles-or-series, volume — the exact static half of one paintChart frame,
 *  in the exact same order. */
export function paintStaticStages(
  ctx: CanvasRenderingContext2D,
  sc: PainterScene,
  geom: PlotGeom,
  pal: Palette,
  mono: string,
): void {
  paintStaticBackdropGrid(ctx, sc, geom, pal, mono);
  ctx.save();
  ctx.beginPath();
  ctx.rect(0, 0, geom.w - AXIS_W, geom.h - PAD_BOTTOM);
  ctx.clip();
  paintStaticOverlays(ctx, sc, geom, pal, mono);
  const kind = sc.kind ?? "candles";
  if (kind === "candles") paintStaticCandles(ctx, sc, geom, pal);
  else paintStaticSeries(kind, ctx, sc, geom, pal);
  paintStaticVolume(ctx, sc, geom, pal, mono);
  ctx.restore();
}

// ---------------------------------------------------------------------------
// The seam entry point.
// ---------------------------------------------------------------------------

/** Backing-store scale: prefer the caller's live transform (paintChart runs
 *  under setTransform(dpr,…)), fall back to the display's devicePixelRatio. */
function dprOf(ctx: CanvasRenderingContext2D): number {
  try {
    const t = typeof ctx.getTransform === "function" ? ctx.getTransform() : null;
    if (t && Number.isFinite(t.a) && t.a > 0) return t.a;
  } catch {
    /* fall through */
  }
  const w = (globalThis as { window?: { devicePixelRatio?: number } }).window;
  const d = w?.devicePixelRatio;
  return typeof d === "number" && d > 0 ? Math.min(2, d) : 1;
}

function createLayerCanvas(): HTMLCanvasElement | undefined {
  const g = (globalThis as { document?: { createElement?: (t: "canvas") => HTMLCanvasElement } }).document;
  return g?.createElement ? g.createElement("canvas") : undefined;
}

/**
 * Paint the static layers into `cache.canvas` and return true when a repaint
 * happened, false when the cached frame was still valid (caller counts draw
 * calls).
 *
 * Flow: compose the key (cacheKeyFor + palette + font — the layer must never
 * show yesterday's theme), size the backing store to the caller's dpr, and on
 * a key/size change repaint the full static stage set into the offscreen
 * layer. Every call then blits the layer into paintChart's active plot clip
 * (opaque → pixel-identical cover of the inline statics painted above) so the
 * dynamic stages that follow — quote line, order lines, replay dim, crosshair,
 * axis tags — draw on a valid static frame.
 */
export function paintStaticLayers(
  ctx: CanvasRenderingContext2D,
  sc: PainterScene,
  geom: PlotGeom,
  pal: Palette,
  mono: string,
  cache: LayerCache,
): boolean {
  const dpr = dprOf(ctx);
  const bwPix = Math.max(1, Math.round(geom.w * dpr));
  const bhPix = Math.max(1, Math.round(geom.h * dpr));
  // Palette + font ride along: a theme/font switch must repaint the layer
  // (they are not part of cacheKeyFor's (sc, geom) contract signature).
  const key = `${cacheKeyFor(sc, geom)}#${Object.values(pal).join(",")}#${mono}`;

  if (!cache.canvas) cache.canvas = createLayerCanvas();
  const cv = cache.canvas;
  if (!cv) return true; // no layer available — report a repaint, skip the blit

  const sizeChanged = cv.width !== bwPix || cv.height !== bhPix;
  if (sizeChanged) {
    cv.width = bwPix; // assigning width/height RESIZES (and clears) the layer
    cv.height = bhPix;
  }

  const repaint = sizeChanged || key !== cache.key;
  if (repaint) {
    const off = cv.getContext("2d");
    if (!off) return true; // cannot paint a layer — retry next frame, no blit
    off.setTransform(dpr, 0, 0, dpr, 0, 0);
    paintStaticStages(off, sc, geom, pal, mono);
    cache.key = key;
  }

  // Deliver the layer: inside paintChart's plot clip, opaque source-over =
  // an exact replacement of the pixels painted underneath (no alpha stacking).
  ctx.drawImage(cv, 0, 0, geom.w, geom.h);
  noteStaticFrame(repaint);
  return repaint;
}
