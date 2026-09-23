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

import { useState } from "react";
import { EmptyState, ErrorState, Panel, Skeleton } from "@/components/primitives";
import { DrawdownChart, EquityCurveChart, SignedBucketChart, type SignedBucket } from "@/components/viz";
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

export function AccountChartsSection() {
  const [range, setRange] = useState<RangeId>("all");
  const days = RANGES.find((r) => r.id === range)?.days ?? null;
  const curve = useEquityCurve(days);
  const growth = useAccountGrowth();

  const points = curve.data?.equity_curve ?? [];
  const cum = curve.data?.cumulative_pnl ?? [];
  const maxDd = points.length ? Math.min(...points.map((p) => p.drawdown_pct ?? 0)) : null;

  const rangeLabel = (first?: string, last?: string) =>
    `${first ? formatDateTime(first) : DASH} → ${last ? formatDateTime(last) : DASH}`;

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
        <ErrorState message={asErrorText(curve.error)} onRetry={() => curve.refetch()} />
      ) : points.length === 0 ? (
        <EmptyState message="No accounting snapshots stored yet." hint="Equity/drawdown charts appear once the accounting worker records snapshots." />
      ) : (
        <div className="acc-plate-grid">
          <Plate
            title="equity (backend running peak shown dashed)"
            meta={`${points.length} samples`}
            footer={rangeLabel(points[0]?.timestamp, points[points.length - 1]?.timestamp)}
          >
            <EquityCurveChart points={points} field="equity" showPeak formatValue={(v) => moneyOrDash(v)} />
            <div className="tiny faint" style={{ marginBlockStart: 5 }}>
              emphasis bars: per-sample equity move scaled to the largest move in view (derived from the samples above)
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
            title="drawdown % (one methodology — accounting core)"
            meta={maxDd === null ? DASH : `worst ${maxDd.toFixed(2)}%`}
            footer="worst sample in view shown in meta · depth comes from the accounting core, never recomputed here"
          >
            <DrawdownChart points={points} maxDrawdownPct={maxDd} />
            <EmphBars
              bars={points.map((pt) => ({ value: pt.drawdown_pct ?? null, tone: "neg" as const }))}
              minPct={8}
            />
          </Plate>

          <Plate
            title="cumulative realized PnL (per closed trade)"
            meta={`${cum.length} steps`}
            footer="reconciles with period net_pnl sums"
          >
            {cum.length === 0 ? (
              <EmptyState message="No closed trades to accumulate." />
            ) : (
              <EquityCurveChart
                points={cum.map((c) => ({ timestamp: c.timestamp, balance: null, equity: c.cumulative_pnl }))}
                field="equity"
                formatValue={(v) => moneyOrDash(v)}
              />
            )}
            {cum.length > 0 && (
              <>
                <div className="tiny faint" style={{ marginBlockStart: 5 }}>
                  emphasis bars: each closed trade&apos;s net PnL, scaled to the largest |net| on this range (backend values)
                </div>
                <EmphBars bars={cum.map((c) => ({ value: c.net_pnl }))} minPct={8} />
              </>
            )}
          </Plate>

          <Plate
            title={`per-trade PnL contributions (last ${Math.min(cum.length, 40)})`}
            meta={cum.length ? `${Math.min(cum.length, 40)} of ${cum.length}` : DASH}
            footer="green = winning trade net, red = losing trade net (backend values)"
          >
            {cum.length === 0 ? (
              <EmptyState message="No per-trade rows." />
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
                emptyHint="no per-trade rows"
                bucketLabel={(b) => (b.bucket_start ? formatDateTime(b.bucket_start) : "")}
              />
            )}
          </Plate>
        </div>
      )}

      <Plate
        title="balance/equity growth (audit_account_snapshots)"
        meta={`${(growth.data ?? []).length} snapshots`}
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
          <EmptyState message="Growth history unavailable." hint="/api/account/growth returned no rows (non-SQLite backend or empty history)." />
        ) : (
          <>
            <EquityCurveChart points={growth.data ?? []} field="balance" formatValue={(v) => moneyOrDash(v)} />
            <EmphLevel values={(growth.data ?? []).map((g) => g.balance)} minPct={10} />
            <div className="tiny faint" style={{ marginBlockStart: 5 }}>
              emphasis strip: audit balance levels (min→max scale, backend snapshots only)
            </div>
          </>
        )}
      </Plate>
      <FreshnessNote updatedAtMs={growth.dataUpdatedAt ?? null} label="growth" />
    </Panel>
  );
}
