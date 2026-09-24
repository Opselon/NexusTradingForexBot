/**
 * Charts section — equity curve, drawdown, growth, cumulative PnL.
 *
 * PURPOSE:  frame the four backend series in analytics-studio grid plates
 *           (recessed surface, engineering grid, corner ticks) and add
 *           emphasis bars derived from the SAME loaded samples; the shared
 *           viz kit still draws the actual charts.
 * OWNER:    uiux-w6-account
 * CONSUMES: useEquityCurve/useAccountGrowth, components/viz (EquityCurveChart,
 *           DrawdownChart, SignedBucketChart), studio-math-react (Plate,
 *           EmphBars/EmphLevel), shared formatters
 * PROVIDES: AccountChartsSection (page section)
 * INVARIANTS: nothing is resampled or smoothed client-side — series come from
 *           /api/account/equity-curve (drawdown computed backend, one
 *           methodology) and /api/account/growth (audit snapshots); empty →
 *           the original EmptyState strings; the range buttons re-query
 *           unchanged.
 * EXTEND:   add plates, not new math; emphasis bars may only scale samples
 *           that are already rendered somewhere on this panel.
 */

import { useMemo, useState } from "react";
import { EmptyState, ErrorState, Panel, Skeleton } from "@/components/primitives";
import { DrawdownChart, EquityCurveChart, SignedBucketChart, type SignedBucket } from "@/components/viz";
import { useI18n } from "@/stores/i18nStore";
import { formatDateTime } from "@/lib/format";
import { useAccountGrowth, useEquityCurve } from "../hooks";
import { DASH, FreshnessNote, asErrorText, moneyOrDash } from "./shared";
import { EmphBars, EmphLevel, Plate } from "./studio-math-react";
import "./account-studio.css";

type RangeId = "7" | "30" | "90" | "365" | "all";

const RANGES: Array<{ id: RangeId; label: string; days: number | null }> = [
  { id: "7", label: "7d", days: 7 },
  { id: "30", label: "30d", days: 30 },
  { id: "90", label: "90d", days: 90 },
  { id: "365", label: "1y", days: 365 },
  { id: "all", label: "all", days: null },
];

/** Stable formatter identity (module scope) — lets the chart bail out of
 *  re-renders instead of seeing a fresh inline arrow every render. */
const fmtMoney = (v: number): string => moneyOrDash(v);

/** Stable bucket caption identity for the per-trade waterfall. */
const bucketLabel = (b: SignedBucket): string => (b.bucket_start ? formatDateTime(b.bucket_start) : "");

