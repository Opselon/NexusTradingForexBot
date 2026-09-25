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
import { useI18n } from "@/stores/i18nStore";

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
  const t = useI18n((s) => s.t);
  if (rows.length === 0) return null; // no rows → no strip (never a phantom 0)
  const d = derive(rows);
  return (
    <div className="pos-strip" role="group" aria-label={t("positions.strip.aria", "Derived aggregates of the visible blotter rows")}>
      <span className="pos-strip__item">
        <span className="pos-strip__lab">{t("positions.strip.pnl", "open p&l")}</span>
        <span
          className="pos-strip__val"
          data-tone={!d.pnlProven ? "partial" : d.pnl >= 0 ? "pos" : "neg"}
          title={d.pnlProven ? t("positions.strip.pnl_title", "Σ profit of the visible rows — arithmetic on backend values") : t("positions.metric.partial_sub", "some rows carry no profit value — sum withheld")}
        >
          {d.pnlProven ? formatPnl(d.pnl) : t("positions.status.partial", "PARTIAL")}
        </span>
        <span className="l4-prov" title={t("positions.strip.pnl_prov", "sum computed in the browser from the rows on screen — not a backend field")}>
{t("positions.strip.derived", "derived")}
        </span>
      </span>

      <span className="pos-strip__item">
        <span className="pos-strip__lab">{t("positions.strip.rows", "rows")}</span>
        <span className="pos-strip__val" title={t("positions.strip.rows_title", "count of rows currently visible in the blotter — not a backend count field")}>
          {d.count}
        </span>
        <span className="l4-prov" title={t("positions.strip.rows_prov", "counted in the browser from the rows on screen — not a backend field")}>
          derived
        </span>
      </span>

      <span className="pos-strip__item">
        <span className="pos-strip__lab">{t("positions.strip.exposure", "gross exposure")}</span>
        <span
          className="pos-strip__val"
          title={
            d.volumeProven
              ? t("positions.strip.exp_title", "Σ volume (lots) of the visible rows — the payload carries no contract size, so a currency notional is not computed")
              : t("positions.strip.exp_title_partial", "some rows carry no volume value — sum withheld")
          }
        >
          {d.volumeProven ? `${t("positions.strip.lots", "{n} lots", { n: formatNumber(d.volume) })}` : "—"}
        </span>
        <span className="l4-prov" title={t("positions.strip.exp_prov", "Σ volume of the rows on screen — not a backend field")}>
          derived
        </span>
      </span>
    </div>
  );
}
