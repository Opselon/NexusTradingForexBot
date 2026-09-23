import { AXIS_W, PAD_BOTTOM, PAD_TOP, type Palette, type PainterScene, type PlotGeom } from "../chartPainter";
import { formatPrice } from "@/lib/format";

/** Wave-2 LANE C seam: TradingView-style crosshair axis tags — price tag on
 *  the right axis at the cursor Y, time tag on the bottom axis at the hovered
 *  bar (drawn AFTER the axes, outside the plot clip). Filled accent chip with
 *  dark text, matching the painter's live-bid tag height (14px). */
export function paintAxisTags(
  ctx: CanvasRenderingContext2D,
  sc: PainterScene,
  geom: PlotGeom,
  pal: Palette,
  mono: string,
): void {
  // Nothing hovered → draw nothing (the frame keeps its live-bid tag as-is).
  const idx = sc.hoverIdx;
  if (idx < 0) return;

  ctx.save();
  ctx.font = `9px ${mono}`;
  ctx.textAlign = "center";

  // TIME TAG — bottom axis, snapped to the hovered bar (future space has no
  // bar time, so only a real shown slot gets a tag).
  const bar = idx < sc.shown.length ? sc.shown[idx] : undefined;
  if (bar) {
    const label = bar.time.replace("T", " ").slice(5, 16);
    const tw = Math.ceil(ctx.measureText(label).width) + 8;
    const ty = geom.h - PAD_BOTTOM + 1;
    const cx = geom.x(idx) + geom.bw / 2;
    // Keep the chip on the bottom strip: [2, plot-right - 60].
    const maxTx = Math.max(2, geom.w - AXIS_W - 60);
    const tx = Math.min(Math.max(cx - tw / 2, 2), maxTx);
    ctx.fillStyle = pal.accentStrong;
    tagRect(ctx, tx, ty, tw, 14);
    ctx.fill();
    ctx.fillStyle = "#04121d";
    ctx.fillText(label, tx + tw / 2, ty + 10);
  }

  // PRICE TAG — right axis at the cursor Y (mirrors the live-bid tag box).
  const hoverY = sc.hoverY;
  if (
    typeof hoverY === "number" &&
    Number.isFinite(hoverY) &&
    hoverY >= PAD_TOP &&
    hoverY <= geom.h - PAD_BOTTOM
  ) {
    const p = priceAtY(sc, geom, hoverY);
    if (p !== null) {
      const tx = geom.w - AXIS_W + 2;
      const ty = hoverY - 7;
      const tw = AXIS_W - 4;
      ctx.fillStyle = pal.accentStrong;
      tagRect(ctx, tx, ty, tw, 14);
      ctx.fill();
      ctx.fillStyle = "#04121d";
      ctx.fillText(formatPrice(p, sc.digits), tx + tw / 2, ty + 10);
    }
  }

  ctx.restore();
}

/** Filled rounded-chip path (roundRect with a rect fallback, like paintChart). */
function tagRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number): void {
  ctx.beginPath();
  if (typeof ctx.roundRect === "function") ctx.roundRect(x, y, w, h, 3);
  else ctx.rect(x, y, w, h);
}

/** Invert geom.y() numerically — the price scale (hi/lo) is NOT handed to the
 *  seam, so bracket the data range over the shown bars and bisect on the
 *  monotonic decreasing y(p). Returns null for a degenerate bracket. */
function priceAtY(sc: PainterScene, geom: PlotGeom, hoverY: number): number | null {
  let lo = Infinity;
  let hi = -Infinity;
  for (const bar of sc.shown) {
    if (typeof bar.low === "number" && Number.isFinite(bar.low)) lo = Math.min(lo, bar.low);
    if (typeof bar.high === "number" && Number.isFinite(bar.high)) hi = Math.max(hi, bar.high);
  }
  if (!Number.isFinite(lo) || !Number.isFinite(hi) || hi <= lo) return null;
  // Clamp the target into the drawn band so a cursor over the volume band
  // resolves to the nearest data extreme instead of inverting off-scale.
  const target = Math.min(Math.max(hoverY, geom.y(hi)), geom.y(lo));
  let a = lo;
  let b = hi;
  for (let i = 0; i < 24; i++) {
    const mid = (a + b) / 2;
    // y decreases as p grows: too far down → price too low → raise the floor.
    if (geom.y(mid) > target) a = mid;
    else b = mid;
  }
  return (a + b) / 2;
}
