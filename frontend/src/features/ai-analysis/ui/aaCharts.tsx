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
 * PERF (wave 2): every component is memo()ed at the bottom of this file —
 *   the page re-renders every 15s on the latest-signal poll, and chart props
 *   are reference-stable (stats data, useMemo'd series), so React skips the
 *   whole SVG subtree between polls.
 */

import { memo } from "react";
import { formatTime } from "@/lib/format";
import { useI18n } from "@/stores/i18nStore";
import { actionTone } from "../model";
import {
  actionFamily,
  actionKpi,
  countRows,
  donutSegments,
  familyTotals,
  rrRatio,
  type CountRow,
  type TimelineSeries,
} from "./vizMath";

const FAM_CLASS = (f: ReturnType<typeof actionFamily>): string => `aa-fam-${f}`;

/* ─────────────────────────── action donut ─────────────────────────────── */

/** Donut of /api/v1/decisions/stats by_action collapsed to display families,
 *  with the real backend labels as a legend (counts + share of the map). */
function ActionDonutBase({ byAction }: { byAction: Record<string, number> | undefined }) {
  const t = useI18n((s) => s.t);
  const legend: CountRow[] = countRows(byAction);
  const kpi = actionKpi(byAction);
  const R = 44;
  const { segs, total } = donutSegments(familyTotals(byAction), R);

  if (total <= 0) {
    return <div className="aa-empty">{t("ai-analysis.chart.no_by_action", "backend returned no by_action counts for this window")}</div>;
  }
  const circ = 2 * Math.PI * R;
  const aria = legend.map((r) => `${r.label} ${r.count}`).join(", ");
  return (
    <div className="aa-donut-wrap">
      <svg viewBox="0 0 120 120" className="aa-donut" role="img" aria-label={t("ai-analysis.chart.decision_mix_aria", "decision mix: {s}", { s: aria })}>
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
          {t("ai-analysis.chart.decisions_cap", "decisions")}
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
function ConfidenceTimelineBase({ series }: { series: TimelineSeries }) {
  const t = useI18n((s) => s.t);
  const { points, dropped, from, to } = series;
  const plottable = points.filter((p) => p.v !== null);
  if (points.length === 0) {
    return <div className="aa-empty">{t("ai-analysis.chart.no_history_rows", "no history rows with a parseable timestamp on this page")}</div>;
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
    t("ai-analysis.chart.tl_aria", "confidence timeline, {n} samples from {a} to {b}", {
      n: plottable.length,
      a: formatTime(from),
      b: formatTime(to),
    }) +
    (dropped > 0 ? ", " + t("ai-analysis.chart.tl_dropped", "{n} rows dropped for unparsable timestamps", { n: dropped }) : "");

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
        {t("ai-analysis.chart.tl_caption", "{n} samples · {m} action label{s} in view", {
          n: plottable.length,
          m: nActions,
          s: nActions === 1 ? "" : "s",
        })}
        {dropped > 0
          ? " · " +
            t("ai-analysis.chart.tl_dropped_paren", "{n} row{s} dropped (unparsable timestamp)", {
              n: dropped,
              s: dropped === 1 ? "" : "s",
            })
          : ""}
      </div>
    </div>
  );
}

/* ───────────────────────────── count bars ─────────────────────────────── */

/** Sorted count bars with share-of-total (stage / reason distributions). */
function BarListBase({
  rows,
  tone = "var(--accent)",
  max = 10,
  empty,
}: {
  rows: CountRow[];
  tone?: string;
  max?: number;
  empty?: string;
}) {
  const t = useI18n((s) => s.t);
  const emptyText = empty ?? t("ai-analysis.chart.no_rows", "backend returned no rows");
  if (rows.length === 0) return <div className="aa-empty">{emptyText}</div>;
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
            aria-label={t("ai-analysis.chart.bar_aria", "{l}: {c} ({p}%)", {
              l: r.label,
              c: r.count,
              p: r.pct.toFixed(1),
            })}
          >
            <i style={{ width: `${(r.count / peak) * 100}%`, background: tone }} />
          </span>
          <span className="aa-bar-v">{r.count.toLocaleString("en-US")}</span>
          <span className="aa-bar-p">{r.pct.toFixed(1)}%</span>
        </div>
      ))}
      {rows.length > max && (
        <div className="aa-bars-more">+{t("ai-analysis.chart.bars_more", "{n} smaller rows not shown", { n: (rows.length - max).toLocaleString("en-US") })}</div>
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
function PriceLadderBase({
  entry,
  sl,
  tp,
}: {
  entry: number | null | undefined;
  sl: number | null | undefined;
  tp: number | null | undefined;
}) {
  const t = useI18n((s) => s.t);
  const rr = rrRatio(entry, sl, tp);
  const risk = finite(entry) && finite(sl) ? Math.abs(entry - sl) : null;
  const reward = finite(entry) && finite(tp) ? Math.abs(tp - entry) : null;

  const levels = [
    { key: "tp", label: "TP", v: tp, dist: reward !== null ? t("ai-analysis.chart.reward", "reward {v}", { v: reward.toFixed(2) }) : null },
    { key: "entry", label: "ENTRY", v: entry, dist: null },
    { key: "sl", label: "SL", v: sl, dist: risk !== null ? t("ai-analysis.chart.risk", "risk {v}", { v: risk.toFixed(2) }) : null },
  ].filter((l): l is { key: string; label: string; v: number; dist: string | null } => finite(l.v));

  if (levels.length === 0) {
    return <div className="aa-empty">{t("ai-analysis.chart.no_levels", "no entry / SL / TP recorded for this decision")}</div>;
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
          {rr === null ? t("ai-analysis.chart.rr_missing", "entry · SL · TP not all recorded") : "|tp−entry| / |entry−sl|"}
        </span>
      </div>
    </div>
  );
}

/* ───────────────────────── small table cells ──────────────────────────── */

/** Action chip with the explicit family classification (unknown -> neutral). */
function AaActionChipBase({ action }: { action: string | null | undefined }) {
  if (!action) return <span className="aa-chip aa-fam-unknown">—</span>;
  return <span className={`aa-chip ${FAM_CLASS(actionFamily(action))}`}>{action}</span>;
}

/** Inline confidence meter cell (width IS the 0..1 backend value). */
function ConfCellBase({ value, action }: { value: number | null; action?: string | null }) {
  const t = useI18n((s) => s.t);
  if (value === null || !Number.isFinite(value)) return <span className="faint">—</span>;
  const pct = Math.max(0, Math.min(1, value)) * 100;
  const tone = actionTone(action ?? null);
  return (
    <span className="aa-conf-cell" role="img" aria-label={t("ai-analysis.chart.conf_aria", "confidence {p}%", { p: pct.toFixed(1) })}>
      <span className="aa-conf-track">
        <i className={tone} style={{ width: `${pct}%` }} />
      </span>
      <span className="aa-conf-v">{pct.toFixed(1)}%</span>
    </span>
  );
}

/* ─────────────────────── memoized exports (wave 2) ────────────────────── *
 * Reference-stable props + memo = the 15s latest-poll re-render skips the
 * entire SVG/table subtrees below. Callers import timelineSeries from
 * ./vizMath directly (memoizing the math would be pointless). */
export const ActionDonut = memo(ActionDonutBase);
export const ConfidenceTimeline = memo(ConfidenceTimelineBase);
export const BarList = memo(BarListBase);
export const PriceLadder = memo(PriceLadderBase);
export const AaActionChip = memo(AaActionChipBase);
export const ConfCell = memo(ConfCellBase);
