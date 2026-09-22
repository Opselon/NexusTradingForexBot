/**
 * Performance metrics + per-kind series + Performance Intelligence.
 *
 * The advanced grid echoes `advanced` from /api/account/performance (computed
 * by the accounting core over `sample_trades` closed trades); missing stats
 * render "—" — never a confident 0 (legacy acctNum contract). The series chart
 * draws net_pnl per consecutive period straight from /performance/{kind}/series.
 * The intelligence panel echoes the structured PerformanceReport summary that
 * the Telegram daily report consumes — same object, so UI and report agree.
 */

import { useState } from "react";
import { EmptyState, ErrorState, Panel, Skeleton, StatusBadge } from "@/components/primitives";
import { Sparkline } from "@/components/viz";
import { formatDateTime } from "@/lib/format";
import { useAccountPerformance, useAccountIntelligence, useAccountSeries } from "../hooks";
import { ADVANCED_ROWS } from "../model";
import type { PeriodKind } from "../types";
import { DASH, FreshnessNote, asErrorText, moneyOrDash } from "./shared";

export function AdvancedMetricsSection() {
  const perf = useAccountPerformance();
  const a = perf.data?.advanced;

  return (
    <Panel
      title="Risk-adjusted performance"
      right={<FreshnessNote updatedAtMs={perf.dataUpdatedAt ?? null} label="advanced" staleAfterMs={90_000} />}
    >
      {perf.isPending ? (
        <Skeleton count={4} height={38} />
      ) : perf.isError ? (
        <ErrorState message={asErrorText(perf.error)} onRetry={() => perf.refetch()} />
      ) : !a ? (
        <EmptyState message="No advanced metrics computed." hint="Needs closed trades in the accounting core." />
      ) : (
        <>
          <div className="acct-grid-stats">
            {ADVANCED_ROWS.map((row) => {
              const raw = (a as Record<string, number | null | undefined>)[row.key];
              const shown =
                raw === null || raw === undefined
                  ? DASH
                  : row.percentOfOne
                    ? `${(raw * 100).toFixed(1)}%`
                    : row.money
                      ? moneyOrDash(raw)
                      : `${raw.toFixed(row.digits)}${row.suffix ?? ""}`;
              const tone = raw === null || raw === undefined ? "" : raw > 0 ? "pos" : raw < 0 ? "neg" : "";
              return (
                <div className="acct-stat" key={row.key} title={row.key}>
                  <div className="k">{row.label}</div>
                  <div className={`v ${tone}`}>{shown}</div>
                </div>
              );
            })}
          </div>
          <div className="tiny faint" style={{ marginTop: 8 }}>
            source: accounting core · {a.sample_trades ?? 0} closed trades · sharpe/sortino/calmar/sqn computed over the same realized series the period
            reports use
          </div>
        </>
      )}
    </Panel>
  );
}

