/**
 * aaCharts — hand-rolled SVG/CSS charts for the AI-Analysis page.
 *
 * PURPOSE: the page's four visualisations (action donut, confidence timeline,
 *   count bars, entry/SL/TP price ladder) plus the small table cells
 *   (action chip, confidence cell). No dependencies, no fetching — every
 *   number arrives as props and is mapped by ./vizMath (pure, node-tested).
 * OWNER: ai-analysis feature; future edits live in this folder.
 * CONSUMES: vizMath derivations, theme tokens only (no hardcoded colors —
 *   family colors are CSS classes .aa-fam-* in ./aiAnalysis.css).
 * PROVIDES: ActionDonut, ConfidenceTimeline, BarList, PriceLadder,
 *   AaActionChip, ConfCell.
 * INVARIANTS: null sample -> gap / explicit "—", never a fabricated value;
 *   unknown action labels render the "unknown" family (neutral), never a
 *   guessed color; chart elements carry role="img" + a text alternative.
 * EXTEND: new charts go through vizMath first (testable math), then here.
 */

import { formatTime } from "@/lib/format";
import { actionTone } from "../model";
import {
  actionFamily,
  actionKpi,
  countRows,
  donutSegments,
  familyTotals,
  rrRatio,
  timelineSeries,
  type CountRow,
  type TimelineSeries,
} from "./vizMath";

const FAM_CLASS = (f: ReturnType<typeof actionFamily>): string => `aa-fam-${f}`;

/* ─────────────────────────── action donut ─────────────────────────────── */

/** Donut of /api/v1/decisions/stats by_action collapsed to display families,
 *  with the real backend labels as a legend (counts + share of the map). */
