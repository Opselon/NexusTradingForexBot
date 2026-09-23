import { useEffect, useState } from "react";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import type { Bar } from "@/types/domain";
import type { ChartHistoryResponse } from "@/pages/_shared/contracts";
import { formatPrice } from "@/lib/format";
import { useI18n } from "@/stores/i18nStore";
import "./legend.css";

export interface ChartLegendProps {
  /** Hovered bar (window slot) — falls back to the last bar when null. */
  hovered: Bar | null;
  last: Bar | null;
  digits: number;
  timeframe: string | null;
  symbol: string | null;
}

/** Bars of the chart-history query — READ-ONLY cache lookup (prefix key match,
 *  freshest symbol/timeframe hit). No extra fetch: DashboardPage's query owns
 *  this entry; the legend only borrows it to measure bar spacing. Returns []
 *  when history never landed (snapshot-fallback source) → countdown omitted. */
function historyBars(qc: QueryClient, symbol: string | null, timeframe: string | null): Bar[] {
  let best: ChartHistoryResponse | null = null;
  let bestAt = -1;
  for (const q of qc.getQueryCache().findAll({ queryKey: ["chart-history"] })) {
    const d = q.state.data as ChartHistoryResponse | undefined;
    if (!d?.bars?.length) continue;
    if (symbol && d.symbol && d.symbol !== symbol) continue;
    if (timeframe && d.timeframe && d.timeframe !== timeframe) continue;
    if (q.state.dataUpdatedAt > bestAt) {
      best = d;
      bestAt = q.state.dataUpdatedAt;
    }
  }
  return best?.bars ?? [];
}

/** Median time step of the LAST 5 COMPLETED bars — works on every TF incl
 *  D1/W1 and weekend gaps (no TF→minutes map). Skips zero/negative diffs;
 *  needs >= 2 completed diffs, else null (countdown omitted). */
function medianBarSpacing(bars: Bar[]): number | null {
  const done = bars.filter((x) => x.is_complete !== false).slice(-5);
  const diffs: number[] = [];
  for (let i = 1; i < done.length; i++) {
    const prev = done[i - 1];
    const cur = done[i];
    if (!prev || !cur) continue;
    const t0 = Date.parse(prev.time);
    const t1 = Date.parse(cur.time);
    if (!Number.isFinite(t0) || !Number.isFinite(t1)) continue;
    const d = t1 - t0;
    if (d > 0) diffs.push(d);
  }
  if (diffs.length < 2) return null;
  diffs.sort((x, y) => x - y);
  return diffs[Math.floor(diffs.length / 2)] ?? null;
}

/** Wave-2 LANE C slot: top-left OHLC legend (hovered-or-last bar values,
 *  delta % vs open, forming-bar close countdown). Values are backend
 *  passthrough — no derived signals, lane-09 safe. */
export function ChartLegend({ hovered, last, digits, timeframe, symbol }: ChartLegendProps) {
  const qc = useQueryClient();
  const [now, setNow] = useState(() => Date.now());
  const t = useI18n((s) => s.t);

  const b = hovered ?? last;
  // Countdown describes the LIVE forming bar (not the hovered one): next
  // boundary = last bar time + median spacing of the last 5 completed bars.
  const forming = last !== null && last.is_complete === false;
  const spacingMs = forming && last ? medianBarSpacing(historyBars(qc, symbol, timeframe)) : null;
  const lastMs = last ? Date.parse(last.time) : NaN;
  const boundary = spacingMs !== null && Number.isFinite(lastMs) ? lastMs + spacingMs : null;

  useEffect(() => {
    if (boundary === null) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [boundary]);

  if (!b) return null;
  const up = b.close !== null && b.open !== null && b.close >= b.open;
  const deltaRaw = b.open !== null && b.close !== null && b.open !== 0 ? ((b.close - b.open) / b.open) * 100 : null;
  const delta = deltaRaw !== null && Number.isFinite(deltaRaw) ? deltaRaw : null;
  let countdown: string | null = null;
  if (boundary !== null) {
    const totalSec = Math.floor(Math.max(0, boundary - now) / 1000);
    countdown = t("dash.chart.closes_in", "closes in {m}:{s}", {
      m: Math.floor(totalSec / 60),
      s: String(totalSec % 60).padStart(2, "0"),
    });
  }
  return (
    <div className="lg-legend" aria-hidden="false">
      <span className="lg-legend__sym">{symbol ?? "—"}</span>
      <span className="lg-legend__tf">{timeframe ?? "—"}</span>
      {b.is_complete === false && <span className="lg-legend__forming">{t("dash.chart.forming", "FORMING")}</span>}
      <span className="lg-legend__vals">
        O <b>{formatPrice(b.open, digits)}</b> H <b>{formatPrice(b.high, digits)}</b> L{" "}
        <b>{formatPrice(b.low, digits)}</b>{" "}
        <span className={up ? "up" : "down"}>
          C <b>{formatPrice(b.close, digits)}</b>
        </span>{" "}
        V <b>{b.tick_volume ?? b.volume ?? "—"}</b>
      </span>
      {delta !== null && (
        <span className={`lg-legend__delta ${delta >= 0 ? "up" : "down"}`}>
          {delta >= 0 ? "+" : ""}
          {delta.toFixed(2)}%
        </span>
      )}
      {countdown !== null && <span className="lg-legend__count">{countdown}</span>}
    </div>
  );
}
