/**
 * Charts section — equity curve, drawdown, growth, cumulative PnL.
 *
 * The series come from the accounting core (`/api/account/equity-curve`
 * carries drawdown already computed with the ONE methodology; `/growth`
 * carries audit-snapshot balance/equity). This panel only renders those rows
 * through the shared viz kit; nothing is resampled or smoothed client-side.
 */

import { useState } from "react";
import { EmptyState, ErrorState, Panel, Skeleton } from "@/components/primitives";
import { DrawdownChart, EquityCurveChart, SignedBucketChart, type SignedBucket } from "@/components/viz";
import { formatDateTime } from "@/lib/format";
import { useAccountGrowth, useEquityCurve } from "../hooks";
import { DASH, FreshnessNote, asErrorText, moneyOrDash } from "./shared";
import { useI18n } from "@/stores/i18nStore";

type RangeId = "7" | "30" | "90" | "365" | "all";

const RANGES: Array<{ id: RangeId; label: string; days: number | null }> = [
  { id: "7", label: "7d", days: 7 },
  { id: "30", label: "30d", days: 30 },
  { id: "90", label: "90d", days: 90 },
  { id: "365", label: "1y", days: 365 },
  { id: "all", label: "all", days: null },
];

type Translator = (key: string, fallback: string, vars?: Record<string, string | number>) => string;

function rangeLabelT(id: RangeId, t: Translator): string {
  switch (id) {
    case "7": return t("account.charts.r7", "7d");
    case "30": return t("account.charts.r30", "30d");
    case "90": return t("account.charts.r90", "90d");
    case "365": return t("account.charts.r365", "1y");
    case "all": return t("account.charts.rall", "all");
    default: return id;
  }
}

export function AccountChartsSection() {
  const t = useI18n((s) => s.t);
  const [range, setRange] = useState<RangeId>("all");
  const days = RANGES.find((r) => r.id === range)?.days ?? null;
  const curve = useEquityCurve(days);
  const growth = useAccountGrowth();

  const points = curve.data?.equity_curve ?? [];
  const cum = curve.data?.cumulative_pnl ?? [];
  const maxDd = points.length ? Math.min(...points.map((p) => p.drawdown_pct ?? 0)) : null;

  return (
    <Panel
      title={t("account.charts.title", "Equity · drawdown · growth")}
      right={
        <>
          <span className="acct-actions">
            {RANGES.map((r) => (
              <button key={r.id} className={`btn small ${range === r.id ? "primary" : "ghost"}`} onClick={() => setRange(r.id)}>
                {rangeLabelT(r.id, t)}
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
        <ErrorState message={asErrorText(curve.error, t)} onRetry={() => curve.refetch()} />
      ) : points.length === 0 ? (
        <EmptyState message={t("account.charts.empty_snapshots", "No accounting snapshots stored yet.")} hint={t("account.charts.empty_snapshots_hint", "Equity/drawdown charts appear once the accounting worker records snapshots.")} />
      ) : (
        <div className="grid cols-2">
          <section>
            <div className="section-title">{t("account.charts.equity_title", "equity (backend running peak shown dashed)")}</div>
            <EquityCurveChart points={points} field="equity" showPeak formatValue={(v) => moneyOrDash(v)} />
            <div className="tiny faint" style={{ marginTop: 4 }}>
              {t("account.charts.samples", "{count} samples · {start} → {end}", {
                count: String(points.length),
                start: points[0] ? formatDateTime(points[0]?.timestamp ?? DASH) : DASH,
                end: points[points.length - 1] ? formatDateTime(points[points.length - 1]?.timestamp ?? DASH) : DASH,
              })}
            </div>
          </section>
          <section>
            <div className="section-title">{t("account.charts.dd_title", "drawdown % (one methodology — accounting core)")}</div>
            <DrawdownChart points={points} maxDrawdownPct={maxDd} />
            <div className="tiny faint" style={{ marginTop: 4 }}>{t("account.charts.worst", "worst sample in view: {worst}", { worst: maxDd === null ? DASH : `${maxDd.toFixed(2)}%` })}</div>
          </section>
          <section>
            <div className="section-title">{t("account.charts.cum_title", "cumulative realized PnL (per closed trade)")}</div>
            {cum.length === 0 ? (
              <EmptyState message={t("account.charts.empty_cum", "No closed trades to accumulate.")} />
            ) : (
              <EquityCurveChart
                points={cum.map((c) => ({ timestamp: c.timestamp, balance: null, equity: c.cumulative_pnl }))}
                field="equity"
                formatValue={(v) => moneyOrDash(v)}
              />
            )}
            <div className="tiny faint" style={{ marginTop: 4 }}>{t("account.charts.steps", "{count} closed-trade steps · reconciles with period net_pnl sums", { count: String(cum.length) })}</div>
          </section>
          <section>
            <div className="section-title">{t("account.charts.pertrade_title", "per-trade PnL contributions (last {count})", { count: String(Math.min(cum.length, 40)) })}</div>
            {cum.length === 0 ? (
              <EmptyState message={t("account.charts.empty_pertrade", "No per-trade rows.")} />
            ) : (
              <SignedBucketChart
                buckets={cum
                  .slice(0, 40)
                  .reverse()
                  .map<SignedBucket>((c) => ({
                    bucket_start: c.timestamp,
                    bullish: c.net_pnl > 0 ? c.net_pnl : 0,
                    bearish: c.net_pnl < 0 ? -c.net_pnl : 0,
                    neutral: 0,
                    article_count: 1,
                    top_title: `#${c.ticket ?? "?"} ${c.outcome ?? ""} · ${c.exit ?? ""}`.trim(),
                  }))}
                emptyHint={t("account.charts.empty_pertrade_hint", "no per-trade rows")}
                bucketLabel={(b) => (b.bucket_start ? formatDateTime(b.bucket_start) : "")}
              />
            )}
            <div className="tiny faint" style={{ marginTop: 4 }}>{t("account.charts.legend", "green = winning trade net, red = losing trade net (backend values)")}</div>
          </section>
        </div>
      )}

      <div className="section-title" style={{ marginTop: 12 }}>
        {t("account.charts.growth_title", "balance/equity growth (audit_account_snapshots)")}
      </div>
      {growth.isPending ? (
        <Skeleton count={1} height={120} />
      ) : growth.isError ? (
        <ErrorState message={asErrorText(growth.error, t)} onRetry={() => growth.refetch()} />
      ) : (growth.data ?? []).length === 0 ? (
        <EmptyState message={t("account.charts.empty_growth", "Growth history unavailable.")} hint={t("account.charts.empty_growth_hint", "/api/account/growth returned no rows (non-SQLite backend or empty history).")} />
      ) : (
        <EquityCurveChart points={growth.data ?? []} field="balance" formatValue={(v) => moneyOrDash(v)} />
      )}
      <FreshnessNote updatedAtMs={growth.dataUpdatedAt ?? null} label={t("account.fresh.growth", "growth")} />
    </Panel>
  );
}
