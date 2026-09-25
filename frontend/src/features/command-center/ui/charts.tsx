/**
 * Command Center charts — presentation-only SVG (feature-local, theme tokens).
 *
 * viz-kit contract applies: props carry backend-derived values (see ../analysis),
 * null rates render as an explicit UNKNOWN row/gap — never a fabricated zero,
 * never a verdict word the backend did not print.
 */

import type { ReactNode } from "react";
import type { FunnelStage, GateOutcome, Histogram } from "../analysis";
import { useI18n } from "@/stores/i18nStore";
import "../command-center.css";

/* ------------------------------------------------------------------ Donut */

export interface DonutSegment {
  label: string;
  count: number;
  /** CSS color (theme token) from the caller's presentation map. */
  color: string;
  share: number;
}

export function Donut({ segments, centerLabel, centerValue }: { segments: DonutSegment[]; centerLabel: string; centerValue: string }) {
  const t = useI18n((s) => s.t);
  if (segments.length === 0) return <div className="cc-chart-empty">{t("command-center.analysis.empty_census", "no census rows returned by the backend")}</div>;
  const R = 54;
  const C = 2 * Math.PI * R;
  let offset = 0;
  return (
    <div className="cc-donut-wrap">
      <svg className="viz cc-donut" viewBox="0 0 140 140" role="img" aria-label={t("command-center.analysis.aria_distribution", "{v} distribution", { v: centerLabel })}>
        {segments.map((s, i) => {
          const len = Math.max(0, s.share * C);
          const el = (
            <circle
              key={s.label}
              cx={70}
              cy={70}
              r={R}
              fill="none"
              stroke={s.color}
              strokeWidth={16}
              strokeDasharray={`${len} ${C - len}`}
              strokeDashoffset={-offset}
              transform="rotate(-90 70 70)"
            >
              <title>{`${s.label}: ${s.count} (${(s.share * 100).toFixed(1)}%)`}</title>
            </circle>
          );
          offset += len;
          void i;
          return el;
        })}
        <text x={70} y={66} textAnchor="middle" className="cc-donut-value">
          {centerValue}
        </text>
        <text x={70} y={84} textAnchor="middle" className="cc-donut-label">
          {centerLabel.toUpperCase()}
        </text>
      </svg>
      <ul className="cc-donut-legend">
        {segments.map((s) => (
          <li key={s.label}>
            <i style={{ background: s.color }} aria-hidden="true" />
            <span className="cc-legend-label" title={s.label}>
              {s.label}
            </span>
            <span className="cc-legend-count">{s.count}</span>
            <span className="cc-legend-share">{(s.share * 100).toFixed(1)}%</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/* ------------------------------------------------------------- Histogram */

export function HistogramChart({
  data,
  label,
  formatBucket,
  tone = "var(--accent)",
}: {
  data: Histogram;
  label: string;
  formatBucket: (lo: number, hi: number) => string;
  tone?: string;
}) {
  const t = useI18n((s) => s.t);
  const W = 320;
  const H = 120;
  const padB = 18;
  const step = W / Math.max(1, data.buckets.length);
  const barW = Math.max(2, step - 3);
  const anyCount = data.buckets.some((b) => b.count > 0);
  return (
    <div className="cc-hist">
      <div className="cc-hist-title">{label}</div>
      {!anyCount ? (
        <div className="cc-chart-empty">{t("command-center.analysis.empty_samples", "no numeric samples reported by the backend")}</div>
      ) : (
        <svg className="viz" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={t("command-center.analysis.aria_distribution", "{v} distribution", { v: label })}>
          {data.buckets.map((b, i) => {
            const h = (b.count / data.max) * (H - padB - 6);
            return (
              <g key={i}>
                <title>{`${formatBucket(b.lo, b.hi)}: ${b.count}`}</title>
                <rect x={i * step + 1.5} y={H - padB - h} width={barW} height={Math.max(b.count > 0 ? 2 : 0, h)} fill={tone} opacity={0.85} rx={1} />
              </g>
            );
          })}
          <line className="viz-zero" x1={0} x2={W} y1={H - padB} y2={H - padB} />
          <text className="viz-axis" x={0} y={H - 5}>
            {formatBucket(data.buckets[0]?.lo ?? 0, data.buckets[0]?.hi ?? 0)}
          </text>
          <text className="viz-axis" x={W / 2} y={H - 5} textAnchor="middle">
            {label}
          </text>
          <text className="viz-axis" x={W} y={H - 5} textAnchor="end">
            {formatBucket(data.buckets[data.buckets.length - 1]?.lo ?? 0, data.buckets[data.buckets.length - 1]?.hi ?? 0)}
          </text>
        </svg>
      )}
      <div className="cc-hist-foot tiny faint">
        n={data.total}
        {data.missing > 0 && <span className="cc-gap"> · {t("command-center.analysis.no_sample", "{n} no sample (gap)", { n: data.missing })}</span>}
        {data.below > 0 && <span> · {t("command-center.analysis.below_range", "{n} below range", { n: data.below })}</span>}
        {data.above > 0 && <span> · {t("command-center.analysis.above_range", "{n} above range", { n: data.above })}</span>}
      </div>
    </div>
  );
}

/* ------------------------------------------------------- Gate outcomes */

export function GateOutcomeRows({ gates }: { gates: GateOutcome[] }) {
  const t = useI18n((s) => s.t);
  if (gates.length === 0) return <div className="cc-chart-empty">{t("command-center.analysis.empty_gates", "no gate metrics reported by the backend")}</div>;
  return (
    <div className="cc-gates">
      {gates.map((g) => {
        const rate = g.pass_rate;
        return (
          <div className="cc-gate-row" key={g.gate}>
            <span className="cc-gate-name">{g.gate}</span>
            <span className="cc-gate-track" role="img" aria-label={t("command-center.analysis.gate_rate_aria", "{gate} pass rate {p}", { gate: g.gate, p: rate === null ? t("command-center.analysis.not_tested", "not tested") : `${(rate * 100).toFixed(1)}%` })}>
              <i className="cc-gate-pass" style={{ width: rate === null ? 0 : `${Math.max(0, Math.min(1, rate)) * 100}%` }} />
              <i className="cc-gate-fail" style={{ width: g.total > 0 ? `${(g.fail / g.total) * 100}%` : 0 }} />
            </span>
            <span className="cc-gate-num">
              {rate === null ? (
                <span className="faint">{t("command-center.analysis.not_tested", "not tested")}</span>
              ) : (
                <>
                  <b>{g.pass}</b>
                  <span className="faint">{t("command-center.analysis.pass_of", "/{n} pass · ", { n: g.total })}</span>
                  <span className="cc-fail">{t("command-center.analysis.fail_count", "{n} fail", { n: g.fail })}</span>
                </>
              )}
            </span>
          </div>
        );
      })}
    </div>
  );
}

/* --------------------------------------------------------------- Funnel */

export function FunnelRows({ stages }: { stages: FunnelStage[] }) {
  const t = useI18n((s) => s.t);
  if (stages.length === 0) return <div className="cc-chart-empty">{t("command-center.analysis.empty_pipeline", "no pipeline counts reported by the backend")}</div>;
  const peak = Math.max(1, ...stages.map((s) => s.value));
  return (
    <div className="cc-funnel">
      {stages.map((s) => (
        <div className="cc-funnel-row" key={s.label}>
          <span className="cc-funnel-label">{s.label}</span>
          <span className="cc-funnel-track" role="img" aria-label={`${s.label}: ${s.value}${s.base !== null ? ` of ${s.base}` : ""}`}>
            <i className={`cc-funnel-fill ${s.tone}`} style={{ width: `${(s.value / peak) * 100}%` }} />
            <span
              className="cc-funnel-base"
              style={{
                left: s.base === null ? undefined : `${Math.min(100, (s.base / peak) * 100)}%`,
              }}
            />
          </span>
          <span className="cc-funnel-num">
            <b>{s.value}</b>
            {s.conversion !== null && <span className="faint"> · {(s.conversion * 100).toFixed(1)}%</span>}
          </span>
        </div>
      ))}
    </div>
  );
}

/* ---------------------------------------------------------- ring histogram */

export function EvidenceRingsChart({ buckets, missing, total }: { buckets: number[]; missing: number; total: number }) {
  const t = useI18n((s) => s.t);
  const peak = Math.max(1, ...buckets);
  const any = buckets.some((b) => b > 0) || missing > 0;
  if (!any) return <div className="cc-chart-empty">{t("command-center.analysis.empty_evidence", "no evidence rows reported by the backend")}</div>;
  return (
    <div className="cc-rings">
      {buckets.map((c, k) => (
        <div className="cc-ring-row" key={k}>
          <span className="cc-ring-label">{t("command-center.analysis.ring_pass", "{n}/4 pass", { n: k })}</span>
          <span className="cc-ring-track" role="img" aria-label={t("command-center.analysis.aria_ring", "{k} of 4 evidence gates passed: {n} strategies", { k, n: c })}>
            <i style={{ width: `${(c / peak) * 100}%` }} />
          </span>
          <span className="cc-ring-num">{c}</span>
        </div>
      ))}
      {missing > 0 && (
        <div className="cc-ring-row cc-gap-row">
          <span className="cc-ring-label">{t("command-center.analysis.no_evidence", "no evidence")}</span>
          <span className="cc-ring-track dashed" role="img" aria-label={t("command-center.analysis.aria_missing", "{n} strategies without evidence rows", { n: missing })}>
            <i className="dashed" style={{ width: `${(missing / peak) * 100}%` }} />
          </span>
          <span className="cc-ring-num">{missing}</span>
        </div>
      )}
      <div className="tiny faint">{t("command-center.analysis.fleet_n", "fleet n={n}", { n: total })}</div>
    </div>
  );
}

/** Section wrapper used by AnalysisSection for one chart card. */
export function ChartCard({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return (
    <div className="cc-card">
      <div className="cc-card-title">{title}</div>
      {hint && <div className="cc-card-hint tiny faint">{hint}</div>}
      {children}
    </div>
  );
}
