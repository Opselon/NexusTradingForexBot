/**
 * AI Analysis — signals, decisions, indicators, 70D shadow, intel (legacy parity).
 *
 * Sections: hero live-signal card (action + radial confidence gauge +
 * entry/SL/TP price ladder with R:R), KPI strip + charts (action donut,
 * confidence timeline, stage/reason bars), signal history (page through v1),
 * indicators console (ui/IndicatorsConsole.tsx), shadow-70D envelope + deep
 * panel, intel hub (orphan panels, now wired). Per-decision drilldown opens a
 * drawer wired to gates/evidence/explanation.
 * No signal/decision is ever synthesized: RESOURCE_NOT_FOUND renders empty.
 * Charts are presentation-only derivations (vizMath.ts): backend counts,
 * backend rows, backend-supplied levels; dropped rows are captioned, never hidden.
 */

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { DataTable, EmptyState, ErrorState, MetricCard, Panel, ProbBar, Segmented, Skeleton } from "@/components/primitives";
import { ConfidenceGauge } from "@/components/viz";
import type { ShellPageProps } from "@/app/featureModule";
import { ApiError } from "@/types/api";
import { formatDateTime, formatNumber } from "@/lib/format";
import { FreshnessCaption, GateStepper, InfoRow } from "../../research/ui/lane5Kit";
import { actionTone, confidence01, obj, str, type SignalDto } from "../model";
import { aiAnalysisQueries, orderHistory } from "../useCases";
import { actionFamily, actionKpi, countRows, topEntry } from "./vizMath";
import { AaActionChip, ActionDonut, BarList, ConfidenceTimeline, ConfCell, PriceLadder, historyTimeline } from "./aaCharts";
import DecisionDrawer from "./DecisionDrawer";
import IndicatorsConsole from "./IndicatorsConsole";
import { IntelligenceTelemetryPanel } from "./IntelligenceTelemetryPanel";
import { PositionTimelineLookup } from "./PositionTimelineLookup";
import { Shadow70DeepPanel } from "./Shadow70DeepPanel";
import "./aiAnalysis.css";

