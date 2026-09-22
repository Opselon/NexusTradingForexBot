/**
 * Control Center — operator console (legacy tab-control-center parity).
 *
 * Screens: OVERVIEW (runtime truth + identity + warnings + kill-switch style
 * engine control) · DECISIONS (observatory + drilldown) · FUNNEL (terminal
 * distributions, labeled as such by the backend) · NO_TRADE forensics ·
 * ORDERS (dispatch evidence + latency) · CALIBRATION.
 *
 * Truth rules: a value the backend did not supply renders NOT RECORDED;
 * engine commands never locally change state — the next authoritative
 * snapshot carries the result; LIVE mode switch needs typed confirmation.
 */

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { ShellPageProps } from "@/app/featureModule";
import {
  ConfirmModal,
  DataTable,
  EmptyState,
  ErrorState,
  MetricCard,
  Panel,
  Segmented,
  SeverityBadge,
  Skeleton,
  StatusBadge,
} from "@/components/primitives";
import { useMutationFeedback } from "@/hooks/useMutationFeedback";
import { formatDateTime, formatNumber, formatPrice, formatAgeMs } from "@/lib/format";
import { CommandResultLine, DistBars, Drawer, FreshnessCaption, InfoRow, JsonBlock, StatusPill } from "../../research/ui/lane5Kit";
import { arr, bool, notRecorded, num, obj, str, type OperatorDecisionRow } from "../model";
import { controlCenterQueries, controlCenterUseCases } from "../useCases";
import { useI18n } from "@/stores/i18nStore";

type Tab = "overview" | "decisions" | "funnel" | "no-trade" | "orders" | "calibration";
const LIVE_CONFIRM_TEXT = "LIVE";

