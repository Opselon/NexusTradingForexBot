/**
 * Performance metrics + per-kind series + Performance Intelligence.
 *
 * The advanced grid echoes `advanced` from /api/account/performance (computed
 * by the accounting core over `sample_trades` closed trades); missing stats
 * render "—" — never a confident 0 (legacy acctNum contract). The series chart
 * draws net_pnl per consecutive period straight from /performance/{kind}/series.
 * The intelligence panel echoes the structured PerformanceReport summary that
 * the Telegram daily report consumes — same object, so UI and report agree.
 *
 * Wave 6 (analytics studio): advanced metrics render in a studio distribution
 * plate; the per-period series carries an emphasis strip scaled to the SAME
 * net_pnl values the sparkline already draws. No metric is recomputed.
 *
 * OWNER:    uiux-w6-account
 * CONSUMES: useAccountPerformance/useAccountIntelligence/useAccountSeries,
 *           ADVANCED_ROWS (../model), shared formatters, studio-math-react
 * PROVIDES: AdvancedMetricsSection, PeriodSeriesSection,
 *           PerformanceIntelligenceSection
 * INVARIANTS: missing stats render "—" — never 0 (BUG-020 lineage); emphasis
 *           bars scale to on-screen values and are labeled "derived"; the
 *           kind switch and report toggle keep working unchanged.
 * EXTEND:   a new advanced stat needs an ADVANCED_ROWS entry, not new math.
 */

import { useMemo, useState } from "react";
import { EmptyState, ErrorState, Panel, Skeleton, StatusBadge } from "@/components/primitives";
import { Sparkline } from "@/components/viz";
import { useI18n } from "@/stores/i18nStore";
import { formatDateTime } from "@/lib/format";
import { useAccountPerformance, useAccountIntelligence, useAccountSeries } from "../hooks";
import { ADVANCED_ROWS } from "../model";
import type { PeriodKind } from "../types";
import { DASH, FreshnessNote, asErrorText, moneyOrDash } from "./shared";
import { DistPanel, EmphBars, type DistRowProps } from "./studio-math-react";

/**
 * Unit family of an advanced row — a count (12 wins) shares no scale with a
 * ratio (Sharpe 1.4), so each family gets its own magnitude ceiling. Pure,
 * module scope: never re-declared inside a render.
 */
function advFamily(r: (typeof ADVANCED_ROWS)[number]): string {
  if (r.money) return "$";
  if (r.percentOfOne || r.suffix === "%") return "pct";
  if (r.suffix === "R") return "R";
  if (r.suffix === "s") return "s";
  if (r.key.startsWith("max_consecutive")) return "count";
  return "ratio";
}

export function AdvancedMetricsSection() {
  const t = useI18n((s) => s.t);
  const perf = useAccountPerformance();
  const a = perf.data?.advanced;

  // One shared scale would let avg_hold_sec (1800s) flatten Sharpe (1.4) to a
  // zero-width bar — the screen must not imply "≈ 0". Each unit family gets its
  // own magnitude ceiling, computed ONCE per distinct payload instead of on
  // every render. Deps are exactly the payload this reads; rows are unchanged.
  const advRows = useMemo<DistRowProps[]>(() => {
    if (!a) return [];
    const ceiling = new Map<string, number>();
    for (const r of ADVANCED_ROWS) {
      const raw = (a as Record<string, number | null | undefined>)[r.key];
      if (raw === null || raw === undefined || !Number.isFinite(raw)) continue;
      const f = advFamily(r);
      ceiling.set(f, Math.max(ceiling.get(f) ?? 0, Math.abs(raw)));
    }
    return ADVANCED_ROWS.map((row) => {
      const raw = (a as Record<string, number | null | undefined>)[row.key];
      const known = raw !== null && raw !== undefined && Number.isFinite(raw);
      const v = known ? (raw as number) : null;
      const shown =
        v === null
          ? DASH
          : row.percentOfOne
            ? `${(v * 100).toFixed(1)}%`
            : row.money
              ? moneyOrDash(v, true)
              : `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(row.digits)}${row.suffix ?? ""}`;
      return {
        label: row.label,
        value: v,
        text: shown,
        full: ceiling.get(advFamily(row)) || 1,
        tone: v === null ? "dim" : v > 0 ? "pos" : v < 0 ? "neg" : "dim",
        title: `${row.key} = ${shown}`,
      };
    });
  }, [a]);

  return (
    <Panel
      title={t("account.advanced.title", "Risk-adjusted performance")}
      right={<FreshnessNote updatedAtMs={perf.dataUpdatedAt ?? null} label={t("account.fresh.advanced", "advanced")} staleAfterMs={90_000} />}
    >
      {perf.isPending ? (
        <Skeleton count={4} height={38} />
      ) : perf.isError ? (
        <ErrorState message={asErrorText(perf.error)} onRetry={() => perf.refetch()} />
      ) : !a ? (
        <EmptyState message={t("account.advanced.empty", "No advanced metrics computed.")} hint={t("account.advanced.empty_hint", "Needs closed trades in the accounting core.")} />
      ) : (
        <DistPanel
          title={t("account.advanced.title", "Risk-adjusted performance")}
          scaleNote={t("account.advanced.scale_note", "bars scale within each unit family — $ rows against $, ratios against ratios, never mixed")}
          footer={t("account.advanced.footer", "source: accounting core · {n} closed trades · sharpe/sortino/calmar/sqn computed over the same realized series the period reports use", { n: a.sample_trades ?? 0 })}
          rows={advRows}
        />
      )}
    </Panel>
  );
}

