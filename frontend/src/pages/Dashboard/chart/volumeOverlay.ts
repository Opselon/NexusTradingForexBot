import type { Palette, PainterScene, PlotGeom } from "../chartPainter";

/** Wave-2 LANE A seam: tick-volume histogram in the reserved band
 *  geom.volY0..volY0+volH (bottom of the plot, above the time axis).
 *  Data passthrough only — bars carry tick_volume (lane-09 safe). */
export function paintVolume(
  _ctx: CanvasRenderingContext2D,
  _sc: PainterScene,
  _geom: PlotGeom,
  _pal: Palette,
  _mono: string,
): void {
  /* LANE A implements: one rect per shown bar, up/down colored, faint
     centerline + peak label when the band is tall enough. */
}
