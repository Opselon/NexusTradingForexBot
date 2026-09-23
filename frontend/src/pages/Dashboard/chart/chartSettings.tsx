import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { ChartKind } from "../chartPainter";

export type { ChartKind };

/** Overlay groups whose VISIBILITY the toolbar may toggle. Values always stay
 *  engine-computed (lane-09: client-side indicator math is forbidden here). */
export type OverlayKey = "zones" | "bos" | "midlines" | "liq" | "orderLines";

interface ChartSettingsValue {
  chartKind: ChartKind;
  setChartKind: (k: ChartKind) => void;
  overlayVisible: Record<OverlayKey, boolean>;
  setOverlayVisible: (k: OverlayKey, v: boolean) => void;
}

const DEFAULT_OVERLAYS: Record<OverlayKey, boolean> = {
  zones: true,
  bos: true,
  midlines: true,
  liq: true,
  orderLines: true,
};
const STORAGE_KEY = "nse.chart.settings.v1";
const Ctx = createContext<ChartSettingsValue | null>(null);

export function ChartSettingsProvider({ children }: { children: ReactNode }) {
  const [chartKind, setChartKindState] = useState<ChartKind>("candles");
  const [overlayVisible, setOverlayVisibleState] = useState<Record<OverlayKey, boolean>>(DEFAULT_OVERLAYS);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw) as { chartKind?: ChartKind; overlayVisible?: Partial<Record<OverlayKey, boolean>> };
      if (parsed.chartKind) setChartKindState(parsed.chartKind);
      if (parsed.overlayVisible) setOverlayVisibleState({ ...DEFAULT_OVERLAYS, ...parsed.overlayVisible });
    } catch {
      /* corrupt or unavailable storage — defaults are fine */
    }
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ chartKind, overlayVisible }));
    } catch {
      /* private mode — settings just do not persist */
    }
  }, [chartKind, overlayVisible]);

  const value = useMemo<ChartSettingsValue>(
    () => ({
      chartKind,
      setChartKind: setChartKindState,
      overlayVisible,
      setOverlayVisible: (k, v) => setOverlayVisibleState((s) => ({ ...s, [k]: v })),
    }),
    [chartKind, overlayVisible],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useChartSettings(): ChartSettingsValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("useChartSettings must be used inside <ChartSettingsProvider>");
  return v;
}