export function PeriodSeriesSection() {
  const t = useI18n((s) => s.t);
  const [kind, setKind] = useState<PeriodKind>("DAY");
  const series = useAccountSeries(kind, 30);

  // Backend series, memoized per response identity; the derivations below read
  // only it. `?? []` lives INSIDE the memo so a pending query does not hand
  // every derivation a brand-new array reference each render. `values` keeps
  // its null gaps (Sparkline renders them as gaps).
  const seriesData = useMemo(() => series.data ?? [], [series.data]);
  const seriesValues = useMemo(
    () => seriesData.map((p) => (typeof p.net_pnl === "number" ? p.net_pnl : null)),
    [seriesData],
  );
  const seriesBars = useMemo(() => seriesData.map((pr) => ({ value: pr.net_pnl ?? null })), [seriesData]);
  const seriesRows = useMemo(
    () =>
      seriesData.map((p, i) => (
        <div className="statline" key={`${p.key}-${i}`} style={{ fontSize: 11, justifyContent: "space-between" }}>
          <span>{p.key ?? `#${i}`}</span>
          <span>{t("account.series.row_trades", "{n} trades", { n: p.total_trades ?? 0 })}</span>
          <span className={(p.net_pnl ?? 0) >= 0 ? "tx-good" : "tx-bad"}>{moneyOrDash(p.net_pnl, true)}</span>
          <span className="faint">{t("account.series.row_wr", "wr {v}", { v: p.win_rate === null || p.win_rate === undefined ? DASH : `${p.win_rate.toFixed(0)}%` })}</span>
        </div>
      )),
    [seriesData],
  );

  return (
    <Panel
      title={t("account.series.title", "Net PnL per {kind} (last 30 consecutive periods)", { kind: kind.toLowerCase() })}
      right={
        <>
          <span className="acct-actions">
            {(["DAY", "WEEK", "MONTH", "YEAR"] as PeriodKind[]).map((k) => (
              <button key={k} className={`btn small ${kind === k ? "primary" : "ghost"}`} onClick={() => setKind(k)}>
                {k}
              </button>
            ))}
          </span>
          <FreshnessNote updatedAtMs={series.dataUpdatedAt ?? null} label={t("account.fresh.series", "series")} />
        </>
      }
    >
      {series.isPending ? (
        <Skeleton count={2} height={60} />
      ) : series.isError ? (
        <ErrorState message={asErrorText(series.error)} onRetry={() => series.refetch()} />
      ) : (series.data ?? []).length === 0 ? (
        <EmptyState message={t("account.series.empty", "No periods returned.")} />
      ) : (
        <>
          <div className="spark-cell" style={{ gap: 14 }}>
            <Sparkline
              values={seriesValues}
              width={420}
              height={54}
              label={t("account.series.sparkline_label", "net pnl per {kind}", { kind })}
            />
            <div className="tiny muted">
              {t("account.series.count", "{n} periods · last {v}", { n: (series.data ?? []).length, v: moneyOrDash(series.data?.[0]?.net_pnl ?? null) })}
            </div>
          </div>
          <div style={{ marginTop: 4 }}>
            <EmphBars bars={seriesBars} minPct={10} />
            <div className="tiny faint" style={{ marginBlockStart: 4 }}>
              {t("account.series.emph_note", "emphasis bars: per-period net PnL, scaled to the largest |net PnL| in view (derived from the values above)")}
            </div>
          </div>
          <div style={{ display: "grid", gap: 2, marginTop: 10, maxHeight: 220, overflowY: "auto" }}>
            {seriesRows}
          </div>
          <div className="tiny faint" style={{ marginTop: 6 }}>{t("account.series.foot_order", "oldest → newest; the accounting worker keeps consecutive-period rows server-side.")}</div>
        </>
      )}
    </Panel>
  );
}

