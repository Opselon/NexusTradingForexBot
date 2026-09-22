/**
 * PURPOSE:  Stress/limits matrix — a responsive CSS-grid of every visible
 *           value-vs-limit pair with sticky row/column headers and hover
 *           tooltips (title attr carrying the payload paths).
 * OWNER:    uiux-wave5-risk  (future edits to this file belong to this lane)
 * CONSUMES: ./riskThresholds (RiskLimitRow, pressureOf, rampStyle, utilTone)
 * PROVIDES: <LimitMatrix rows={…} />
 * INVARIANTS: cells render "—" for an absent number (never 0); the tint is
 *             colour-only depth-past-soft-threshold arithmetic, never a
 *             verdict word; empty input renders the kit's EmptyState.
 * EXTEND:   rows come from ./limitRows — add data there, not here.
 */

import { EmptyState } from "@/components/primitives";
import { formatNumber } from "@/lib/format";
import { pressureOf, rampStyle, utilTone } from "./riskThresholds";
import type { RiskLimitRow } from "./riskThresholds";

const COLUMNS = ["Metric", "Value", "Limit", "Utilization"] as const;

export function LimitMatrix({ rows }: { rows: RiskLimitRow[] }) {
  if (rows.length === 0) {
    return (
      <EmptyState
        message="No limit rows in the payload (no account, exposure or config numbers to compare)."
        hint="The matrix appears as soon as the backend sends a comparable pair — an empty store is never rendered as 'within limit'."
      />
    );
  }
  return (
    <div className="rsk-matrix" role="table" aria-label="Stress and limits matrix">
      {COLUMNS.map((c) => (
        <div key={c} className="rsk-mx__h" role="columnheader">
          {c}
        </div>
      ))}

      {rows.map((row) => {
        const pressure = pressureOf(row.value, row.limit, row.direction);
        const utilPct = pressure === null ? null : Math.round(Math.min(pressure, 9.99) * 100);
        const tone = utilTone(row.direction === "le" ? pressure : pressure);
        const valueText = row.value === null ? "—" : `${formatNumber(row.value, row.digits)}${row.unit}`;
        const limitText = row.limit === null ? "—" : `${formatNumber(row.limit, row.digits)}${row.unit}`;
        const tip =
          row.limit === null
            ? `${row.label}: ${valueText} — ${row.field} (no backend limit in payload; never read as satisfied)`
            : `${row.label}: ${valueText} vs ${limitText} — ${row.field}`;
        return (
          <div key={row.id} className="rsk-mx__row" role="row">
            <div className="rsk-mx__rowh" role="rowheader" title={tip}>
              <span className="rsk-mx__name">{row.label}</span>
              <span className="rsk-mx__field">{row.direction === "ge" ? "floor ≥" : "ceiling ≤"}</span>
            </div>
            <div className="rsk-mx__cell num" role="cell" title={tip} style={rampStyle(pressure)}>
              {valueText}
            </div>
            <div className="rsk-mx__cell num" role="cell" title={tip}>
              {limitText}
            </div>
            <div className={`rsk-mx__cell num tone-${tone}`} role="cell" title={tip} style={rampStyle(pressure)}>
              {utilPct === null ? <span className="rsk-mx__unk">— no limit</span> : `${utilPct}%`}
            </div>
          </div>
        );
      })}
    </div>
  );
}
