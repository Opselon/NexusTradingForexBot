import type { Palette, PainterScene, PlotGeom } from "../chartPainter";

/**
 * Wave-3 LANE A seam: cache the STATIC layers of the chart.
 *
 * Everything that depends only on (bars geometry + scale + overlays + kind) is
 * static per frame unless the view/price window moved: zones, BOS, midlines,
 * liq markers, candles/series, volume. The hover crosshair, live quote line +
 * tag, replay cursor dim and the axis tags are DYNAMIC and stay in paintChart.
 *
 * Correctness contract (a stale static layer is a lie to the operator):
 * invalidate on ANY of bars identity, slot geometry, price scale, digits, kind.
 * The cache key function is exported so a test can assert it changes exactly
 * when it must.
 */
export interface LayerCache {
  key: string;
  canvas: HTMLCanvasElement;
}

export function cacheKeyFor(sc: PainterScene, geom: PlotGeom): string {
  const sig = [
    sc.shown.length,
    sc.count,
    sc.left,
    sc.digits,
    sc.kind ?? "candles",
    geom.w,
    geom.h,
    geom.priceH,
    geom.volH,
    (sc.overlays?.rectangles?.length ?? 0),
    (sc.overlays?.bos_lines?.length ?? 0),
    (sc.overlays?.midlines?.length ?? 0),
    (sc.overlays?.liq_markers?.length ?? 0),
    sc.overlays?.order_lines ? 1 : 0,
  ];
  return sig.join("|");
}

/**
 * Paint the static layers into `cache.canvas` and return true when a repaint
 * happened, false when the cached frame was still valid (caller counts draw
 * calls). Lane A implements the real painting (delegating to the existing
 * stage code paths); the scaffold keeps it a no-op that always reports a
 * repaint so behavior is unchanged until the lane lands.
 */
export function paintStaticLayers(
  _ctx: CanvasRenderingContext2D,
  _sc: PainterScene,
  _geom: PlotGeom,
  _pal: Palette,
  _mono: string,
  _cache: LayerCache,
): boolean {
  /* LANE A implements. */
  return true;
}