export function PeriodSeriesSection() {
  const [kind, setKind] = useState<PeriodKind>("DAY");
  const series = useAccountSeries(kind, 30);

  return (
    <Panel
      title={`Net PnL per ${kind.toLowerCase()} (last 30 consecutive periods)`}
      right={
        <>
          <span className="acct-actions">
            {(["DAY", "WEEK", "MONTH", "YEAR"] as PeriodKind[]).map((k) => (
              <button key={k} className={`btn small ${kind === k ? "primary" : "ghost"}`} onClick={() => setKind(k)}>
                {k}
              </button>
            ))}
          </span>
          <FreshnessNote updatedAtMs={series.dataUpdatedAt ?? null} label="series" />
        </>
      }
    >
      {series.isPending ? (
        <Skeleton count={2} height={60} />
      ) : series.isError ? (
        <ErrorState message={asErrorText(series.error)} onRetry={() => series.refetch()} />
      ) : (series.data ?? []).length === 0 ? (
        <EmptyState message="No periods returned." />
      ) : (
        <>
          <div className="spark-cell" style={{ gap: 14 }}>
            <Sparkline
              values={(series.data ?? []).map((p) => (typeof p.net_pnl === "number" ? p.net_pnl : null))}
              width={420}
              height={54}
              label={`net pnl per ${kind}`}
            />
            <div className="tiny muted">
              {(series.data ?? []).length} periods · last {moneyOrDash(series.data?.[0]?.net_pnl ?? null)}
            </div>
          </div>
          <div style={{ display: "grid", gap: 2, marginTop: 10, maxHeight: 220, overflowY: "auto" }}>
            {(series.data ?? []).map((p, i) => (
              <div className="statline" key={`${p.key}-${i}`} style={{ fontSize: 11, justifyContent: "space-between" }}>
                <span>{p.key ?? `#${i}`}</span>
                <span>{p.total_trades ?? 0} trades</span>
                <span className={ (p.net_pnl ?? 0) >= 0 ? "tx-good" : "tx-bad" } >{moneyOrDash(p.net_pnl, true)}</span>
                <span className="faint">wr {p.win_rate === null || p.win_rate === undefined ? DASH : `${p.win_rate.toFixed(0)}%`}</span>
              </div>
            ))}
          </div>
          <div className="tiny faint" style={{ marginTop: 6 }}>oldest → newest; the accounting worker keeps consecutive-period rows server-side.</div>
        </>
      )}
    </Panel>
  );
}

export function PerformanceIntelligenceSection() {
  const [kind, setKind] = useState<PeriodKind>("DAY");
  const intel = useAccountIntelligence(kind);
  const [showReport, setShowReport] = useState(false);

  const i = intel.data?.intelligence;

  return (
    <Panel
      title="Performance intelligence (report engine)"
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
            {showReport ? "summary" : "full report"}
          </button>
        </>
      }
    >
      {intel.isPending ? (
        <Skeleton count={2} height={44} />
      ) : intel.isError ? (
        <ErrorState message={asErrorText(intel.error)} onRetry={() => intel.refetch()} />
      ) : !intel.data ? (
        <EmptyState message="No intelligence report." />
      ) : showReport ? (
        <pre style={{ background: "var(--bg-inset)", border: "1px solid var(--border)", borderRadius: 8, padding: 10, fontSize: 10.5, maxHeight: 420, overflow: "auto", fontFamily: "var(--mono)", margin: 0 }}>
          {JSON.stringify(intel.data.report ?? {}, null, 2)}
        </pre>
      ) : (
        <div className="grid cols-2">
          <div>
            <dl className="kv">
              <dt>anomaly state</dt>
              <dd>
                <StatusBadge status={i?.status ?? "NO_DATA"} />
              </dd>
              <dt>behavior state</dt>
              <dd>
                <StatusBadge status={i?.behavior_state ?? "NO_DATA"} />
              </dd>
              <dt>trades analyzed</dt>
              <dd>{i?.trades_analyzed ?? DASH}</dd>
              <dt>evidence coverage</dt>
              <dd>{i?.evidence_coverage == null ? DASH : `${(i.evidence_coverage * 100).toFixed(0)}%`}</dd>
              <dt>versions</dt>
              <dd className="tiny">
                behavior {i?.analysis_version || DASH} · anomaly {i?.anomaly_version || DASH}
              </dd>
            </dl>
          </div>
          <div>
            <div className="section-title">behavioral flags (backend counts)</div>
            {Object.keys(i?.behavioral_flags ?? {}).length === 0 ? (
              <EmptyState message="No behavioral flags counted." />
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
              anomalies
            </div>
            {Object.keys(i?.anomalies ?? {}).length === 0 ? (
              <EmptyState message="No anomalies counted for this period." />
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
        deterministic multi-stage enrichment over the accounting core — the same object the Telegram daily report consumes (read-only, never writes
        financial truth) · period {kind}
      </div>
      <FreshnessNote updatedAtMs={intel.dataUpdatedAt ?? null} label="intelligence" />
    </Panel>
  );
}

/** Human-readable line: latest snapshot time if present in the report. */
export function intelGeneratedAt(report: Record<string, unknown> | undefined): string {
  const g = report?.generated_at;
  return typeof g === "string" ? formatDateTime(g) : DASH;
}
