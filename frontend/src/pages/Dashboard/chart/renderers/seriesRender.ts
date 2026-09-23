import type { ChartKind, Palette, PainterScene, PlotGeom } from "../../chartPainter";

/** Wave-2 LANE D seam: non-candle series presentation (line / area / hollow)
 *  over the SAME slot geometry — pure presentation of backend OHLC, no math
 *  beyond pixel mapping (lane-09 safe). Called instead of the candle loop
 *  whenever scene.kind !== "candles". */
export function paintSeries(
  _kind: ChartKind,
  _ctx: CanvasRenderingContext2D,
  _sc: PainterScene,
  _geom: PlotGeom,
  _pal: Palette,
  _mono: string,
): void {
  /* LANE D implements: line = close polyline; area = same + vertical fade;
     hollow = unfilled candle bodies. Forming bar keeps the dashed accent. */
}
