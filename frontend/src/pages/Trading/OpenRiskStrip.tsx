/**
 * PURPOSE:  Derived open-risk strip for the trade desk: broker positions
 *           currently in view are summarized as a count / floating P&L /
 *           long-vs-short volume breakdown, each figure carrying a visible
 *           "derived" provenance chip.
 * OWNER:    uiux-w6-trading  (future edits belongs to this lane)
 * CONSUMES: Position rows handed in by the page (mt5 status → snapshot
 *           fallback), positionSide / formatNumber / formatPnl, .trd-risk
 *           classes from trading.css, .l4-prov from pages.css.
 * PROVIDES: default OpenRiskStrip component (props: rows).
 * INVARIANTS: renders nothing for zero rows; sums are withheld (PARTIAL / —)
 *             the moment any row lacks that value; every figure is browser
 *             arithmetic over the rows on screen, labeled "derived".
 * EXTEND:   new stats go through derive() with an explicit proven flag.
 */
import type { Position } from "@/types/domain";
import { formatNumber, formatPnl, positionSide } from "@/lib/format";

interface Derived {
  count: number;
  pnl: number;
  pnlProven: boolean;
  buyVolume: number;
  sellVolume: number;
  volumeProven: boolean;
}

/** Plain arithmetic over the visible rows; *Proven is false when any row
 *  lacks the input — a partial sum is never presented as a total. */
function derive(rows: Position[]): Derived {
  let pnl = 0;
  let pnlProven = true;
  let buyVolume = 0;
  let sellVolume = 0;
  let volumeProven = true;
  for (const r of rows) {
    if (typeof r.profit === "number" && Number.isFinite(r.profit)) pnl += r.profit;
    else pnlProven = false;
    if (typeof r.volume === "number" && Number.isFinite(r.volume)) {
      const side = positionSide(r.type);
      if (side === "BUY") buyVolume += r.volume;
      else if (side === "SELL") sellVolume += r.volume;
      // UNKNOWN-side volume counts toward neither bucket (never guessed).
    } else {
      volumeProven = false;
    }
  }
  return { count: rows.length, pnl, pnlProven, buyVolume, sellVolume, volumeProven };
}

export default function OpenRiskStrip({ rows }: { rows: Position[] }) {
  if (rows.length === 0) return null; // no rows → no strip (never a phantom 0)
  const d = derive(rows);
  return (
    <div className="trd-risk" role="group" aria-label="Open-risk summary of the positions in view (derived)">
      <span className="trd-risk__item">
        <span className="trd-risk__lab">positions in view</span>
        <span className="trd-risk__val" title="count of broker position rows on screen — not a backend count field">
          {d.count}
        </span>
        <span className="l4-prov" title="counted in the browser from the rows on screen — not a backend field">
          derived
        </span>
      </span>

      <span className="trd-risk__item">
        <span className="trd-risk__lab">floating p&amp;l</span>
        <span
          className="trd-risk__val"
          data-tone={!d.pnlProven ? "partial" : d.pnl >= 0 ? "pos" : "neg"}
          title={d.pnlProven ? "Σ profit of the positions in view — arithmetic on backend values" : "some rows carry no profit value — sum withheld"}
        >
          {d.pnlProven ? formatPnl(d.pnl) : "PARTIAL"}
        </span>
        <span className="l4-prov" title="sum computed in the browser from the rows on screen — not a backend field">
          derived
        </span>
      </span>

      <span className="trd-risk__item">
        <span className="trd-risk__lab">buy / sell volume</span>
        <span
          className="trd-risk__val"
          title={
            d.volumeProven
              ? "Σ volume (lots) by side — each bucket restates Position.type; UNKNOWN-side rows count toward neither bucket"
              : "some rows carry no volume value — sums withheld"
          }
        >
          {d.volumeProven ? (
            <>
              <span className="trd-risk__buy">{formatNumber(d.buyVolume)}</span>
              <span className="trd-risk__slash" aria-hidden="true"> / </span>
              <span className="trd-risk__sell">{formatNumber(d.sellVolume)}</span>
            </>
          ) : (
            "—"
          )}
        </span>
        <span className="l4-prov" title="Σ volume of the rows on screen — not a backend field">
          derived
        </span>
      </span>
    </div>
  );
}
