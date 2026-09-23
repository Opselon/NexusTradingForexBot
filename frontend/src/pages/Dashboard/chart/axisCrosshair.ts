import type { Palette, PainterScene, PlotGeom } from "../chartPainter";

/** Wave-2 LANE C seam: TradingView-style crosshair axis tags — price tag on
 *  the right axis at the cursor Y, time tag on the bottom axis at the hovered
 *  bar (drawn AFTER the axes, outside the plot clip). */
export function paintAxisTags(
  _ctx: CanvasRenderingContext2D,
  _sc: PainterScene,
  _geom: PlotGeom,
  _pal: Palette,
  _mono: string,
): void {
  /* LANE C implements. */
}