export default function ControlCenterPage(props: ShellPageProps) {
  void props;
  const t = useI18n((s) => s.t);
  /** model.notRecorded sentinel — rendered translated (data itself untouched). */
  const nr = (v: string | number | null | undefined): string => {
    const s = notRecorded(v);
    return s === "NOT RECORDED" ? t("control-center.truth.not_recorded", "NOT RECORDED") : s;
  };
  const [tab, setTab] = useState<Tab>("overview");
  const [hours, setHours] = useState<number | undefined>(72);
  const [actionFilter, setActionFilter] = useState("");
  const [search, setSearch] = useState("");
  const [detailId, setDetailId] = useState<number | null>(null);
  const [stopConfirm, setStopConfirm] = useState(false);
  const [startConfirm, setStartConfirm] = useState(false);
  const [modeTarget, setModeTarget] = useState("");
  const [liveConfirm, setLiveConfirm] = useState("");
  const engineCmd = useMutationFeedback();
  const modeCmd = useMutationFeedback();
  const qc = useQueryClient();

  const summaryQ = useQuery({
    queryKey: ["control-center", "summary"],
    queryFn: ({ signal }) => controlCenterQueries.summary(signal),
    refetchInterval: 15_000,
    retry: false,
  });
  const decisionsQ = useQuery({
    queryKey: ["control-center", "decisions", hours, actionFilter, search],
    queryFn: ({ signal }) => controlCenterQueries.decisions({ hours, action: actionFilter || undefined, search: search || undefined, limit: 100 }, signal),
    retry: false,
    enabled: tab === "decisions",
  });
  const funnelQ = useQuery({
    queryKey: ["control-center", "funnel", hours],
    queryFn: ({ signal }) => controlCenterQueries.funnel(hours, signal),
    retry: false,
    enabled: tab === "funnel",
  });
  const noTradeQ = useQuery({
    queryKey: ["control-center", "no-trade", hours],
    queryFn: ({ signal }) => controlCenterQueries.noTrade(hours, 8, signal),
    retry: false,
    enabled: tab === "no-trade",
  });
  const ordersQ = useQuery({
    queryKey: ["control-center", "orders"],
    queryFn: ({ signal }) => controlCenterQueries.orders(signal),
    refetchInterval: 30_000,
    retry: false,
    enabled: tab === "orders",
  });
  const calibrationQ = useQuery({
    queryKey: ["control-center", "calibration"],
    queryFn: ({ signal }) => controlCenterQueries.calibration(signal),
    retry: false,
    enabled: tab === "calibration",
  });

  const s = summaryQ.data;
  const rt = obj(s?.runtime);
  const idt = obj(s?.identity);
  const health = obj(rt.health);
  const subs = obj(health.subsystems);
  const running = bool(rt.engine_running) ?? false;
  const mode = String(rt.runtime_mode ?? rt.execution_mode ?? "UNKNOWN").toUpperCase();
  const isLive = mode.startsWith("LIVE");

  const refreshAll = () => {
    void qc.invalidateQueries({ queryKey: ["control-center"] });
    void qc.invalidateQueries({ queryKey: ["engine-snapshot"] });
  };

  return (
    <div>
      <div className="page-head" style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <h2>{t("nav.feature.control-center", "Control Center")}</h2>
        <span className="muted small">{t("control-center.page.subtitle", "operator evidence console (read-only ledger views + guarded engine control)")}</span>
        <span className={`badge ${isLive ? "bad" : "good"}`} title={t("control-center.a11y.mode_banner", "mode banner")}>{mode}</span>
        <FreshnessCaption timestamp={str(rt.snapshot_timestamp)} source="operator/summary" isFetching={summaryQ.isFetching} error={summaryQ.isError} />
      </div>

      {isLive && (
        <div className="banner down" role="alert">
          {t("control-center.banner.live", "LIVE mode dispatches real orders to the broker. Verify risk state before any operator action.")}
        </div>
      )}

      <Segmented
        options={[
          { id: "overview" as const, label: t("control-center.tab.overview", "Overview") },
          { id: "decisions" as const, label: t("control-center.tab.decisions", "Decisions") },
          { id: "funnel" as const, label: t("control-center.tab.funnel", "Funnel") },
          { id: "no-trade" as const, label: t("control-center.tab.no_trade", "NO_TRADE") },
          { id: "orders" as const, label: t("control-center.tab.orders", "Orders") },
          { id: "calibration" as const, label: t("control-center.tab.calibration", "Calibration") },
        ]}
        value={tab}
        onChange={setTab}
      />

      <div style={{ marginTop: 12, display: "grid", gap: 12 }}>
        {tab === "overview" && (
          <>
            <div className="grid cols-4">
              <MetricCard label={t("control-center.kpi.runtime", "Runtime")} value={<StatusBadge status={running ? "RUNNING" : "STOPPED"} />} sub={str(obj(health.details).engine) ?? undefined} />
              <MetricCard
                label={t("control-center.kpi.data_tick", "Data (tick)")}
                value={<StatusBadge status={bool(rt.tick_stale) ? "STALE" : rt.tick_freshness_ms !== undefined && rt.tick_freshness_ms !== null ? "READY" : "UNKNOWN"} />}
                sub={num(rt.tick_freshness_ms) === null ? t("control-center.kpi.freshness_not_recorded", "freshness NOT RECORDED") : t("control-center.kpi.tick_age", "tick age {age}", { age: formatAgeMs(num(rt.tick_freshness_ms)) })}
              />
              <MetricCard label={t("control-center.kpi.model", "Model")} value={<StatusBadge status={str(subs.model)} />} sub={str(obj(health.details).model) ?? undefined} />
              <MetricCard label={t("control-center.kpi.db_mt5", "Database / MT5")} value={<StatusBadge status={str(subs.database)} />} sub={t("control-center.kpi.mt5", "mt5: {v}", { v: str(subs.mt5) ?? "—" })} />
            </div>
            <div className="grid cols-2">
              <Panel title={t("control-center.panel.identity", "Runtime identity (release snapshot)")} tight>
                <dl className="kv">
                  <InfoRow label={t("control-center.label.version", "version")} value={nr(str(idt.version))} />
                  <InfoRow label={t("control-center.label.commit", "commit")} value={nr(`${str(idt.commit) ?? ""}${str(idt.commit_status) ? ` (${str(idt.commit_status)})` : ""}`)} />
                  <InfoRow label={t("control-center.label.channel", "channel")} value={nr(str(idt.channel))} />
                  <InfoRow label={t("control-center.label.symbol_regime", "symbol / regime")} value={`${nr(str(rt.symbol))} / ${nr(str(rt.regime))}`} />
                  <InfoRow label={t("control-center.label.bid_ask_spread", "bid / ask / spread")} value={`${formatPrice(num(rt.bid), 2)} / ${formatPrice(num(rt.ask), 2)} / ${formatPrice(num(rt.spread), 2)}`} />
                  <InfoRow label="provenance.price" value={str(obj(rt.provenance).price) ?? t("control-center.truth.not_recorded", "NOT RECORDED")} />
                </dl>
              </Panel>
              <Panel title={t("control-center.panel.ledger_stats", "Ledger stats (bounded recent window)")} tight>
                {s?.ledger?.available === false ? (
                  <EmptyState message={t("control-center.empty.ledger_unavailable", "ledger unavailable")} hint={s.ledger.reason ?? "LEDGER_UNAVAILABLE"} />
                ) : (
                  <>
                    <dl className="kv" style={{ marginBottom: 8 }}>
                      <InfoRow label={t("control-center.label.scanned_rows", "scanned rows")} value={String(s?.ledger?.scanned_rows ?? "—")} />
                      <InfoRow label={t("control-center.label.latest_decision", "latest decision")} value={formatDateTime(s?.ledger?.latest_decision_at)} />
                    </dl>
                    <DistBars rows={Object.entries(obj(s?.ledger?.actions)).map(([k, v]) => ({ label: k, count: num(v) ?? 0 }))} />
                  </>
                )}
              </Panel>
            </div>
            <Panel title={t("control-center.panel.warnings", "Warnings (ledger-visible, backend-derived)")} tight>
              {arr(s?.warnings).length === 0 ? (
                <EmptyState message={t("control-center.empty.no_warnings", "No warnings reported.")} />
              ) : (
                <div style={{ display: "grid", gap: 8 }}>
                  {arr(s?.warnings).map((w, i) => (
                    <div key={i} style={{ borderInlineStart: "2px solid var(--amber)", paddingInlineStart: 8 }}>
                      <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
                        <SeverityBadge severity={str(w.severity)} />
                        <span className="small">{str(w.what)}</span>
                      </div>
                      <div className="tiny muted">{t("control-center.warn.why", "why")}: {str(w.why) ?? "—"} · {t("control-center.warn.impact", "impact")}: {str(w.impact) ?? "—"} · {t("control-center.warn.todo", "do")}: {str(w.what_to_do) ?? "—"}</div>
                    </div>
                  ))}
                </div>
              )}
            </Panel>
            <Panel title={t("control-center.panel.engine_control", "Engine control (guarded)")} accent tight>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                <button className="btn primary" disabled={engineCmd.state.running || running} onClick={() => setStartConfirm(true)}>
                  {t("control-center.action.start_engine", "▶ Start engine")}
                </button>
                <button className="btn danger" disabled={engineCmd.state.running || !running} onClick={() => setStopConfirm(true)}>
                  {t("control-center.action.stop_engine", "■ Stop engine (kill switch)")}
                </button>
                <select className="select" style={{ width: 130 }} value={modeTarget} onChange={(e) => setModeTarget(e.target.value)}>
                  <option value="">{t("control-center.action.mode_switch", "mode switch…")}</option>
                  {["PAPER", "LIVE", "SHADOW"].map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
                <button
                  className={`btn ${modeTarget === "LIVE" ? "danger" : ""}`}
                  disabled={!modeTarget || modeCmd.state.running || modeTarget.toUpperCase() === mode}
                  onClick={() => void applyMode()}
                >
                  {t("control-center.action.apply", "apply")}
                </button>
              </div>
              <div className="tiny muted" style={{ marginTop: 6 }}>
                {t("control-center.panel.engine_commands_note", "Commands go to /api/engine/toggle + /api/engine/mode (BUG-148 hot-swap path). The UI never shows a locally-changed state — the next authoritative snapshot decides.")}
              </div>
              <CommandResultLine state={engineCmd.state} />
              <CommandResultLine state={modeCmd.state} />
            </Panel>
          </>
        )}

        {tab === "decisions" && (
          <Panel
            title={t("control-center.panel.decisions", "Decision observatory (audit_signals, read-only)")}
            right={
              <div style={{ display: "flex", gap: 6 }}>
                <input className="input" style={{ width: 150 }} placeholder={t("control-center.action.search", "search")} value={search} onChange={(e) => setSearch(e.target.value)} />
                <select className="select" style={{ width: 120 }} value={actionFilter} onChange={(e) => setActionFilter(e.target.value)}>
                  <option value="">{t("control-center.filter.action_any", "action: any")}</option>
                  {["BUY", "SELL", "NO_TRADE"].map((a) => (
                    <option key={a} value={a}>
                      {a}
                    </option>
                  ))}
                </select>
                <select className="select" style={{ width: 96 }} value={String(hours ?? 72)} onChange={(e) => setHours(Number(e.target.value))}>
                  {[1, 24, 72, 168].map((h) => (
                    <option key={h} value={h}>
                      {h}h
                    </option>
                  ))}
                </select>
              </div>
            }
            tight
          >
            {decisionsQ.isPending ? (
              <Skeleton count={5} />
            ) : decisionsQ.isError ? (
              <ErrorState message={decisionsQ.error instanceof Error ? decisionsQ.error.message : t("control-center.err.decisions", "decisions failed")} onRetry={() => void decisionsQ.refetch()} />
            ) : decisionsQ.data?.available === false ? (
              <EmptyState message={t("control-center.empty.ledger_unavailable", "ledger unavailable")} />
            ) : (decisionsQ.data?.rows ?? []).length === 0 ? (
              <EmptyState message={t("control-center.empty.no_decisions", "No decisions match the filters.")} />
            ) : (
              <DataTable
                headers={[
                  { label: t("control-center.th.id", "id"), num: true },
                  { label: t("control-center.th.symbol", "symbol") },
                  { label: t("control-center.th.action", "action") },
                  { label: t("control-center.th.conf", "conf"), num: true },
                  { label: t("control-center.th.stage", "stage") },
                  { label: t("control-center.th.gate", "gate") },
                  { label: t("control-center.th.reason", "reason") },
                  { label: t("control-center.th.at", "at") },
                  { label: "" },
                ]}
              >
                {(decisionsQ.data?.rows ?? []).map((r: OperatorDecisionRow) => (
                  <tr key={controlCenterUseCases.decisionKey(r)}>
                    <td className="num tiny">{r.id ?? "—"}</td>
                    <td className="small">{r.symbol ?? "—"}</td>
                    <td>
                      <StatusBadge status={r.action} />
                    </td>
                    <td className="num tiny">{r.confidence == null ? t("control-center.truth.not_recorded", "NOT RECORDED") : formatNumber(r.confidence, 3)}</td>
                    <td className="tiny">{r.decision_stage ?? "—"}</td>
                    <td className="tiny">{r.blocked_by ?? ""}</td>
                    <td className="tiny muted" title={r.reason_code ?? ""}>
                      {(r.reason_code ?? "—").slice(0, 20)}
                    </td>
                    <td className="tiny">{formatDateTime(r.generated_at)}</td>
                    <td>
                      <button className="btn small ghost" disabled={r.payload_ok === false} onClick={() => setDetailId(num(r.id) ?? null)}>
                        {r.payload_ok === false ? t("control-center.action.payload_bad", "payload ✗") : t("control-center.action.inspect", "inspect")}
                      </button>
                    </td>
                  </tr>
                ))}
              </DataTable>
            )}
            <div className="tiny faint" style={{ marginTop: 6 }}>
              {t("control-center.decisions.footnote", "rows with unparseable payload are kept and flagged (never silently dropped) — inspect disabled for them by the backend contract.")}
            </div>
          </Panel>
        )}

        {tab === "funnel" && (
          <Panel title={t("control-center.panel.funnel", "Terminal-stage funnel")} tight>
            {funnelQ.isPending ? (
              <Skeleton count={4} />
            ) : funnelQ.isError ? (
              <EmptyState message={t("control-center.err.funnel", "funnel endpoint failed")} />
            ) : (
              <>
                <div className="tiny muted" style={{ marginBottom: 8 }}>
                  funnelQ.data?.note ?? t("control-center.empty.terminal_note", "TERMINAL distributions — the ledger records the final blocking stage per decision.")
                </div>
                <div className="grid cols-3">
                  <div>
                    <div className="section-title">{t("control-center.section.actions", "actions")}</div>
                    <DistBars rows={(funnelQ.data?.actions ?? []).map((a) => ({ label: a.action ?? "—", count: a.count ?? 0 }))} tone="var(--green)" />
                  </div>
                  <div>
                    <div className="section-title">{t("control-center.section.stages", "stages")}</div>
                    <DistBars rows={(funnelQ.data?.stages ?? []).map((a) => ({ label: a.stage ?? "—", count: a.count ?? 0 }))} tone="var(--amber)" />
                  </div>
                  <div>
                    <div className="section-title">{t("control-center.section.gates", "gates")}</div>
                    <DistBars rows={(funnelQ.data?.gates ?? []).map((a) => ({ label: a.gate ?? "—", count: a.count ?? 0 }))} tone="var(--red)" />
                  </div>
                </div>
                <div className="tiny faint" style={{ marginTop: 6 }}>
                  {t("control-center.funnel.footnote", "scanned {scanned} rows / total {total} — summary numbers reconcile against the same window.", { scanned: String(funnelQ.data?.scanned_rows ?? "—"), total: String(funnelQ.data?.total ?? "—") })}
                </div>
              </>
            )}
          </Panel>
        )}

        {tab === "no-trade" && (
          <Panel title={t("control-center.panel.no_trade", "NO_TRADE forensics")} tight>
            {noTradeQ.isPending ? (
              <Skeleton count={4} />
            ) : noTradeQ.isError ? (
              <EmptyState message={t("control-center.err.no_trade", "no-trade endpoint failed")} />
            ) : (
              <div className="grid cols-2">
                <div>
                  <div className="section-title">{t("control-center.section.blocking_gates", "blocking gates")}</div>
                  <DistBars rows={(noTradeQ.data?.gates ?? []).map((g) => ({ label: g.gate ?? "—", count: g.count ?? 0 }))} tone="var(--red)" />
                  <div className="section-title" style={{ marginTop: 8 }}>
                    {t("control-center.section.regimes", "regimes")}
                  </div>
                  <DistBars rows={(noTradeQ.data?.regimes ?? []).map((g) => ({ label: g.regime ?? "—", count: g.count ?? 0 }))} />
                </div>
                <div>
                  <div className="section-title">{t("control-center.section.hourly_trend", "hourly trend")}</div>
                  <DistBars rows={(noTradeQ.data?.hourly_trend ?? []).map((g) => ({ label: (g.hour ?? "—").slice(5, 13), count: g.count ?? 0 }))} tone="var(--violet)" />
                  <div className="section-title" style={{ marginTop: 8 }}>
                    {t("control-center.section.recent_examples", "recent examples")}
                  </div>
                  <DataTable headers={[{ label: t("control-center.th.at", "at") }, { label: t("control-center.th.reason", "reason") }, { label: t("control-center.th.gate", "gate") }]}>
                    {arr(noTradeQ.data?.recent).map((r, i) => (
                      <tr key={i}>
                        <td className="tiny">{formatDateTime(str(r.generated_at))}</td>
                        <td className="tiny">{str(r.reason_code) ?? "—"}</td>
                        <td className="tiny">{str(r.blocked_by) ?? ""}</td>
                      </tr>
                    ))}
                  </DataTable>
                  <div className="tiny faint" style={{ marginTop: 4 }}>
                    {t("control-center.nt.unresolved", "model direction unresolved: {n}", { n: String(noTradeQ.data?.model_direction_unresolved ?? "—") })}
                  </div>
                </div>
              </div>
            )}
          </Panel>
        )}

        {tab === "orders" && (
          <Panel title={t("control-center.panel.orders", "Dispatch evidence (audit_orders) + latency")} tight>
            {ordersQ.isPending ? (
              <Skeleton count={5} />
            ) : ordersQ.isError ? (
              <ErrorState message={ordersQ.error instanceof Error ? ordersQ.error.message : t("control-center.err.orders", "orders failed")} onRetry={() => void ordersQ.refetch()} />
            ) : ordersQ.data?.available === false ? (
              <EmptyState message={t("control-center.empty.ledger_unavailable", "ledger unavailable")} />
            ) : (
              <>
                <div style={{ marginBottom: 8 }}>
                  <MetricCard
                    label={t("control-center.kpi.latency", "latency p50 / p95 / p99 (ms)")}
                    value={
                      ordersQ.data?.latency
                        ? `${formatNumber(ordersQ.data.latency.p50_ms ?? NaN, 1)} / ${formatNumber(ordersQ.data.latency.p95_ms ?? NaN, 1)} / ${formatNumber(ordersQ.data.latency.p99_ms ?? NaN, 1)}`
                        : t("control-center.truth.not_recorded", "NOT RECORDED")
                    }
                    sub={t("control-center.kpi.n_rows", "n={n} · {rows} rows", { n: String(ordersQ.data?.latency?.n ?? 0), rows: String(ordersQ.data?.count ?? 0) })}
                  />
                </div>
                <DataTable headers={[{ label: t("control-center.th.ts", "ts") }, { label: t("control-center.th.ticket", "ticket"), num: true }, { label: t("control-center.th.action", "action") }, { label: t("control-center.th.price", "price"), num: true }, { label: t("control-center.th.vol", "vol"), num: true }, { label: t("control-center.th.latency", "latency"), num: true }, { label: t("control-center.th.mode", "mode") }, { label: t("control-center.th.reason", "reason") }]}>
                  {arr(ordersQ.data?.rows).map((o, i) => (
                    <tr key={i}>
                      <td className="tiny">{formatDateTime(str(o.timestamp))}</td>
                      <td className="num tiny">{num(o.ticket) ?? "—"}</td>
                      <td>
                        <StatusPill status={str(o.action)} />
                      </td>
                      <td className="num tiny">{formatPrice(num(o.price), 2)}</td>
                      <td className="num tiny">{num(o.volume) ?? "—"}</td>
                      <td className="num tiny">{num(o.latency) === null ? "—" : `${formatNumber(num(o.latency)!, 0)}ms`}</td>
                      <td className="tiny">{str(o.execution_mode) ?? "—"}</td>
                      <td className="tiny muted">{str(o.reason) ?? ""}</td>
                    </tr>
                  ))}
                </DataTable>
              </>
            )}
          </Panel>
        )}

        {tab === "calibration" && (
          <Panel title={t("control-center.panel.calibration", "Calibration monitor (identity-bound serving model)")} right={<span className="tiny muted">/api/operator/calibration</span>} tight>
            {calibrationQ.isPending ? (
              <Skeleton count={4} />
            ) : calibrationQ.isError ? (
              <EmptyState message={calibrationQ.error instanceof Error ? calibrationQ.error.message : t("control-center.err.calibration", "calibration monitor failed")} />
            ) : calibrationQ.data?.available !== true ? (
              <EmptyState
              message={t("control-center.empty.calibration_unavailable", "calibration monitor not available")}
              hint={t("control-center.empty.calibration_hint", "the endpoint answers available:false when the artifact is missing")}
            />
            ) : (
              <>
                <div className="grid cols-4">
                  <MetricCard label={t("control-center.kpi.calibration", "calibration")} value={<StatusPill status={calibrationQ.data.calibration_status} />} sub={t("control-center.kpi.serving_fp", "serving fp {fp}", { fp: calibrationQ.data.serving_fingerprint ?? "—" })} />
                  <MetricCard label={t("control-center.kpi.artifact", "artifact")} value={calibrationQ.data.artifact_status ?? "—"} tone={calibrationQ.data.artifact_status === "PRESENT" ? "pos" : "neg"} sub={t("control-center.kpi.collector", "collector {v}", { v: calibrationQ.data.collector_status ?? "—" })} />
                  <MetricCard
                    label={t("control-center.kpi.splits", "splits (cal/val)")}
                    value={`${String(calibrationQ.data.calibration_split ?? 0)}/${String(calibrationQ.data.validation_split ?? 0)}`}
                    sub={t("control-center.kpi.required_deficit", "required {required} · deficit {deficit}", { required: String(calibrationQ.data.required_per_split ?? "—"), deficit: String(calibrationQ.data.deficit ?? 0) })}
                  />
                  <MetricCard
                    label={t("control-center.kpi.risk_multiplier", "risk multiplier (probe)")}
                    value={formatNumber(calibrationQ.data.risk_multiplier ?? NaN, 3)}
                    sub={`ece ${formatNumber(calibrationQ.data.ece ?? NaN, 3)} · brier ${formatNumber(calibrationQ.data.brier ?? NaN, 3)}`}
                  />
                </div>
                <JsonBlock value={{ excluded: calibrationQ.data.excluded, oos_cutoff: calibrationQ.data.oos_cutoff, matches_serving: calibrationQ.data.matches_serving }} maxChars={1600} />
              </>
            )}
          </Panel>
        )}
      </div>

      {detailId !== null && <DecisionInspector id={detailId} onClose={() => setDetailId(null)} />}

      {(startConfirm || stopConfirm) && (
        <ConfirmModal
          title={stopConfirm ? t("control-center.confirm.stop_title", "STOP the engine (kill switch)") : t("control-center.confirm.start_title", "Start the engine")}
          danger={stopConfirm}
          confirmLabel={stopConfirm ? t("control-center.confirm.stop", "Stop engine") : t("control-center.confirm.start", "Start engine")}
          busy={engineCmd.state.running}
          onCancel={() => {
            setStopConfirm(false);
            setStartConfirm(false);
          }}
          onConfirm={async () => {
            const stopping = stopConfirm;
            setStopConfirm(false);
            setStartConfirm(false);
            await engineCmd.run(() => controlCenterUseCases.toggleEngine(!stopping));
            refreshAll();
          }}
        >
          <div className="small">
            {stopConfirm
              ? t("control-center.confirm.stop_body", "Stops the engine loop: no new decisions or dispatches. Open positions remain under broker/exits — closing them is a separate explicit action on the Positions page.")
              : t("control-center.confirm.start_body", "Starts the engine loop via the canonical async start path (BUG-239). The backend response decides.")}
          </div>
        </ConfirmModal>
      )}

      {modeTarget === "LIVE" && liveConfirm !== LIVE_CONFIRM_TEXT && (
        <ConfirmModal
          title={t("control-center.live.title", "Switch execution mode to LIVE")}
          danger
          confirmLabel={t("control-center.live.abort", "abort switch")}
          busy={false}
          onCancel={() => setModeTarget("")}
          onConfirm={() => setModeTarget("")}
        >
          <div className="confirm-box">
            <div className="small">{t("control-center.live.type_hint", "LIVE dispatches real orders. Type “{w}” below the button to arm the switch (legacy parity guard).", { w: LIVE_CONFIRM_TEXT })}</div>
            <div className="row">
              <input className="input" style={{ width: 160 }} value={liveConfirm} onChange={(e) => setLiveConfirm(e.target.value)} placeholder={LIVE_CONFIRM_TEXT} />
              <button className="btn small danger" disabled={liveConfirm !== LIVE_CONFIRM_TEXT} onClick={() => void applyMode()}>
                {t("control-center.live.switch", "switch to LIVE")}
              </button>
            </div>
          </div>
        </ConfirmModal>
      )}
    </div>
  );

  async function applyMode(): Promise<void> {
    if (!modeTarget) return;
    if (modeTarget === "LIVE" && liveConfirm !== LIVE_CONFIRM_TEXT) {
      setLiveConfirm("");
      return;
    }
    const ok = await modeCmd.run(() => controlCenterUseCases.setMode(modeTarget));
    if (ok) {
      setModeTarget("");
      setLiveConfirm("");
      refreshAll();
    }
  }
}

/** One-decision inspector: full payload + correlated orders (method disclosed). */
function DecisionInspector({ id, onClose }: { id: number; onClose: () => void }) {
  const t = useI18n((s) => s.t);
  /** model.notRecorded sentinel — rendered translated (data itself untouched). */
  const nr = (v: string | number | null | undefined): string => {
    const s = notRecorded(v);
    return s === "NOT RECORDED" ? t("control-center.truth.not_recorded", "NOT RECORDED") : s;
  };
  const detailQ = useQuery({
    queryKey: ["control-center", "decision", id],
    queryFn: ({ signal }) => controlCenterQueries.decisionDetail(id, signal),
    retry: false,
  });
  const d = obj(detailQ.data?.decision);
  const probs = obj(d.probabilities);
  return (
    <Drawer title={t("control-center.inspector.title", "Decision #{id} — evidence", { id: String(id) })} onClose={onClose}>
      {detailQ.isPending ? (
        <Skeleton count={4} />
      ) : detailQ.data?.available === false ? (
        <EmptyState message={str(obj(detailQ.data?.error).reason) ?? t("control-center.empty.decision_not_found", "decision not found")} />
      ) : (
        <div style={{ display: "grid", gap: 10 }}>
          <Panel title={t("control-center.panel.ledger_row", "Ledger row")} tight>
            <dl className="kv">
              <InfoRow label={t("control-center.label.action_mode", "action / mode")} value={`${nr(str(d.action))} / ${nr(str(d.execution_mode))}`} />
              <InfoRow label={t("control-center.label.confidence", "confidence")} value={nr(str(d.confidence))} />
              <InfoRow label="stage / blocked_by" value={`${nr(str(d.decision_stage))} / ${nr(str(d.blocked_by))}`} />
              <InfoRow label={t("control-center.label.reason", "reason")} value={nr(str(d.reason_code))} />
              <InfoRow label="request_id" value={<span className="inline-mono tiny">{nr(str(d.request_id))}</span>} />
            </dl>
          </Panel>
          <Panel title={t("control-center.panel.model_probs", "Model probabilities (NOT RECORDED when absent)")} tight>
            {bool(d.payload_ok) === false ? (
              <EmptyState message={t("control-center.empty.payload_unparseable", "payload unparseable — kept with payload_ok:false (never fabricated)")} />
            ) : (
              <dl className="kv">
                <InfoRow label="P(buy)" value={nr(str(probs.buy))} />
                <InfoRow label="P(sell)" value={nr(str(probs.sell))} />
                <InfoRow label="P(no_trade)" value={nr(str(probs.no_trade))} />
                <InfoRow label="P(wait)" value={nr(str(probs.wait))} />
                <InfoRow label="model_action" value={nr(str(probs.model_action))} />
                <InfoRow label="confidence_source" value={nr(str(obj(probs.raw).source))} />
              </dl>
            )}
          </Panel>
          <Panel title={t("control-center.inspector.correlated_orders", "Correlated orders ({n})", { n: String(arr(d.orders).length) })} tight>
            <div className="tiny muted" style={{ marginBottom: 6 }}>
              {t("control-center.inspector.correlation_method", "correlation method: {v}", { v: nr(str(d.correlation_method)) })}
            </div>
            {arr(d.orders).length === 0 ? (
              <EmptyState message={t("control-center.empty.no_correlated", "No correlated dispatch rows.")} />
            ) : (
              <DataTable headers={[{ label: t("control-center.th.ts", "ts") }, { label: t("control-center.th.ticket", "ticket") }, { label: t("control-center.th.action", "action") }, { label: t("control-center.th.latency", "latency") }]}>
                {arr(d.orders).map((o, i) => (
                  <tr key={i}>
                    <td className="tiny">{formatDateTime(str(o.timestamp))}</td>
                    <td className="num tiny">{num(o.ticket) ?? "—"}</td>
                    <td className="tiny">{str(o.action) ?? "—"}</td>
                    <td className="num tiny">{num(o.latency) === null ? "—" : `${String(num(o.latency))}ms`}</td>
                  </tr>
                ))}
              </DataTable>
            )}
          </Panel>
          <Panel title={t("control-center.panel.raw_payload", "Raw evidence payload")} tight>
            <JsonBlock value={d.payload ?? d} maxChars={5000} />
          </Panel>
        </div>
      )}
    </Drawer>
  );
}
