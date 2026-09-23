import type { Bar } from "@/types/domain";
import { formatPrice } from "@/lib/format";
import "./legend.css";

export interface ChartLegendProps {
  /** Hovered bar (window slot) — falls back to the last bar when null. */
  hovered: Bar | null;
  last: Bar | null;
  digits: number;
  timeframe: string | null;
  symbol: string | null;
}

/** Wave-2 LANE C slot: top-left OHLC legend. Scaffold renders the honest
 *  minimum (hovered-or-last bar values); lane C adds delta %, countdown and
 *  layout polish. Values are backend passthrough — no derived signals. */
export function ChartLegend({ hovered, last, digits, timeframe, symbol }: ChartLegendProps) {
  const b = hovered ?? last;
  if (!b) return null;
  const up = b.close !== null && b.open !== null && b.close >= b.open;
  return (
    <div className="lg-legend" aria-hidden="false">
      <span className="lg-legend__sym">{symbol ?? "—"}</span>
      <span className="lg-legend__tf">{timeframe ?? "—"}</span>
      {b.is_complete === false && <span className="lg-legend__forming">FORMING</span>}
      <span className="lg-legend__vals">
        O <b>{formatPrice(b.open, digits)}</b> H <b>{formatPrice(b.high, digits)}</b> L{" "}
        <b>{formatPrice(b.low, digits)}</b>{" "}
        <span className={up ? "up" : "down"}>
          C <b>{formatPrice(b.close, digits)}</b>
        </span>{" "}
        V <b>{b.tick_volume ?? b.volume ?? "—"}</b>
      </span>
    </div>
  );
}
