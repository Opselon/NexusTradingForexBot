/**
 * PURPOSE:  Derived aggregate strip rendered directly above the blotter:
 *           total open P&L, visible-row count, gross exposure — each figure
 *           carrying a visible "derived" provenance chip.
 * OWNER:    uiux-wave5-positions  (future edits to this file belong to this lane)
 * CONSUMES: Position type from types/domain, formatPnl/formatNumber from
 *           lib/format, .pos-strip/.l4-prov classes (positions.css/pages.css).
 * PROVIDES: default AggregateStrip component.
 * INVARIANTS: renders nothing for zero rows; a sum is withheld (PARTIAL / —)
 *             the moment any row lacks that value — never a partial number
 *             presented as a total; all arithmetic is over the rows handed in.
 * EXTEND:   new stats go through derive() with an explicit proven flag — never
 *           display a figure whose inputs could be silently missing.
 */
import type { Position } from "@/types/domain";
import { formatNumber, formatPnl } from "@/lib/format";

interface Derived {
  pnl: number;
  pnlProven: boolean;
  volume: number;
  volumeProven: boolean;
  count: number;
}

/** Plain arithmetic over the visible rows; `*Proven` is false when any row
 *  lacks the input — sums are only trustworthy when every addend exists. */
function derive(rows: Position[]): Derived {
  let pnl = 0;
  let pnlProven = true;
  let volume = 0;
  let volumeProven = true;
  for (const r of rows) {
    if (typeof r.profit === "number" && Number.isFinite(r.profit)) pnl += r.profit;
    else pnlProven = false;
    if (typeof r.volume === "number" && Number.isFinite(r.volume)) volume += r.volume;
    else volumeProven = false;
  }
  return { pnl, pnlProven, volume, volumeProven, count: rows.length };
}

export default function AggregateStrip({ rows }: { rows: Position[] }) {
  if (rows.length === 0) return null; // no rows → no strip (never a phantom 0)
  const d = derive(rows);
  return (
    <div className="pos-strip" role="group" aria-label="Derived aggregates of the visible blotter rows">
      <span className="pos-strip__item">
        <span className="pos-strip__lab">open p&amp;l</span>
        <span
          className="pos-strip__val"
          data-tone={!d.pnlProven ? "partial" : d.pnl >= 0 ? "pos" : "neg"}
          title={d.pnlProven ? "Σ profit of the visible rows — arithmetic on backend values" : "some rows carry no profit value — sum withheld"}
        >
          {d.pnlProven ? formatPnl(d.pnl) : "PARTIAL"}
        </span>
        <span className="l4-prov" title="sum computed in the browser from the rows on screen — not a backend field">
          derived
        </span>
      </span>

      <span className="pos-strip__item">
        <span className="pos-strip__lab">rows</span>
        <span className="pos-strip__val" title="count of rows currently visible in the blotter — not a backend count field">
          {d.count}
        </span>
        <span className="l4-prov" title="counted in the browser from the rows on screen — not a backend field">
          derived
        </span>
      </span>

      <span className="pos-strip__item">
        <span className="pos-strip__lab">gross exposure</span>
        <span
          className="pos-strip__val"
          title={
            d.volumeProven
              ? "Σ volume (lots) of the visible rows — the payload carries no contract size, so a currency notional is not computed"
              : "some rows carry no volume value — sum withheld"
          }
        >
          {d.volumeProven ? `${formatNumber(d.volume)} lots` : "—"}
        </span>
        <span className="l4-prov" title="Σ volume of the rows on screen — not a backend field">
          derived
        </span>
      </span>
    </div>
  );
}