export function PerformanceIntelligenceSection() {
  const t = useI18n((s) => s.t);
  const [kind, setKind] = useState<PeriodKind>("DAY");
  const intel = useAccountIntelligence(kind);
  const [showReport, setShowReport] = useState(false);

  const i = intel.data?.intelligence;

  // perf: pretty-print the backend report once per distinct payload
  // instead of on every render; dep is the exact object serialized.
  const report = intel.data?.report;
  const reportText = useMemo(() => JSON.stringify(report ?? {}, null, 2), [report]);
  return (
    <Panel
      title={t("account.intel.panel_title", "Performance intelligence (report engine)")}
      right={
        <>
          <span className="acct-actions">
            {(["DAY", "WEEK", "MONTH", "YEAR"] as PeriodKind[]).map((k) => (
              <button key={k} className={`btn small ${kind === k ? "primary" : "ghost"}`} onClick={() => setKind(k)}>
                {k}
              </button>
            ))}
          </span>
          <button className="btn small ghost" onClick={() => setShowReport((v) => !v)}>
            {showReport ? t("account.intel.summary", "summary") : t("account.intel.full_report", "full report")}
          </button>
        </>
      }
    >
      {intel.isPending ? (
        <Skeleton count={2} height={44} />
      ) : intel.isError ? (
        <ErrorState message={asErrorText(intel.error)} onRetry={() => intel.refetch()} />
      ) : !intel.data ? (
        <EmptyState message={t("account.intel.empty", "No intelligence report.")} />
      ) : showReport ? (
        <pre tabIndex={0} style={{ background: "var(--bg-inset)", border: "1px solid var(--border)", borderRadius: 8, padding: 10, fontSize: 10.5, maxHeight: 420, overflow: "auto", fontFamily: "var(--mono)", margin: 0 }}>
          {reportText}
        </pre>
      ) : (
        <div className="grid cols-2">
          <div>
            <dl className="kv">
              <dt>{t("account.intel.anomaly_state", "anomaly state")}</dt>
              <dd>
                <StatusBadge status={i?.status ?? "NO_DATA"} />
              </dd>
              <dt>{t("account.intel.behavior_state", "behavior state")}</dt>
              <dd>
                <StatusBadge status={i?.behavior_state ?? "NO_DATA"} />
              </dd>
              <dt>{t("account.intel.trades_analyzed", "trades analyzed")}</dt>
              <dd>{i?.trades_analyzed ?? DASH}</dd>
              <dt>{t("account.intel.evidence_coverage", "evidence coverage")}</dt>
              <dd>{i?.evidence_coverage == null ? DASH : `${(i.evidence_coverage * 100).toFixed(0)}%`}</dd>
              <dt>{t("account.intel.versions", "versions")}</dt>
              <dd className="tiny">
                {t("account.intel.versions_value", "behavior {b} · anomaly {a}", { b: i?.analysis_version || DASH, a: i?.anomaly_version || DASH })}
              </dd>
            </dl>
          </div>
          <div>
            <div className="section-title">{t("account.intel.flags_title", "behavioral flags (backend counts)")}</div>
            {Object.keys(i?.behavioral_flags ?? {}).length === 0 ? (
              <EmptyState message={t("account.intel.flags_empty", "No behavioral flags counted.")} />
            ) : (
              <div className="statline">
                {Object.entries(i?.behavioral_flags ?? {}).map(([k, v]) => (
                  <span key={k}>
                    {k}: <b>{v}</b>
                  </span>
                ))}
              </div>
            )}
            <div className="section-title" style={{ marginTop: 10 }}>
              {t("account.intel.anomalies_title", "anomalies")}
            </div>
            {Object.keys(i?.anomalies ?? {}).length === 0 ? (
              <EmptyState message={t("account.intel.anomalies_empty", "No anomalies counted for this period.")} />
            ) : (
              <div className="statline">
                {Object.entries(i?.anomalies ?? {}).map(([k, v]) => (
                  <span key={k}>
                    {k}: <b>{v}</b>
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
      <div className="tiny faint" style={{ marginTop: 8 }}>
        {t("account.intel.note", "deterministic multi-stage enrichment over the accounting core — the same object the Telegram daily report consumes (read-only, never writes financial truth) · period {period}", { period: kind })}
      </div>
      <FreshnessNote updatedAtMs={intel.dataUpdatedAt ?? null} label={t("account.fresh.intelligence", "intelligence")} />
    </Panel>
  );
}

/** Human-readable line: latest snapshot time if present in the report. */
export function intelGeneratedAt(report: Record<string, unknown> | undefined): string {
  const g = report?.generated_at;
  return typeof g === "string" ? formatDateTime(g) : DASH;
}
