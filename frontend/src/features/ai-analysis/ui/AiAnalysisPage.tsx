/**
 * AI Analysis — signals, decisions, indicators, 70D shadow (legacy tab parity).
 *
 * Sections: live signal card · decision stats + NO_TRADE reason distribution ·
 * signal history (page through v1) · indicators wall (gauges/oscillators/MAs/
 * pivots as compact cards) · shadow-70d panel. Per-decision drilldown opens a
 * drawer wired to the gates/evidence/explanation endpoints.
 * No signal/decision is ever synthesized: RESOURCE_NOT_FOUND renders empty.
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  DataTable,
  EmptyState,
  ErrorState,
  MetricCard,
  Panel,
  ProbBar,
  Segmented,
  Skeleton,
  StatusBadge,
} from "@/components/primitives";
import type { ShellPageProps } from "@/app/featureModule";
import { ApiError } from "@/types/api";
import { formatDateTime, formatNumber, formatPrice } from "@/lib/format";
import { DistBars, FreshnessCaption, GateStepper, InfoRow, StatusPill } from "../../research/ui/lane5Kit";
import { actionTone, confidence01, distRows, obj, str, type SignalDto } from "../model";
import { aiAnalysisQueries, orderHistory } from "../useCases";
import DecisionDrawer from "./DecisionDrawer";

type Tab = "signals" | "indicators" | "shadow";

const TFS = ["M1", "M5", "M15", "H1", "H4", "D1"];

export default function AiAnalysisPage(props: ShellPageProps) {
  void props;
  const [tab, setTab] = useState<Tab>("signals");
  const [hoursBack, setHoursBack] = useState(168);
  const [page, setPage] = useState(1);
  const [tf, setTf] = useState("M1");
  const [openDecision, setOpenDecision] = useState<string | null>(null);

  const latestQ = useQuery({
    queryKey: ["ai-analysis", "signal-latest"],
    queryFn: ({ signal }) => aiAnalysisQueries.signalLatest(signal),
    refetchInterval: 15_000,
    retry: false,
  });
  const historyQ = useQuery({
    queryKey: ["ai-analysis", "signal-history", page, hoursBack],
    queryFn: ({ signal }) => aiAnalysisQueries.signalHistory(page, hoursBack, signal),
    retry: false,
    enabled: tab === "signals",
    placeholderData: (prev) => prev,
  });
  const statsQ = useQuery({
    queryKey: ["ai-analysis", "decision-stats", hoursBack],
    queryFn: ({ signal }) => aiAnalysisQueries.decisionStats(hoursBack, signal),
    retry: false,
    enabled: tab === "signals",
  });
  const reasonsQ = useQuery({
    queryKey: ["ai-analysis", "no-trade-reasons"],
    queryFn: ({ signal }) => aiAnalysisQueries.noTradeReasons(signal),
    retry: false,
    enabled: tab === "signals",
  });
  const indSummaryQ = useQuery({
    queryKey: ["ai-analysis", "indicators", "summary", tf],
    queryFn: ({ signal }) => aiAnalysisQueries.indicatorsSummary({ timeframe: tf }, signal),
    retry: false,
    enabled: tab === "indicators",
    staleTime: 20_000,
  });
  const indOscQ = useQuery({
    queryKey: ["ai-analysis", "indicators", "osc", tf],
    queryFn: ({ signal }) => aiAnalysisQueries.indicatorsOscillators({ timeframe: tf }, signal),
    retry: false,
    enabled: tab === "indicators",
  });
  const indMaQ = useQuery({
    queryKey: ["ai-analysis", "indicators", "ma", tf],
    queryFn: ({ signal }) => aiAnalysisQueries.indicatorsMovingAverages({ timeframe: tf }, signal),
    retry: false,
    enabled: tab === "indicators",
  });
  const indPivotQ = useQuery({
    queryKey: ["ai-analysis", "indicators", "pivots", tf],
    queryFn: ({ signal }) => aiAnalysisQueries.indicatorsPivots({ timeframe: tf }, signal),
    retry: false,
    enabled: tab === "indicators",
  });
  const shadowQ = useQuery({
    queryKey: ["ai-analysis", "shadow70d"],
    queryFn: ({ signal }) => aiAnalysisQueries.shadow70d(signal),
    retry: false,
    enabled: tab === "shadow",
  });

  const isNotFound = (e: unknown) => e instanceof ApiError && (e.status === 404 || e.code === "RESOURCE_NOT_FOUND");
  const latest = latestQ.data;

  return (
    <div>
      <div className="page-head" style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <h2>AI Analysis</h2>
        <span className="muted small">model signals · decision gates · indicators · shadow 70D</span>
        <FreshnessCaption timestamp={str(latest?.generated_at)} source="audit_signals ledger" isFetching={latestQ.isFetching} error={latestQ.isError && !isNotFound(latestQ.error)} />
      </div>

      <Panel title="Latest signal (live card)" accent tight>
        {latestQ.isPending ? (
          <Skeleton count={2} />
        ) : isNotFound(latestQ.error) ? (
          <EmptyState message="No signals recorded yet." hint="/api/v1/signals/latest answers RESOURCE_NOT_FOUND — the engine has not emitted a decision." />
        ) : latestQ.isError ? (
          <ErrorState message={(latestQ.error as Error).message} requestId={latestQ.error instanceof ApiError ? latestQ.error.requestId : null} onRetry={() => void latestQ.refetch()} />
        ) : latest ? (
          <div className="decision-card">
            <div>
              <div className={`big ${latest.action === "BUY" ? "buy" : latest.action === "SELL" ? "sell" : "hold"}`}>{latest.action ?? "—"}</div>
              <div className="why-detail tiny">{formatDateTime(latest.generated_at)}</div>
            </div>
            <div style={{ flex: 1 }}>
              <div className="why">
                {latest.symbol} · {latest.regime ?? "regime not recorded"} · {latest.execution_mode ?? "—"}
              </div>
              <ProbBar
                rows={[
                  { label: "confidence", value: confidence01(latest.confidence), tone: actionTone(latest.action) },
                  { label: "before filters", value: confidence01(latest.confidence_before_filters), tone: "flat" },
                  { label: "after filters", value: confidence01(latest.confidence_after_filters), tone: "flat" },
                ]}
              />
              <dl className="kv" style={{ marginTop: 8 }}>
                <InfoRow label="entry / SL / TP" value={`${formatPrice(latest.proposed_entry)} / ${formatPrice(latest.stop_loss)} / ${formatPrice(latest.take_profit)}`} />
                <InfoRow label="htf / smc score" value={`${formatNumber(latest.htf_score ?? null, 3)} / ${formatNumber(latest.smc_score ?? null, 3)}`} />
                <InfoRow label="stage" value={latest.decision_stage ?? "—"} />
                <InfoRow label="blocked_by" value={latest.blocked_by ?? "—"} />
              </dl>
            </div>
            <div style={{ display: "grid", gap: 6, alignContent: "start" }}>
              <button className="btn small primary" onClick={() => setOpenDecision(String(latest.request_id ?? ""))}>
                inspect decision
              </button>
              <span className="tiny muted">gates · evidence · explanation</span>
            </div>
          </div>
        ) : null}
      </Panel>

      <div style={{ marginBlock: 12 }}>
        <Segmented
          options={[
            { id: "signals" as const, label: "Decisions & history" },
            { id: "indicators" as const, label: "Indicators wall" },
            { id: "shadow" as const, label: "Shadow 70D" },
          ]}
          value={tab}
          onChange={setTab}
        />
      </div>

      {tab === "signals" && (
        <div style={{ display: "grid", gap: 12 }}>
          <div className="grid cols-2">
            <Panel
              title={`Decision stats (${hoursBack}h window)`}
              right={
                <select className="select" style={{ width: 110 }} value={hoursBack} onChange={(e) => setHoursBack(Number(e.target.value))}>
                  {[24, 72, 168, 720].map((h) => (
                    <option key={h} value={h}>
                      {h}h
                    </option>
                  ))}
                </select>
              }
              tight
            >
              {statsQ.isPending ? (
                <Skeleton count={2} />
              ) : statsQ.isError ? (
                <EmptyState message={statsQ.error instanceof Error ? statsQ.error.message : "stats unavailable"} />
              ) : (
                <>
                  <MetricCard label="total decisions" value={String(statsQ.data?.total ?? 0)} sub={`scanned window ${statsQ.data?.window_hours ?? hoursBack}h`} />
                  <div style={{ marginTop: 8 }}>
                    <div className="section-title">by action</div>
                    <DistBars rows={distRows(statsQ.data?.by_action)} />
                  </div>
                  <div style={{ marginTop: 8 }}>
                    <div className="section-title">by stage</div>
                    <DistBars rows={distRows(statsQ.data?.by_stage)} tone="var(--violet)" />
                  </div>
                </>
              )}
            </Panel>
            <Panel title="NO_TRADE reason distribution" tight>
              {reasonsQ.isPending ? (
                <Skeleton count={2} />
              ) : reasonsQ.isError ? (
                <EmptyState message="reasons endpoint failed" />
              ) : (
                <>
                  <div className="small muted" style={{ marginBottom: 6 }}>
                    {String(reasonsQ.data?.total ?? 0)} NO_TRADE decisions in ledger
                  </div>
                  <DistBars rows={distRows(reasonsQ.data?.reasons)} tone="var(--amber)" />
                </>
              )}
            </Panel>
          </div>

          <Panel title="Signal history (ledger, newest first)" tight>
            {historyQ.isPending ? (
              <Skeleton count={5} />
            ) : historyQ.isError ? (
              <ErrorState message={historyQ.error instanceof Error ? historyQ.error.message : "history failed"} onRetry={() => void historyQ.refetch()} />
            ) : (historyQ.data?.items ?? []).length === 0 ? (
              <EmptyState message="No signals in this window." />
            ) : (
              <>
                <DataTable
                  headers={[
                    { label: "decision" },
                    { label: "symbol" },
                    { label: "action" },
                    { label: "conf", num: true },
                    { label: "stage" },
                    { label: "reason" },
                    { label: "at" },
                    { label: "" },
                  ]}
                >
                  {orderHistory(historyQ.data?.items ?? []).map((s: SignalDto, i: number) => (
                    <tr key={s.request_id ?? i}>
                      <td className="inline-mono tiny">{str(s.request_id)?.slice(0, 10) ?? "—"}</td>
                      <td className="small">{s.symbol}</td>
                      <td>
                        <StatusBadge status={s.action} />
                      </td>
                      <td className="num tiny">{confidence01(s.confidence) === null ? "—" : `${(confidence01(s.confidence)! * 100).toFixed(1)}%`}</td>
                      <td className="tiny">{s.decision_stage ?? "—"}</td>
                      <td className="tiny muted" title={s.reason_code ?? ""}>
                        {s.blocked_by ? `blocked:${s.blocked_by}` : (s.reason_code ?? "—")}
                      </td>
                      <td className="tiny">{formatDateTime(s.generated_at)}</td>
                      <td>
                        <button className="btn small ghost" onClick={() => setOpenDecision(String(s.request_id ?? ""))}>
                          drilldown
                        </button>
                      </td>
                    </tr>
                  ))}
                </DataTable>
                <div style={{ display: "flex", gap: 8, marginTop: 8, alignItems: "center" }}>
                  <button className="btn small" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
                    ← newer
                  </button>
                  <span className="tiny muted">page {page}</span>
                  <button className="btn small" disabled={!historyQ.data?.has_more} onClick={() => setPage((p) => p + 1)}>
                    older →
                  </button>
                  <span className="tiny faint" style={{ marginInlineStart: "auto" }}>
                    {historyQ.data?.page_size} rows/page · hours_back cap 720 (backend-enforced)
                  </span>
                </div>
              </>
            )}
          </Panel>
        </div>
      )}

      {tab === "indicators" && (
        <div style={{ display: "grid", gap: 12 }}>
          <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
            <span className="tiny muted">timeframe</span>
            {TFS.map((t) => (
              <button key={t} className={`btn small ${tf === t ? "primary" : "ghost"}`} onClick={() => setTf(t)}>
                {t}
              </button>
            ))}
            <FreshnessCaption timestamp={null} source={`${indSummaryQ.data?.symbol ?? ""} · ${String(indSummaryQ.data?.bar_count ?? 0)} bars`} isFetching={indSummaryQ.isFetching} error={indSummaryQ.isError} />
          </div>
          {indSummaryQ.isPending ? (
            <Skeleton count={4} />
          ) : indSummaryQ.isError ? (
            <EmptyState
              message={indSummaryQ.error instanceof Error ? indSummaryQ.error.message : "indicators unavailable"}
              hint="ENGINE_UNAVAILABLE is a legitimate answer — no synthetic bars are ever generated."
            />
          ) : (
            <>
              <div className="grid cols-4">
                <MetricCard label="last close" value={formatPrice(indSummaryQ.data?.last_close, 3)} sub={`${indSummaryQ.data?.symbol ?? "—"} ${tf} · ${String(indSummaryQ.data?.bar_count ?? 0)} bars`} />
                <MetricCard
                  label="verdicts"
                  value={
                    indSummaryQ.data?.summary
                      ? `${indSummaryQ.data.summary.Buy ?? 0}B / ${indSummaryQ.data.summary.Neutral ?? 0}N / ${indSummaryQ.data.summary.Sell ?? 0}S`
                      : "—"
                  }
                  tone="dim"
                  sub="gauge summary counts (backend)"
                />
                {Object.entries(indSummaryQ.data?.gauges ?? {})
                  .filter(([, g]) => typeof g === "object" && g !== null && (g as { label?: string }).label !== undefined)
                  .slice(0, 2)
                  .map(([k, g]) => (
                    <MetricCard
                      key={k}
                      label={`gauge · ${k}`}
                      value={<StatusPill status={g.label ?? "UNKNOWN"} />}
                      sub={`sell ${formatNumber(g.sell ?? null, 1)} · neutral ${formatNumber(g.neutral ?? null, 1)} · buy ${formatNumber(g.buy ?? null, 1)} · ${formatNumber(g.angle_deg ?? null, 0)}°`}
                    />
                  ))}
              </div>
              <Panel title="Gauge wall (all backend gauges)" tight>
                <div style={{ display: "grid", gap: 6, gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))" }}>
                  {Object.entries(indSummaryQ.data?.gauges ?? {}).map(([k, g]) => (
                    <div key={k} style={{ border: "1px solid var(--border)", borderRadius: 6, padding: "6px 8px" }}>
                      <div className="tiny muted">{k}</div>
                      <div style={{ display: "flex", gap: 6, alignItems: "baseline" }}>
                        <StatusPill status={g.label ?? "UNKNOWN"} />
                        <span className="num tiny inline-mono">
                          {formatNumber(g.sell ?? null, 1)} / {formatNumber(g.neutral ?? null, 1)} / {formatNumber(g.buy ?? null, 1)}
                        </span>
                      </div>
                    </div>
                  ))}
                  {Object.keys(indSummaryQ.data?.gauges ?? {}).length === 0 && <EmptyState message="no gauges returned" />}
                </div>
              </Panel>
              <div className="grid cols-2">
                <IndicatorWallPanel title="Oscillators" query={indOscQ} />
                <IndicatorWallPanel title="Moving averages" query={indMaQ} />
              </div>
              <Panel title="Pivots (Classic / Fibonacci / Camarilla / Woodie / DM)" tight>
                {indPivotQ.isPending ? (
                  <Skeleton count={2} />
                ) : indPivotQ.isError || !indPivotQ.data?.pivots ? (
                  <EmptyState message="pivot matrix not returned" />
                ) : (
                  <DataTable headers={[{ label: "level" }, ...(indPivotQ.data.pivots.columns ?? []).map((c) => ({ label: c })) ]}>
                    {(indPivotQ.data.pivots.rows ?? []).slice(0, 20).map((r, i) => (
                      <tr key={i}>
                        <td className="small">{str(r.name) ?? str(r.label) ?? `row ${i}`}</td>
                        {(indPivotQ.data!.pivots!.columns ?? []).map((c) => (
                          <td key={c} className="num tiny">
                            {formatNumber(typeof r[c] === "number" ? (r[c] as number) : NaN, 2)}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </DataTable>
                )}
              </Panel>
            </>
          )}
        </div>
      )}

      {tab === "shadow" && (
        <Panel title="Shadow 70D observer (health · disagreements · drift)" right={<FreshnessCaption timestamp={shadowQ.data?.generated_at} isFetching={shadowQ.isFetching} error={shadowQ.isError} />} tight>
          {shadowQ.isPending ? (
            <Skeleton count={3} />
          ) : shadowQ.isError ? (
            <EmptyState message={shadowQ.error instanceof Error ? shadowQ.error.message : "shadow store unavailable"} />
          ) : (
            <div className="grid cols-2">
              <div>
                <div className="section-title">summary</div>
                <dl className="kv">
                  {Object.entries(obj(shadowQ.data?.summary)).slice(0, 14).map(([k, v]) => (
                    <InfoRow key={k} label={k} value={typeof v === "object" ? "…" : String(v)} />
                  ))}
                </dl>
                <div className="section-title" style={{ marginTop: 10 }}>
                  disagreement counts
                </div>
                <GateStepper
                  gates={Object.entries(obj(shadowQ.data?.disagreement_counts)).map(([k, v]) => ({ name: k, status: "INFO", reason: String(v) }))}
                />
              </div>
              <div>
                <div className="section-title">drift alerts (latest 25)</div>
                {(shadowQ.data?.drift_alerts ?? []).length === 0 ? (
                  <EmptyState message="No drift alerts recorded." />
                ) : (
                  <DataTable headers={[{ label: "feature" }, { label: "kind" }, { label: "value", num: true }, { label: "at" }]}>
                    {(shadowQ.data?.drift_alerts ?? []).slice(0, 12).map((a, i) => (
                      <tr key={i}>
                        <td className="tiny">{str(a.feature) ?? str(a.name) ?? "—"}</td>
                        <td className="tiny">{str(a.kind) ?? str(a.alert_type) ?? "—"}</td>
                        <td className="num tiny">{formatNumber(Number(a.value ?? a.score ?? NaN), 3)}</td>
                        <td className="tiny">{formatDateTime(str(a.created_at) ?? str(a.detected_at))}</td>
                      </tr>
                    ))}
                  </DataTable>
                )}
              </div>
            </div>
          )}
        </Panel>
      )}

      {openDecision && <DecisionDrawer decisionId={openDecision} onClose={() => setOpenDecision(null)} />}
    </div>
  );
}

/** Compact indicator-card wall (name/value/action rows from the backend). */
function IndicatorWallPanel({
  title,
  query,
}: {
  title: string;
  query: {
    isPending: boolean;
    isError: boolean;
    error: unknown;
    data?: { oscillators?: unknown; moving_averages?: unknown };
  };
}) {
  const rows = (query.data?.oscillators ?? query.data?.moving_averages ?? []) as Array<{
    name?: string;
    value?: number | null;
    action?: string;
  }>;
  return (
    <Panel title={title} tight>
      {query.isPending ? (
        <Skeleton count={3} />
      ) : query.isError ? (
        <EmptyState message={query.error instanceof Error ? query.error.message : "endpoint failed"} />
      ) : rows.length === 0 ? (
        <EmptyState message="no rows returned" />
      ) : (
        <div style={{ display: "grid", gap: 4 }}>
          {rows.map((r, i) => (
            <div key={i} style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
              <span className="small" style={{ minWidth: 90 }}>
                {r.name ?? "—"}
              </span>
              <span className="num inline-mono small" style={{ minWidth: 80, textAlign: "end" }}>
                {r.value === null || r.value === undefined ? "—" : formatNumber(r.value, 3)}
              </span>
              <StatusBadge status={r.action ?? "UNKNOWN"} />
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}
