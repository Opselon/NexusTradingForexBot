import type { ChartKind, Palette, PainterScene, PlotGeom } from "../../chartPainter";

/** Wave-2 LANE D seam: non-candle series presentation (line / area / hollow)
 *  over the SAME slot geometry — pure presentation of backend OHLC, no math
 *  beyond pixel mapping (lane-09 safe). Called instead of the candle loop
 *  whenever scene.kind !== "candles" (inside the plot clip, before the quote
 *  line). Null closes break segments — honest gaps, never interpolated. */

interface Pt {
  x: number;
  y: number;
}

/** Hex / rgb(a) color string → the same color at a fixed alpha (theme.css
 *  tokens are hex; rgb(a) passes through; anything else is returned as-is). */
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

/** Close-price points per slot (window-relative: geom.x takes a slot index
 *  relative to scene.left). A null close starts a NEW segment on the next
 *  bar — the polyline is never drawn across missing data. */
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

/** Hollow candles: wicks for every complete row (green up / red down), up
 *  bodies stroke-only, down bodies filled; the forming bar keeps the dashed
 *  accent border — same geometry and sizing as the candle loop. */
function paintHollow(ctx: CanvasRenderingContext2D, sc: PainterScene, geom: PlotGeom, pal: Palette): void {
  for (let i = 0; i < sc.shown.length; i++) {
    const b = sc.shown[i];
    if (!b || b.open === null || b.close === null || b.high === null || b.low === null) continue; // honest gap
    const up = b.close >= b.open;
    const color = up ? pal.green : pal.red;
    const cx = geom.x(i) + geom.bw / 2;
    // wick (candle-loop style: same lineWidth, direction color)
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

export function paintSeries(
  kind: ChartKind,
  ctx: CanvasRenderingContext2D,
  sc: PainterScene,
  geom: PlotGeom,
  pal: Palette,
  _mono: string,
): void {
  if (sc.shown.length === 0) return;
  ctx.save();
  if (kind === "hollow") {
    paintHollow(ctx, sc, geom, pal);
  } else {
    const segs = closeSegments(sc, geom);
    if (kind === "area") {
      // Fill: polyline closed down to the bottom of the price band (volY0)
      // and back along it to the first point — one filled subpath per segment.
      // Gradient spans the price band in canvas coords (volY0 - priceH == the
      // plot's top edge), accent 0.30 alpha at the top fading to 0.02.
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
    // Stroke the close line on top (line and area) — round joins, gaps stay
    // open because each segment is its own subpath inside one path.
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