type Tab = "signals" | "indicators" | "shadow" | "intel";

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
    refetchInterval: 60_000,
  });
  const statsQ = useQuery({
    queryKey: ["ai-analysis", "decision-stats", hoursBack],
    queryFn: ({ signal }) => aiAnalysisQueries.decisionStats(hoursBack, signal),
    retry: false,
    enabled: tab === "signals",
    refetchInterval: 60_000,
  });
  const reasonsQ = useQuery({
    queryKey: ["ai-analysis", "no-trade-reasons"],
    queryFn: ({ signal }) => aiAnalysisQueries.noTradeReasons(signal),
    retry: false,
    enabled: tab === "signals",
    refetchInterval: 60_000,
  });
  const shadowQ = useQuery({
    queryKey: ["ai-analysis", "shadow70d"],
    queryFn: ({ signal }) => aiAnalysisQueries.shadow70d(signal),
    retry: false,
    enabled: tab === "shadow",
  });

  const isNotFound = (e: unknown) => e instanceof ApiError && (e.status === 404 || e.code === "RESOURCE_NOT_FOUND");
  const latest = latestQ.data;
  const latestFamily = actionFamily(latest?.action);

  // Timeline series: page rows → normalized confidence (model rule) → sorted.
  const timeline = useMemo(
    () =>
      historyTimeline(
        (historyQ.data?.items ?? []).map((s) => ({
          generated_at: s.generated_at,
          action: s.action,
          conf01: confidence01(s.confidence),
        })),
      ),
    [historyQ.data?.items],
  );

  // vizMath/countRows/orderHistory call-site memoization (contract 2.3: pure
  // .ts model functions get their memo WHERE the component re-runs them).
  // Every dep is exactly the payload slice the derivation reads; the rendered
  // values are byte-for-byte what the inline calls produced at base.
  const statsKpi = useMemo(() => actionKpi(statsQ.data?.by_action), [statsQ.data]);
  const statsTop = useMemo(() => topEntry(statsQ.data?.by_stage), [statsQ.data]);
  const stageRows = useMemo(() => countRows(statsQ.data?.by_stage), [statsQ.data]);
  const reasonRows = useMemo(() => countRows(reasonsQ.data?.reasons), [reasonsQ.data]);
  const historyRows = useMemo(() => orderHistory(historyQ.data?.items ?? []), [historyQ.data?.items]);
  // Shadow tab: Object.entries/slice chains over payload maps, same optional-
  // chain deps shape the base timeline memo uses.
  const shadowSummaryEntries = useMemo(
    () => Object.entries(obj(shadowQ.data?.summary)).slice(0, 14),
    [shadowQ.data?.summary],
  );
  const shadowGates = useMemo(
    () =>
      Object.entries(obj(shadowQ.data?.disagreement_counts)).map(([k, v]) => ({
        name: k,
        status: "INFO",
        reason: String(v),
      })),
    [shadowQ.data?.disagreement_counts],
  );
  const shadowAlerts = useMemo(
    () => (shadowQ.data?.drift_alerts ?? []).slice(0, 12),
    [shadowQ.data?.drift_alerts],
  );

  return (
    <div>
      <div className="page-head" style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <h2>AI Analysis</h2>
        <span className="muted small">model signals · decision gates · indicators · shadow 70D · intel</span>
        <FreshnessCaption timestamp={str(latest?.generated_at)} source="audit_signals ledger" isFetching={latestQ.isFetching} error={latestQ.isError && !isNotFound(latestQ.error)} />
      </div>

      <Panel title="Latest signal (live card)" accent tight>
        {latestQ.isPending ? (
          <Skeleton count={2} />
        ) : isNotFound(latestQ.error) ? (
          <EmptyState
            message="No signals recorded yet."
            hint="/api/v1/signals/latest answers RESOURCE_NOT_FOUND — the engine has not emitted a decision."
          />
        ) : latestQ.isError ? (
          <ErrorState
            message={(latestQ.error as Error).message}
            requestId={latestQ.error instanceof ApiError ? latestQ.error.requestId : null}
            onRetry={() => void latestQ.refetch()}
          />
        ) : latest ? (
          <div className={`panel-body aa-hero aa-fam-${latestFamily}`}>
            <div className="aa-hero-act">
              <div className="aa-big">{latest.action ?? "—"}</div>
              <div className="aa-when">{formatDateTime(latest.generated_at)}</div>
              <button className="btn small primary" onClick={() => setOpenDecision(String(latest.request_id ?? ""))}>
                inspect decision
              </button>
              <span className="tiny muted">gates · evidence · explanation</span>
            </div>

            <div className="aa-gauge">
              <ConfidenceGauge value={confidence01(latest.confidence)} label="confidence" />
            </div>

            <div className="aa-details">
              <div className="aa-meta">
                <b className="aa-symbol">{latest.symbol ?? "—"}</b>
                {latest.regime ? <span className="aa-chip aa-fam-unknown">{latest.regime}</span> : <span className="tiny faint">regime not recorded</span>}
                {latest.execution_mode ? <span className="aa-chip aa-fam-unknown">{latest.execution_mode}</span> : null}
                <span className="aa-chip aa-fam-unknown">{latest.decision_stage ?? "stage not recorded"}</span>
              </div>
              <ProbBar
                rows={[
                  { label: "confidence", value: confidence01(latest.confidence), tone: actionTone(latest.action) },
                  { label: "before filters", value: confidence01(latest.confidence_before_filters), tone: "flat" },
                  { label: "after filters", value: confidence01(latest.confidence_after_filters), tone: "flat" },
                ]}
              />
              <dl className="kv" style={{ marginTop: 2 }}>
                <InfoRow label="htf / smc score" value={`${formatNumber(latest.htf_score ?? null, 3)} / ${formatNumber(latest.smc_score ?? null, 3)}`} />
                <InfoRow label="blocked_by" value={latest.blocked_by ?? "—"} />
              </dl>
              <div className="aa-reason-line" title={latest.reason_code ?? ""}>
                reason: {latest.reason_code ?? "—"}
              </div>
            </div>

            <div className="aa-hero-ladder">
              <PriceLadder entry={latest.proposed_entry ?? null} sl={latest.stop_loss ?? null} tp={latest.take_profit ?? null} />
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
            { id: "intel" as const, label: "Intel & timeline" },
          ]}
          value={tab}
          onChange={setTab}
        />
      </div>

      {tab === "signals" && (
        <div className="aa-stack">
          <div className="aa-segbar">
            <span className="section-title" style={{ margin: 0 }}>
              decision stats
            </span>
            <span className="tiny faint">window</span>
            <select
              aria-label="Stats window (hours)"
              className="select"
              style={{ width: 92 }}
              value={hoursBack}
              onChange={(e) => {
                setHoursBack(Number(e.target.value));
                setPage(1);
              }}
            >
              {[24, 72, 168, 720].map((h) => (
                <option key={h} value={h}>
                  {h}h
                </option>
              ))}
            </select>
          </div>

          {statsQ.isPending ? (
            <div className="grid cols-4">
              <Skeleton count={4} />
            </div>
          ) : statsQ.isError ? (
            <EmptyState message={statsQ.error instanceof Error ? statsQ.error.message : "stats unavailable"} />
          ) : (
            (() => {
              const st = statsQ.data;
              const kpi = statsKpi;
              const top = statsTop;
              const pct = (n: number) => (kpi.total > 0 ? `${((n / kpi.total) * 100).toFixed(1)}%` : "—");
              return (
                <>
                  <div className="grid cols-4">
                    <MetricCard label="decisions in window" value={kpi.total.toLocaleString()} sub={`/decisions/stats · ${st?.window_hours ?? hoursBack}h scanned`} />
                    <MetricCard label="trade actions" value={kpi.trade.toLocaleString()} sub={`${pct(kpi.trade)} of decisions · non-NO_TRADE`} />
                    <MetricCard label="no-trade" value={kpi.noTrade.toLocaleString()} sub={`${pct(kpi.noTrade)} of decisions · filter blocks`} />
                    <MetricCard
                      label="top stage"
                      value={<span style={{ fontSize: 13, fontWeight: 700 }}>{top?.label ?? "—"}</span>}
                      sub={top ? `${top.count.toLocaleString()} · ${pct(top.count)}` : "no stage rows"}
                    />
                  </div>

                  <div className="grid cols-2">
                    <Panel title="Decision mix (by action)" tight>
                      <div className="panel-body">
                        <ActionDonut byAction={st?.by_action} />
                      </div>
                    </Panel>
                    <Panel title="NO_TRADE reasons" right={<span className="tiny faint">{(reasonsQ.data?.total ?? 0).toLocaleString()} in ledger</span>} tight>
                      <div className="panel-body">
                        <BarList rows={reasonRows} tone="var(--amber)" max={8} />
                      </div>
                    </Panel>
                  </div>

                  <div className="grid cols-2">
                    <Panel title="Decision stage distribution" right={<span className="tiny faint">sorted by count — backend returns no stage order</span>} tight>
                      <div className="panel-body">
                        <BarList rows={stageRows} tone="var(--violet)" max={20} />
                      </div>
                    </Panel>
                    <Panel
                      title="Confidence over time"
                      right={<span className="tiny faint">{historyQ.isPending ? "loading…" : `current history page · ${historyQ.data?.page_size ?? "—"} rows max`}</span>}
                      tight
                    >
                      <div className="panel-body">
                        <ConfidenceTimeline series={timeline} />
                      </div>
                    </Panel>
                  </div>
                </>
              );
            })()
          )}

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
                  {historyRows.map((s: SignalDto, i: number) => (
                    <tr key={s.request_id ?? i}>
                      <td className="inline-mono tiny">{str(s.request_id)?.slice(0, 10) ?? "—"}</td>
                      <td className="small">{s.symbol}</td>
                      <td>
                        <AaActionChip action={s.action} />
                      </td>
                      <td className="num">
                        <ConfCell value={confidence01(s.confidence)} action={s.action} />
                      </td>
                      <td className="tiny">{s.decision_stage ?? "—"}</td>
                      <td className="tiny muted aa-reason" title={s.reason_code ?? ""}>
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
        <div className="aa-stack">
          <Panel
            title="Shadow 70D observer (health · disagreements · drift)"
            right={<FreshnessCaption timestamp={shadowQ.data?.generated_at} isFetching={shadowQ.isFetching} error={shadowQ.isError} />}
            tight
          >
            {shadowQ.isPending ? (
              <Skeleton count={3} />
            ) : shadowQ.isError ? (
              <EmptyState message={shadowQ.error instanceof Error ? shadowQ.error.message : "shadow store unavailable"} />
            ) : (
              <div className="grid cols-2">
                <div>
                  <div className="section-title">summary</div>
                  <dl className="kv">
                    {shadowSummaryEntries.map(([k, v]) => (
                        <InfoRow key={k} label={k} value={typeof v === "object" ? "…" : String(v)} />
                      ))}
                  </dl>
                  <div className="section-title" style={{ marginTop: 10 }}>
                    disagreement counts
                  </div>
                  <GateStepper gates={shadowGates} />
                </div>
                <div>
                  <div className="section-title">drift alerts (latest 25)</div>
                  {(shadowQ.data?.drift_alerts ?? []).length === 0 ? (
                    <EmptyState message="No drift alerts recorded." />
                  ) : (
                    <DataTable headers={[{ label: "feature" }, { label: "kind" }, { label: "value", num: true }, { label: "at" }]}>
                      {shadowAlerts.map((a, i) => (
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

          {/* v1 deep panel: legacy envelope above + health/disagreements/alerts below. */}
          <Shadow70DeepPanel />
        </div>
      )}

      {tab === "intel" && (
        <div className="aa-stack">
          <div className="tiny faint">behaviour coverage — legacy tab-ai-analysis also loads intelligence centre + position timeline (orphaned components, now wired).</div>
          <IntelligenceTelemetryPanel />
          <PositionTimelineLookup />
        </div>
      )}

      {openDecision && <DecisionDrawer decisionId={openDecision} onClose={() => setOpenDecision(null)} />}
    </div>
  );
}