export function AccountChartsSection() {
  const t = useI18n((s) => s.t);
  const [range, setRange] = useState<RangeId>("all");
  const days = RANGES.find((r) => r.id === range)?.days ?? null;
  const curve = useEquityCurve(days);
  const growth = useAccountGrowth();

  // Payload arrays + the derivations over them are computed ONCE per response
  // identity (`curve.data` only changes when a fetch lands). Deps are exactly
  // the values each derivation reads; the rendered points are unchanged.
  const curveData = curve.data;
  const points = useMemo(() => curveData?.equity_curve ?? [], [curveData]);
  const cum = useMemo(() => curveData?.cumulative_pnl ?? [], [curveData]);
  const maxDd = useMemo(
    () => (points.length ? Math.min(...points.map((p) => p.drawdown_pct ?? 0)) : null),
    [points],
  );
  const cumChartPoints = useMemo(
    () => cum.map((c) => ({ timestamp: c.timestamp, balance: null, equity: c.cumulative_pnl })),
    [cum],
  );
  const buckets = useMemo(
    () =>
      cum
        .slice(0, 40)
        .reverse()
        .map<SignedBucket>((c) => ({
          bucket_start: c.timestamp,
          bullish: c.net_pnl > 0 ? c.net_pnl : 0,
          bearish: c.net_pnl < 0 ? -c.net_pnl : 0,
          neutral: 0,
          article_count: 1,
          top_title: `#${c.ticket ?? "?"} ${c.outcome ?? ""} · ${c.exit ?? ""}`.trim(),
        })),
    [cum],
  );

  const fmt = (ts?: string) => (ts ? formatDateTime(ts) : DASH);
  const rangeLabel = (first?: string, last?: string) => `${fmt(first)} → ${fmt(last)}`;

  return (
    <Panel
      title={t("account.charts.title", "Equity · drawdown · growth")}
      right={
        <>
          <span className="acct-actions">
            {RANGES.map((r) => (
              <button key={r.id} className={`btn small ${range === r.id ? "primary" : "ghost"}`} onClick={() => setRange(r.id)}>
                {r.label}
              </button>
            ))}
          </span>
          <FreshnessNote updatedAtMs={curve.dataUpdatedAt ?? null} label={t("account.fresh.curve", "curve")} />
        </>
      }
    >
      {curve.isPending ? (
        <Skeleton count={3} height={120} />
      ) : curve.isError ? (
        <ErrorState message={asErrorText(curve.error)} onRetry={() => curve.refetch()} />
      ) : points.length === 0 ? (
        <EmptyState message={t("account.charts.empty_snapshots", "No accounting snapshots stored yet.")} hint={t("account.charts.empty_snapshots_hint", "Equity/drawdown charts appear once the accounting worker records snapshots.")} />
      ) : (
        <div className="acc-plate-grid">
          <Plate
            title={t("account.charts.equity_title", "equity (backend running peak shown dashed)")}
            meta={t("account.charts.samples", "{count} samples · {start} → {end}", { count: points.length, start: fmt(points[0]?.timestamp), end: fmt(points[points.length - 1]?.timestamp) })}
            footer={rangeLabel(points[0]?.timestamp, points[points.length - 1]?.timestamp)}
          >
            <EquityCurveChart points={points} field="equity" showPeak formatValue={(v) => moneyOrDash(v)} />
            <div className="tiny faint" style={{ marginBlockStart: 5 }}>
              {t("account.charts.emph_note_equity", "emphasis bars: per-sample equity move scaled to the largest move in view (derived from the samples above)")}
            </div>
            <EmphBars
              bars={points.map((pt, i) => {
                if (i === 0) return { value: null as number | null };
                const prev = points[i - 1];
                const a = pt.equity;
                const b = prev?.equity ?? null;
                if (a === null || b === null) return { value: null as number | null };
                return { value: a - b };
              })}
              minPct={8}
            />
          </Plate>

          <Plate
            title={t("account.charts.dd_title", "drawdown % (one methodology — accounting core)")}
            meta={maxDd === null ? DASH : t("account.charts.worst_dd", "worst sample in view: {worst}", { worst: maxDd.toFixed(2) })}
            footer={t("account.charts.dd_footer", "worst sample in view shown in meta · depth comes from the accounting core, never recomputed here")}
          >
            <DrawdownChart points={points} maxDrawdownPct={maxDd} />
            <EmphBars
              bars={points.map((pt) => ({ value: pt.drawdown_pct ?? null, tone: "neg" as const }))}
              minPct={8}
            />
          </Plate>

          <Plate
            title={t("account.charts.cum_title", "cumulative realized PnL (per closed trade)")}
            meta={t("account.charts.steps", "{count} closed-trade steps", { count: cum.length })}
            footer={t("account.charts.cum_footer", "reconciles with period net_pnl sums")}
          >
            {cum.length === 0 ? (
              <EmptyState message={t("account.charts.empty_cum", "No closed trades to accumulate.")} />
            ) : (
              <EquityCurveChart
                points={cumChartPoints}
                field="equity"
                formatValue={fmtMoney}
              />
            )}
            {cum.length > 0 && (
              <>
                <div className="tiny faint" style={{ marginBlockStart: 5 }}>
                  {t("account.charts.emph_note_cum", "emphasis bars: each closed trade's net PnL, scaled to the largest |net| on this range (backend values)")}
                </div>
                <EmphBars bars={cum.map((c) => ({ value: c.net_pnl }))} minPct={8} />
              </>
            )}
          </Plate>

          <Plate
            title={t("account.charts.pertrade_title", "per-trade PnL contributions (last {count})", { count: Math.min(cum.length, 40) })}
            meta={cum.length ? t("account.charts.of_total", "{shown} of {total}", { shown: Math.min(cum.length, 40), total: cum.length }) : DASH}
            footer={t("account.charts.pertrade_footer", "green = winning trade net, red = losing trade net (backend values)")}
          >
            {cum.length === 0 ? (
              <EmptyState message={t("account.charts.empty_pertrade", "No per-trade rows.")} />
            ) : (
              <SignedBucketChart
                buckets={buckets}
                emptyHint={t("account.charts.empty_pertrade_hint", "no per-trade rows")}
                bucketLabel={bucketLabel}
              />
            )}
          </Plate>
        </div>
      )}

      <Plate
        title={t("account.charts.growth_title", "balance/equity growth (audit_account_snapshots)")}
        meta={t("account.charts.snapshots", "{n} snapshots", { n: (growth.data ?? []).length })}
        footer={
          growth.isError || growth.isPending || (growth.data ?? []).length === 0
            ? undefined
            : rangeLabel((growth.data ?? [])[0]?.timestamp, (growth.data ?? [])[(growth.data ?? []).length - 1]?.timestamp)
        }
      >
        {growth.isPending ? (
          <Skeleton count={1} height={120} />
        ) : growth.isError ? (
          <ErrorState message={asErrorText(growth.error)} onRetry={() => growth.refetch()} />
        ) : (growth.data ?? []).length === 0 ? (
          <EmptyState message={t("account.charts.empty_growth", "Growth history unavailable.")} hint={t("account.charts.empty_growth_hint", "/api/account/growth returned no rows (non-SQLite backend or empty history).")} />
        ) : (
          <>
            <EquityCurveChart points={growth.data ?? []} field="balance" formatValue={(v) => moneyOrDash(v)} />
            <EmphLevel values={(growth.data ?? []).map((g) => g.balance)} minPct={10} />
            <div className="tiny faint" style={{ marginBlockStart: 5 }}>
              {t("account.charts.emph_note_growth", "emphasis strip: audit balance levels (min→max scale, backend snapshots only)")}
            </div>
          </>
        )}
      </Plate>
      <FreshnessNote updatedAtMs={growth.dataUpdatedAt ?? null} label={t("account.fresh.growth", "growth")} />
    </Panel>
  );
}
