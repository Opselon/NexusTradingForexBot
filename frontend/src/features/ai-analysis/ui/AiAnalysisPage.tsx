/**
 * AI Analysis — signals, decisions, indicators, 70D shadow (legacy tab parity).
 *
 * Sections: live signal card · decision stats + NO_TRADE reason distribution ·
 * signal history (page through v1) · indicators console (gauges, distribution
 * bar, tables, pivot matrix — ui/IndicatorsConsole.tsx, full-snapshot feed) ·
 * shadow-70d panel. Per-decision drilldown opens a drawer wired to the
 * gates/evidence/explanation endpoints.
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
import { DistBars, FreshnessCaption, GateStepper, InfoRow } from "../../research/ui/lane5Kit";
import { actionTone, confidence01, distRows, obj, str, type SignalDto } from "../model";
import { aiAnalysisQueries, orderHistory } from "../useCases";
import DecisionDrawer from "./DecisionDrawer";
import IndicatorsConsole from "./IndicatorsConsole";

type Tab = "signals" | "indicators" | "shadow";

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
                <select aria-label="Stats window (hours)" className="select" style={{ width: 110 }} value={hoursBack} onChange={(e) => setHoursBack(Number(e.target.value))}>
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

      {tab === "indicators" && <IndicatorsConsole tf={tf} onTf={setTf} />}

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