export function ActionDonut({ byAction }: { byAction: Record<string, number> | undefined }) {
  const legend: CountRow[] = countRows(byAction);
  const kpi = actionKpi(byAction);
  const R = 44;
  const { segs, total } = donutSegments(familyTotals(byAction), R);

  if (total <= 0) {
    return <div className="aa-empty">backend returned no by_action counts for this window</div>;
  }
  const circ = 2 * Math.PI * R;
  const aria = legend.map((r) => `${r.label} ${r.count}`).join(", ");
  return (
    <div className="aa-donut-wrap">
      <svg viewBox="0 0 120 120" className="aa-donut" role="img" aria-label={`decision mix: ${aria}`}>
        <g transform="rotate(-90 60 60)">
          <circle className="aa-donut-track" cx="60" cy="60" r={R} />
          {segs.map((s) => (
            <circle
              key={s.family}
              className={`aa-donut-seg ${FAM_CLASS(s.family)}`}
              cx="60"
              cy="60"
              r={R}
              strokeDasharray={`${s.len.toFixed(2)} ${(circ - s.len).toFixed(2)}`}
              strokeDashoffset={s.offset.toFixed(2)}
            >
              <title>{`${s.family}: ${s.count.toLocaleString("en-US")}`}</title>
            </circle>
          ))}
        </g>
        <text className="aa-donut-total" x="60" y="57" textAnchor="middle">
          {kpi.total.toLocaleString("en-US")}
        </text>
        <text className="aa-donut-cap" x="60" y="72" textAnchor="middle">
          decisions
        </text>
      </svg>
      <ul className="aa-legend">
        {legend.map((r) => (
          <li key={r.label}>
            <i className={`aa-dot ${FAM_CLASS(actionFamily(r.label))}`} aria-hidden="true" />
            <span className="aa-legend-k" title={r.label}>
              {r.label}
            </span>
            <span className="aa-legend-v">{r.count.toLocaleString("en-US")}</span>
            <span className="aa-legend-p">{r.pct.toFixed(1)}%</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/* ─────────────────────── confidence timeline ──────────────────────────── */

/** Confidence (0..1) over time from the CURRENT history page's rows. Gaps
 *  where the backend recorded no confidence; dropped rows are captioned. */
export function ConfidenceTimeline({ series }: { series: TimelineSeries }) {
  const { points, dropped, from, to } = series;
  const plottable = points.filter((p) => p.v !== null);
  if (points.length === 0) {
    return <div className="aa-empty">no history rows with a parseable timestamp on this page</div>;
  }

  const W = 640;
  const H = 210;
  const L = 40;
  const Rm = 12;
  const T = 12;
  const B = 26;
  const plotW = W - L - Rm;
  const plotH = H - T - B;
  const span = from !== null && to !== null ? to - from : 0;
  const xOf = (t: number): number =>
    span > 0 ? L + ((t - from!) / span) * plotW : L + plotW / 2;
  const yOf = (v: number): number => T + (1 - Math.max(0, Math.min(1, v))) * plotH;

  const gridVals = [0, 0.25, 0.5, 0.75, 1];
  // Polyline points with explicit gaps (vizMath already clamped v to 0..1).
  const xy = points.map((p) => (p.v === null ? null : { x: xOf(p.t), y: yOf(p.v) }));
  let d = "";
  let pen = false;
  for (const p of xy) {
    if (!p) {
      pen = false;
      continue;
    }
    d += pen ? ` L${p.x.toFixed(2)},${p.y.toFixed(2)}` : ` M${p.x.toFixed(2)},${p.y.toFixed(2)}`;
    pen = true;
  }

  const nActions = new Set(points.map((p) => p.action ?? "?")).size;
  const aria =
    `confidence timeline, ${plottable.length} samples from ${formatTime(from)} to ${formatTime(to)}` +
    (dropped > 0 ? `, ${dropped} rows dropped for unparsable timestamps` : "");

  return (
    <div className="aa-tl">
      <svg viewBox={`0 0 ${W} ${H}`} className="aa-tl-svg" role="img" aria-label={aria}>
        {gridVals.map((g) => (
          <g key={g}>
            <line className="aa-tl-grid" x1={L} x2={W - Rm} y1={yOf(g)} y2={yOf(g)} />
            <text className="aa-tl-axis" x={L - 7} y={yOf(g) + 3} textAnchor="end">
              {Math.round(g * 100)}%
            </text>
          </g>
        ))}
        {d && <path className="aa-tl-line" d={d} />}
        {points.map((p, i) =>
          p.v === null ? null : (
            <circle
              key={i}
              className={`aa-tl-dot ${FAM_CLASS(actionFamily(p.action))}`}
              cx={xOf(p.t)}
              cy={yOf(p.v)}
              r={2.6}
            >
              <title>{`${formatTime(p.t)} · ${p.action ?? "—"} · ${(p.v * 100).toFixed(1)}%`}</title>
            </circle>
          ),
        )}
        <text className="aa-tl-axis" x={L} y={H - 8} textAnchor="start">
          {formatTime(from)}
        </text>
        <text className="aa-tl-axis" x={W - Rm} y={H - 8} textAnchor="end">
          {formatTime(to)}
        </text>
      </svg>
      <div className="aa-tl-cap">
        {plottable.length} samples · {nActions} action label{nActions === 1 ? "" : "s"} in view
        {dropped > 0 ? ` · ${dropped} row${dropped === 1 ? "" : "s"} dropped (unparsable timestamp)` : ""}
      </div>
    </div>
  );
}

/** Build a timeline from history rows using the shared derivation. */
export function historyTimeline(
  items: Array<{ generated_at?: string | null; action?: string | null; conf01?: number | null }> | undefined,
): TimelineSeries {
  return timelineSeries(items);
}

/* ───────────────────────────── count bars ─────────────────────────────── */

/** Sorted count bars with share-of-total (stage / reason distributions). */
export function BarList({
  rows,
  tone = "var(--accent)",
  max = 10,
  empty = "backend returned no rows",
}: {
  rows: CountRow[];
  tone?: string;
  max?: number;
  empty?: string;
}) {
  if (rows.length === 0) return <div className="aa-empty">{empty}</div>;
  const shown = rows.slice(0, max);
  const peak = Math.max(1, ...shown.map((r) => r.count));
  return (
    <div className="aa-bars">
      {shown.map((r) => (
        <div className="aa-bar-row" key={r.label}>
          <span className="aa-bar-k" title={r.label}>
            {r.label}
          </span>
          <span
            className="aa-bar-track"
            role="img"
            aria-label={`${r.label}: ${r.count} (${r.pct.toFixed(1)}%)`}
          >
            <i style={{ width: `${(r.count / peak) * 100}%`, background: tone }} />
          </span>
          <span className="aa-bar-v">{r.count.toLocaleString("en-US")}</span>
          <span className="aa-bar-p">{r.pct.toFixed(1)}%</span>
        </div>
      ))}
      {rows.length > max && (
        <div className="aa-bars-more">+{(rows.length - max).toLocaleString("en-US")} smaller rows not shown</div>
      )}
    </div>
  );
}

/* ─────────────────────────── price ladder ─────────────────────────────── */

const finite = (v: number | null | undefined): v is number =>
  typeof v === "number" && Number.isFinite(v);

/** Vertical entry/SL/TP ladder: markers positioned proportionally over the
 *  recorded price range, distances in price units, R:R as arithmetic over the
 *  three levels (null -> "—", never a guess). */
export function PriceLadder({
  entry,
  sl,
  tp,
}: {
  entry: number | null | undefined;
  sl: number | null | undefined;
  tp: number | null | undefined;
}) {
  const rr = rrRatio(entry, sl, tp);
  const risk = finite(entry) && finite(sl) ? Math.abs(entry - sl) : null;
  const reward = finite(entry) && finite(tp) ? Math.abs(tp - entry) : null;

  const levels = [
    { key: "tp", label: "TP", v: tp, dist: reward !== null ? `reward ${reward.toFixed(2)}` : null },
    { key: "entry", label: "ENTRY", v: entry, dist: null },
    { key: "sl", label: "SL", v: sl, dist: risk !== null ? `risk ${risk.toFixed(2)}` : null },
  ].filter((l): l is { key: string; label: string; v: number; dist: string | null } => finite(l.v));

  if (levels.length === 0) {
    return <div className="aa-empty">no entry / SL / TP recorded for this decision</div>;
  }
  const vals = levels.map((l) => l.v);
  const max = Math.max(...vals);
  const min = Math.min(...vals);
  const spread = max - min;
  const positioned = levels.length > 1 && spread > 0;

  return (
    <div className="aa-ladder">
      <div className={`aa-ladder-axis ${positioned ? "positioned" : ""}`} role="img"
        aria-label={levels.map((l) => `${l.label} ${l.v.toFixed(2)}`).join(", ")}>
        {levels.map((l) => (
          <div
            key={l.key}
            className={`aa-lvl aa-lvl-${l.key}`}
            style={positioned ? { top: `${((max - l.v) / spread) * 100}%` } : undefined}
          >
            <span className="aa-lvl-k">{l.label}</span>
            <span className="aa-lvl-line" aria-hidden="true" />
            <span className="aa-lvl-v">{l.v.toFixed(2)}</span>
            {l.dist && <span className="aa-lvl-d">{l.dist}</span>}
          </div>
        ))}
      </div>
      <div className="aa-rr">
        <span className="aa-rr-k">R:R</span>
        <b className={rr === null ? "faint" : ""}>{rr === null ? "—" : `1 : ${rr.toFixed(2)}`}</b>
        <span className="aa-rr-note">
          {rr === null ? "entry · SL · TP not all recorded" : "|tp−entry| / |entry−sl|"}
        </span>
      </div>
    </div>
  );
}

/* ───────────────────────── small table cells ──────────────────────────── */

/** Action chip with the explicit family classification (unknown -> neutral). */
export function AaActionChip({ action }: { action: string | null | undefined }) {
  if (!action) return <span className="aa-chip aa-fam-unknown">—</span>;
  return <span className={`aa-chip ${FAM_CLASS(actionFamily(action))}`}>{action}</span>;
}

/** Inline confidence meter cell (width IS the 0..1 backend value). */
export function ConfCell({ value, action }: { value: number | null; action?: string | null }) {
  if (value === null || !Number.isFinite(value)) return <span className="faint">—</span>;
  const pct = Math.max(0, Math.min(1, value)) * 100;
  const tone = actionTone(action ?? null);
  return (
    <span className="aa-conf-cell" role="img" aria-label={`confidence ${pct.toFixed(1)}%`}>
      <span className="aa-conf-track">
        <i className={tone} style={{ width: `${pct}%` }} />
      </span>
      <span className="aa-conf-v">{pct.toFixed(1)}%</span>
    </span>
  );
}
