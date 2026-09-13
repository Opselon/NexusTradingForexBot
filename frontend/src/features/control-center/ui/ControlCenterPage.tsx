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

type Tab = "overview" | "decisions" | "funnel" | "no-trade" | "orders" | "calibration";
const LIVE_CONFIRM_TEXT = "LIVE";

export default function ControlCenterPage(props: ShellPageProps) {
  void props;
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
        <h2>Control Center</h2>
        <span className="muted small">operator evidence console (read-only ledger views + guarded engine control)</span>
        <span className={`badge ${isLive ? "bad" : "good"}`} title="mode banner">{mode}</span>
        <FreshnessCaption timestamp={str(rt.snapshot_timestamp)} source="operator/summary" isFetching={summaryQ.isFetching} error={summaryQ.isError} />
      </div>

      {isLive && (
        <div className="banner down" role="alert">
          LIVE mode dispatches real orders to the broker. Verify risk state before any operator action.
        </div>
      )}

      <Segmented
        options={[
          { id: "overview" as const, label: "Overview" },
          { id: "decisions" as const, label: "Decisions" },
          { id: "funnel" as const, label: "Funnel" },
          { id: "no-trade" as const, label: "NO_TRADE" },
          { id: "orders" as const, label: "Orders" },
          { id: "calibration" as const, label: "Calibration" },
        ]}
        value={tab}
        onChange={setTab}
      />

      <div style={{ marginTop: 12, display: "grid", gap: 12 }}>
        {tab === "overview" && (
          <>
            <div className="grid cols-4">
              <MetricCard label="Runtime" value={<StatusBadge status={running ? "RUNNING" : "STOPPED"} />} sub={str(obj(health.details).engine) ?? undefined} />
              <MetricCard
                label="Data (tick)"
                value={<StatusBadge status={bool(rt.tick_stale) ? "STALE" : rt.tick_freshness_ms !== undefined && rt.tick_freshness_ms !== null ? "READY" : "UNKNOWN"} />}
                sub={num(rt.tick_freshness_ms) === null ? "freshness NOT RECORDED" : `tick age ${formatAgeMs(num(rt.tick_freshness_ms))}`}
              />
              <MetricCard label="Model" value={<StatusBadge status={str(subs.model)} />} sub={str(obj(health.details).model) ?? undefined} />
              <MetricCard label="Database / MT5" value={<StatusBadge status={str(subs.database)} />} sub={`mt5: ${str(subs.mt5) ?? "—"}`} />
            </div>
            <div className="grid cols-2">
              <Panel title="Runtime identity (release snapshot)" tight>
                <dl className="kv">
                  <InfoRow label="version" value={notRecorded(str(idt.version))} />
                  <InfoRow label="commit" value={notRecorded(`${str(idt.commit) ?? ""}${str(idt.commit_status) ? ` (${str(idt.commit_status)})` : ""}`)} />
                  <InfoRow label="channel" value={notRecorded(str(idt.channel))} />
                  <InfoRow label="symbol / regime" value={`${notRecorded(str(rt.symbol))} / ${notRecorded(str(rt.regime))}`} />
                  <InfoRow label="bid / ask / spread" value={`${formatPrice(num(rt.bid), 2)} / ${formatPrice(num(rt.ask), 2)} / ${formatPrice(num(rt.spread), 2)}`} />
                  <InfoRow label="provenance.price" value={str(obj(rt.provenance).price) ?? "NOT RECORDED"} />
                </dl>
              </Panel>
              <Panel title="Ledger stats (bounded recent window)" tight>
                {s?.ledger?.available === false ? (
                  <EmptyState message="ledger unavailable" hint={s.ledger.reason ?? "LEDGER_UNAVAILABLE"} />
                ) : (
                  <>
                    <dl className="kv" style={{ marginBottom: 8 }}>
                      <InfoRow label="scanned rows" value={String(s?.ledger?.scanned_rows ?? "—")} />
                      <InfoRow label="latest decision" value={formatDateTime(s?.ledger?.latest_decision_at)} />
                    </dl>
                    <DistBars rows={Object.entries(obj(s?.ledger?.actions)).map(([k, v]) => ({ label: k, count: num(v) ?? 0 }))} />
                  </>
                )}
              </Panel>
            </div>
            <Panel title="Warnings (ledger-visible, backend-derived)" tight>
              {arr(s?.warnings).length === 0 ? (
                <EmptyState message="No warnings reported." />
              ) : (
                <div style={{ display: "grid", gap: 8 }}>
                  {arr(s?.warnings).map((w, i) => (
                    <div key={i} style={{ borderInlineStart: "2px solid var(--amber)", paddingInlineStart: 8 }}>
                      <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
                        <SeverityBadge severity={str(w.severity)} />
                        <span className="small">{str(w.what)}</span>
                      </div>
                      <div className="tiny muted">why: {str(w.why) ?? "—"} · impact: {str(w.impact) ?? "—"} · do: {str(w.what_to_do) ?? "—"}</div>
                    </div>
                  ))}
                </div>
              )}
            </Panel>
            <Panel title="Engine control (guarded)" accent tight>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                <button className="btn primary" disabled={engineCmd.state.running || running} onClick={() => setStartConfirm(true)}>
                  ▶ Start engine
                </button>
                <button className="btn danger" disabled={engineCmd.state.running || !running} onClick={() => setStopConfirm(true)}>
                  ■ Stop engine (kill switch)
                </button>
                <select className="select" style={{ width: 130 }} value={modeTarget} onChange={(e) => setModeTarget(e.target.value)}>
                  <option value="">mode switch…</option>
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
                  apply
                </button>
              </div>
              <div className="tiny muted" style={{ marginTop: 6 }}>
                Commands go to /api/engine/toggle + /api/engine/mode (BUG-148 hot-swap path). The UI never shows a locally-changed state — the next
                authoritative snapshot decides.
              </div>
              <CommandResultLine state={engineCmd.state} />
              <CommandResultLine state={modeCmd.state} />
            </Panel>
          </>
        )}

        {tab === "decisions" && (
          <Panel
            title="Decision observatory (audit_signals, read-only)"
            right={
              <div style={{ display: "flex", gap: 6 }}>
                <input className="input" style={{ width: 150 }} placeholder="search" value={search} onChange={(e) => setSearch(e.target.value)} />
                <select className="select" style={{ width: 120 }} value={actionFilter} onChange={(e) => setActionFilter(e.target.value)}>
                  <option value="">action: any</option>
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
              <ErrorState message={decisionsQ.error instanceof Error ? decisionsQ.error.message : "decisions failed"} onRetry={() => void decisionsQ.refetch()} />
            ) : decisionsQ.data?.available === false ? (
              <EmptyState message="ledger unavailable" />
            ) : (decisionsQ.data?.rows ?? []).length === 0 ? (
              <EmptyState message="No decisions match the filters." />
            ) : (
              <DataTable
                headers={[
                  { label: "id", num: true },
                  { label: "symbol" },
                  { label: "action" },
                  { label: "conf", num: true },
                  { label: "stage" },
                  { label: "gate" },
                  { label: "reason" },
                  { label: "at" },
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
                    <td className="num tiny">{r.confidence == null ? "NOT RECORDED" : formatNumber(r.confidence, 3)}</td>
                    <td className="tiny">{r.decision_stage ?? "—"}</td>
                    <td className="tiny">{r.blocked_by ?? ""}</td>
                    <td className="tiny muted" title={r.reason_code ?? ""}>
                      {(r.reason_code ?? "—").slice(0, 20)}
                    </td>
                    <td className="tiny">{formatDateTime(r.generated_at)}</td>
                    <td>
                      <button className="btn small ghost" disabled={r.payload_ok === false} onClick={() => setDetailId(num(r.id) ?? null)}>
                        {r.payload_ok === false ? "payload ✗" : "inspect"}
                      </button>
                    </td>
                  </tr>
                ))}
              </DataTable>
            )}
            <div className="tiny faint" style={{ marginTop: 6 }}>
              rows with unparseable payload are kept and flagged (never silently dropped) — inspect disabled for them by the backend contract.
            </div>
          </Panel>
        )}

        {tab === "funnel" && (
          <Panel title="Terminal-stage funnel" tight>
            {funnelQ.isPending ? (
              <Skeleton count={4} />
            ) : funnelQ.isError ? (
              <EmptyState message="funnel endpoint failed" />
            ) : (
              <>
                <div className="tiny muted" style={{ marginBottom: 8 }}>
                  {funnelQ.data?.note ?? "TERMINAL distributions — the ledger records the final blocking stage per decision."}
                </div>
                <div className="grid cols-3">
                  <div>
                    <div className="section-title">actions</div>
                    <DistBars rows={(funnelQ.data?.actions ?? []).map((a) => ({ label: a.action ?? "—", count: a.count ?? 0 }))} tone="var(--green)" />
                  </div>
                  <div>
                    <div className="section-title">stages</div>
                    <DistBars rows={(funnelQ.data?.stages ?? []).map((a) => ({ label: a.stage ?? "—", count: a.count ?? 0 }))} tone="var(--amber)" />
                  </div>
                  <div>
                    <div className="section-title">gates</div>
                    <DistBars rows={(funnelQ.data?.gates ?? []).map((a) => ({ label: a.gate ?? "—", count: a.count ?? 0 }))} tone="var(--red)" />
                  </div>
                </div>
                <div className="tiny faint" style={{ marginTop: 6 }}>
                  scanned {String(funnelQ.data?.scanned_rows ?? "—")} rows / total {String(funnelQ.data?.total ?? "—")} — summary numbers reconcile
                  against the same window.
                </div>
              </>
            )}
          </Panel>
        )}

        {tab === "no-trade" && (
          <Panel title="NO_TRADE forensics" tight>
            {noTradeQ.isPending ? (
              <Skeleton count={4} />
            ) : noTradeQ.isError ? (
              <EmptyState message="no-trade endpoint failed" />
            ) : (
              <div className="grid cols-2">
                <div>
                  <div className="section-title">blocking gates</div>
                  <DistBars rows={(noTradeQ.data?.gates ?? []).map((g) => ({ label: g.gate ?? "—", count: g.count ?? 0 }))} tone="var(--red)" />
                  <div className="section-title" style={{ marginTop: 8 }}>
                    regimes
                  </div>
                  <DistBars rows={(noTradeQ.data?.regimes ?? []).map((g) => ({ label: g.regime ?? "—", count: g.count ?? 0 }))} />
                </div>
                <div>
                  <div className="section-title">hourly trend</div>
                  <DistBars rows={(noTradeQ.data?.hourly_trend ?? []).map((g) => ({ label: (g.hour ?? "—").slice(5, 13), count: g.count ?? 0 }))} tone="var(--violet)" />
                  <div className="section-title" style={{ marginTop: 8 }}>
                    recent examples
                  </div>
                  <DataTable headers={[{ label: "at" }, { label: "reason" }, { label: "gate" }]}>
                    {arr(noTradeQ.data?.recent).map((r, i) => (
                      <tr key={i}>
                        <td className="tiny">{formatDateTime(str(r.generated_at))}</td>
                        <td className="tiny">{str(r.reason_code) ?? "—"}</td>
                        <td className="tiny">{str(r.blocked_by) ?? ""}</td>
                      </tr>
                    ))}
                  </DataTable>
                  <div className="tiny faint" style={{ marginTop: 4 }}>
                    model direction unresolved: {String(noTradeQ.data?.model_direction_unresolved ?? "—")}
                  </div>
                </div>
              </div>
            )}
          </Panel>
        )}

        {tab === "orders" && (
          <Panel title="Dispatch evidence (audit_orders) + latency" tight>
            {ordersQ.isPending ? (
              <Skeleton count={5} />
            ) : ordersQ.isError ? (
              <ErrorState message={ordersQ.error instanceof Error ? ordersQ.error.message : "orders failed"} onRetry={() => void ordersQ.refetch()} />
            ) : ordersQ.data?.available === false ? (
              <EmptyState message="ledger unavailable" />
            ) : (
              <>
                <div style={{ marginBottom: 8 }}>
                  <MetricCard
                    label="latency p50 / p95 / p99 (ms)"
                    value={
                      ordersQ.data?.latency
                        ? `${formatNumber(ordersQ.data.latency.p50_ms ?? NaN, 1)} / ${formatNumber(ordersQ.data.latency.p95_ms ?? NaN, 1)} / ${formatNumber(ordersQ.data.latency.p99_ms ?? NaN, 1)}`
                        : "NOT RECORDED"
                    }
                    sub={`n=${String(ordersQ.data?.latency?.n ?? 0)} · ${String(ordersQ.data?.count ?? 0)} rows`}
                  />
                </div>
                <DataTable headers={[{ label: "ts" }, { label: "ticket", num: true }, { label: "action" }, { label: "price", num: true }, { label: "vol", num: true }, { label: "latency", num: true }, { label: "mode" }, { label: "reason" }]}>
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
          <Panel title="Calibration monitor (identity-bound serving model)" right={<span className="tiny muted">/api/operator/calibration</span>} tight>
            {calibrationQ.isPending ? (
              <Skeleton count={4} />
            ) : calibrationQ.isError ? (
              <EmptyState message={calibrationQ.error instanceof Error ? calibrationQ.error.message : "calibration monitor failed"} />
            ) : calibrationQ.data?.available !== true ? (
              <EmptyState message="calibration monitor not available" hint="the endpoint answers available:false when the artifact is missing" />
            ) : (
              <>
                <div className="grid cols-4">
                  <MetricCard label="calibration" value={<StatusPill status={calibrationQ.data.calibration_status} />} sub={`serving fp ${calibrationQ.data.serving_fingerprint ?? "—"}`} />
                  <MetricCard label="artifact" value={calibrationQ.data.artifact_status ?? "—"} tone={calibrationQ.data.artifact_status === "PRESENT" ? "pos" : "neg"} sub={`collector ${calibrationQ.data.collector_status ?? "—"}`} />
                  <MetricCard
                    label="splits (cal/val)"
                    value={`${String(calibrationQ.data.calibration_split ?? 0)}/${String(calibrationQ.data.validation_split ?? 0)}`}
                    sub={`required ${String(calibrationQ.data.required_per_split ?? "—")} · deficit ${String(calibrationQ.data.deficit ?? 0)}`}
                  />
                  <MetricCard
                    label="risk multiplier (probe)"
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
          title={stopConfirm ? "STOP the engine (kill switch)" : "Start the engine"}
          danger={stopConfirm}
          confirmLabel={stopConfirm ? "Stop engine" : "Start engine"}
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
              ? "Stops the engine loop: no new decisions or dispatches. Open positions remain under broker/exits — closing them is a separate explicit action on the Positions page."
              : "Starts the engine loop via the canonical async start path (BUG-239). The backend response decides."}
          </div>
        </ConfirmModal>
      )}

      {modeTarget === "LIVE" && liveConfirm !== LIVE_CONFIRM_TEXT && (
        <ConfirmModal
          title="Switch execution mode to LIVE"
          danger
          confirmLabel="abort switch"
          busy={false}
          onCancel={() => setModeTarget("")}
          onConfirm={() => setModeTarget("")}
        >
          <div className="confirm-box">
            <div className="small">LIVE dispatches real orders. Type “{LIVE_CONFIRM_TEXT}” below the button to arm the switch (legacy parity guard).</div>
            <div className="row">
              <input className="input" style={{ width: 160 }} value={liveConfirm} onChange={(e) => setLiveConfirm(e.target.value)} placeholder={LIVE_CONFIRM_TEXT} />
              <button className="btn small danger" disabled={liveConfirm !== LIVE_CONFIRM_TEXT} onClick={() => void applyMode()}>
                switch to LIVE
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
  const detailQ = useQuery({
    queryKey: ["control-center", "decision", id],
    queryFn: ({ signal }) => controlCenterQueries.decisionDetail(id, signal),
    retry: false,
  });
  const d = obj(detailQ.data?.decision);
  const probs = obj(d.probabilities);
  return (
    <Drawer title={`Decision #${id} — evidence`} onClose={onClose}>
      {detailQ.isPending ? (
        <Skeleton count={4} />
      ) : detailQ.data?.available === false ? (
        <EmptyState message={str(obj(detailQ.data?.error).reason) ?? "decision not found"} />
      ) : (
        <div style={{ display: "grid", gap: 10 }}>
          <Panel title="Ledger row" tight>
            <dl className="kv">
              <InfoRow label="action / mode" value={`${notRecorded(str(d.action))} / ${notRecorded(str(d.execution_mode))}`} />
              <InfoRow label="confidence" value={notRecorded(str(d.confidence))} />
              <InfoRow label="stage / blocked_by" value={`${notRecorded(str(d.decision_stage))} / ${notRecorded(str(d.blocked_by))}`} />
              <InfoRow label="reason" value={notRecorded(str(d.reason_code))} />
              <InfoRow label="request_id" value={<span className="inline-mono tiny">{notRecorded(str(d.request_id))}</span>} />
            </dl>
          </Panel>
          <Panel title="Model probabilities (NOT RECORDED when absent)" tight>
            {bool(d.payload_ok) === false ? (
              <EmptyState message="payload unparseable — kept with payload_ok:false (never fabricated)" />
            ) : (
              <dl className="kv">
                <InfoRow label="P(buy)" value={notRecorded(str(probs.buy))} />
                <InfoRow label="P(sell)" value={notRecorded(str(probs.sell))} />
                <InfoRow label="P(no_trade)" value={notRecorded(str(probs.no_trade))} />
                <InfoRow label="P(wait)" value={notRecorded(str(probs.wait))} />
                <InfoRow label="model_action" value={notRecorded(str(probs.model_action))} />
                <InfoRow label="confidence_source" value={notRecorded(str(obj(probs.raw).source))} />
              </dl>
            )}
          </Panel>
          <Panel title={`Correlated orders (${arr(d.orders).length})`} tight>
            <div className="tiny muted" style={{ marginBottom: 6 }}>
              correlation method: {notRecorded(str(d.correlation_method))}
            </div>
            {arr(d.orders).length === 0 ? (
              <EmptyState message="No correlated dispatch rows." />
            ) : (
              <DataTable headers={[{ label: "ts" }, { label: "ticket" }, { label: "action" }, { label: "latency" }]}>
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
          <Panel title="Raw evidence payload" tight>
            <JsonBlock value={d.payload ?? d} maxChars={5000} />
          </Panel>
        </div>
      )}
    </Drawer>
  );
}
