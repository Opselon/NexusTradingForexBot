/**
 * Charts section — equity curve, drawdown, growth, cumulative PnL.
 *
 * The series come from the accounting core (`/api/account/equity-curve`
 * carries drawdown already computed with the ONE methodology; `/growth`
 * carries audit-snapshot balance/equity). This panel only renders those rows
 * through the shared viz kit; nothing is resampled or smoothed client-side.
 */

import { useMemo, useState } from "react";
import { EmptyState, ErrorState, Panel, Skeleton } from "@/components/primitives";
import { DrawdownChart, EquityCurveChart, SignedBucketChart, type SignedBucket } from "@/components/viz";
import { formatDateTime } from "@/lib/format";
import { useAccountGrowth, useEquityCurve } from "../hooks";
import { DASH, FreshnessNote, errorProps, moneyOrDash } from "./shared";

type RangeId = "7" | "30" | "90" | "365" | "all";

/** Stable chart formatters — inline arrow props would defeat any memo in the
 *  shared viz components on every equity-curve poll tick. */
const fmtMoney = (v: number | null | undefined): string => moneyOrDash(v);
const fmtBucketTime = (b: SignedBucket): string => (b.bucket_start ? formatDateTime(b.bucket_start) : "");

const RANGES: Array<{ id: RangeId; label: string; days: number | null }> = [
  { id: "7", label: "7d", days: 7 },
  { id: "30", label: "30d", days: 30 },
  { id: "90", label: "90d", days: 90 },
  { id: "365", label: "1y", days: 365 },
  { id: "all", label: "all", days: null },
];

export function AccountChartsSection() {
  const [range, setRange] = useState<RangeId>("all");
  const days = RANGES.find((r) => r.id === range)?.days ?? null;
  const curve = useEquityCurve(days);
  const growth = useAccountGrowth();

  const points = curve.data?.equity_curve ?? [];
  const cum = curve.data?.cumulative_pnl ?? [];
  /* Derived chart inputs memoized: the 60 s curve poll must not rebuild the
   * series arrays (or rotate the window) on every tick. */
  const maxDd = useMemo(() => (points.length ? Math.min(...points.map((p) => p.drawdown_pct ?? 0)) : null), [points]);
  const cumPoints = useMemo(
    () => cum.map((c) => ({ timestamp: c.timestamp, balance: null, equity: c.cumulative_pnl })),
    [cum],
  );
  const buckets = useMemo<SignedBucket[]>(
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

  return (
    <Panel
      title="Equity · drawdown · growth"
      right={
        <>
          <span className="acct-actions">
            {RANGES.map((r) => (
              <button key={r.id} className={`btn small ${range === r.id ? "primary" : "ghost"}`} onClick={() => setRange(r.id)}>
                {r.label}
              </button>
            ))}
          </span>
          <FreshnessNote updatedAtMs={curve.dataUpdatedAt ?? null} label="curve" />
        </>
      }
    >
      {curve.isPending ? (
        <Skeleton count={3} height={120} />
      ) : curve.isError ? (
        <ErrorState {...errorProps(curve.error)} onRetry={() => curve.refetch()} />
      ) : points.length === 0 ? (
        <EmptyState message="No accounting snapshots stored yet." hint="Equity/drawdown charts appear once the accounting worker records snapshots." />
      ) : (
        <div className="grid cols-2">
          <section>
            <div className="section-title">equity (backend running peak shown dashed)</div>
            <EquityCurveChart points={points} field="equity" showPeak formatValue={fmtMoney} />
            <div className="tiny faint" style={{ marginTop: 4 }}>
              {points.length} samples · {points[0] ? formatDateTime(points[0]?.timestamp ?? DASH) : DASH} → {points[points.length - 1] ? formatDateTime(points[points.length - 1]?.timestamp ?? DASH) : DASH}
            </div>
          </section>
          <section>
            <div className="section-title">drawdown % (one methodology — accounting core)</div>
            <DrawdownChart points={points} maxDrawdownPct={maxDd} />
            <div className="tiny faint" style={{ marginTop: 4 }}>worst sample in view: {maxDd === null ? DASH : `${maxDd.toFixed(2)}%`}</div>
          </section>
          <section>
            <div className="section-title">cumulative realized PnL (per closed trade)</div>
            {cum.length === 0 ? (
              <EmptyState message="No closed trades to accumulate." />
            ) : (
              <EquityCurveChart points={cumPoints} field="equity" formatValue={fmtMoney} />
            )}
            <div className="tiny faint" style={{ marginTop: 4 }}>{cum.length} closed-trade steps · reconciles with period net_pnl sums</div>
          </section>
          <section>
            <div className="section-title">per-trade PnL contributions (last {Math.min(cum.length, 40)})</div>
            {cum.length === 0 ? (
              <EmptyState message="No per-trade rows." />
            ) : (
              <SignedBucketChart
                buckets={buckets}
                emptyHint="no per-trade rows"
                bucketLabel={fmtBucketTime}
              />
            )}
            <div className="tiny faint" style={{ marginTop: 4 }}>green = winning trade net, red = losing trade net (backend values)</div>
          </section>
        </div>
      )}

      <div className="section-title" style={{ marginTop: 12 }}>
        balance/equity growth (audit_account_snapshots)
      </div>
      {growth.isPending ? (
        <Skeleton count={1} height={120} />
      ) : growth.isError ? (
        <ErrorState {...errorProps(growth.error)} onRetry={() => growth.refetch()} />
      ) : (growth.data ?? []).length === 0 ? (
        <EmptyState message="Growth history unavailable." hint="/api/account/growth returned no rows (non-SQLite backend or empty history)." />
      ) : (
        <EquityCurveChart points={growth.data ?? []} field="balance" formatValue={fmtMoney} />
      )}
      <FreshnessNote updatedAtMs={growth.dataUpdatedAt ?? null} label="growth" />
    </Panel>
  );
}
