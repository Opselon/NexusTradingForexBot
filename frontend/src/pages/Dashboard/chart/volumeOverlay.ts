import type { Palette, PainterScene, PlotGeom } from "../chartPainter";

/** Wave-2 LANE A seam: tick-volume histogram in the reserved band
 *  geom.volY0..volY0+volH (bottom of the plot, above the time axis).
 *  Data passthrough only — bars carry tick_volume (lane-09 safe):
 *  no indicators, no signals, the only math here is pixel scaling. */
export function paintVolume(
  ctx: CanvasRenderingContext2D,
  sc: PainterScene,
  geom: PlotGeom,
  pal: Palette,
  mono: string,
): void {
  const { volY0, volH, bw } = geom;
  if (!(volH > 0) || sc.shown.length === 0) return;

  // Pass 1 — read tick_volume (payload fallback: volume). Honest gaps: a
  // missing/non-numeric value leaves its slot EMPTY (never drawn as zero),
  // and the peak scale only ever sees real values.
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
  if (max <= 0) return; // nothing real to scale against → draw nothing

  const baseY = volY0 + volH;           // bars grow up from the band floor
  const usable = Math.max(1, volH - 6); // 6px top headroom for the peak label
  const barW = Math.max(1, bw * 0.6);
  const tall = volH >= 70;              // labels only when the band allows it
  const rightX = geom.x(0) + geom.plotW - 4; // right edge of the plot clip

  // Faint band centerline (price/volume seam divider) — tall band only.
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

  // Pass 2 — one translucent rect per shown bar, candle-direction colored
  // (close >= open → green, else red; unknown OHLC → neutral dim).
  ctx.save();
  ctx.globalAlpha = 0.35;
  for (let i = 0; i < vols.length; i++) {
    const v = vols[i];
    if (v == null) continue;
    const barH = (v / max) * usable;
    if (!(barH > 0)) continue;
    const b = sc.shown[i];
    let color = pal.dim; // direction cannot be inferred without both prices
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
  ctx.restore(); // globalAlpha back to the caller's state

  // Tall band only: 8px mono dim labels — "volume" left, peak value
  // right-aligned at the axis edge (plain payload number, no unit).
  if (!tall) return;
  ctx.save();
  ctx.globalAlpha = 1;
  ctx.fillStyle = pal.dim;
  ctx.font = `8px ${mono}`;
  ctx.textAlign = "left";
  ctx.fillText("volume", 6, volY0 + 10);
  ctx.textAlign = "right";
  ctx.fillText(String(max), rightX, volY0 + 10);
  ctx.restore(); // textAlign/font/fillStyle restored for later stages
}
