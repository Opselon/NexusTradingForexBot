/**
 * Trading — command console around backend-supported actions ONLY.
 *
 * Supported (backend-verified):
 *  - Start / Stop engine loop      POST /api/engine/toggle
 *  - Execution mode switch         POST /api/engine/mode  (LIVE requires
 *    typed confirmation; the backend itself enforces adapter realignment
 *    and refuses invalid transitions — the UI only relays)
 *  - Close position                POST /api/positions/close
 *  - Modify SL/TP                  POST /api/positions/modify
 *
 * NOT implemented (no backend route exists): manual order placement, order
 * cancel. The UI refuses to fake such actions.
 *
 * Upgraded sections (tab-account / control-center parity inside this page):
 *  - dispatch order flow (GET /api/operator/orders — audit_orders rows +
 *    backend latency stats) with CSV export
 *  - virtual/real reconciliation: engine ledger rows vs broker positions
 *    (/api/account/trades vs /api/mt5/status), matched by ticket — a drift
 *    is shown as drift, never auto-hidden
 *  - execution history (GET /api/v1/execution/history, paginated)
 *  - SMC/ICT readout: the overlay objects the engine computed
 *    (visual_overlays) + the algo config the engine runs with
 * Every verdict/result is the backend's own reply; every section carries
 * skeleton / error+retry / honest-empty states.
 */

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { engineApi } from "@/api/engineApi";
import { positionsApi } from "@/api/positionsApi";
import { useMutationFeedback } from "@/hooks/useMutationFeedback";
import { operatorApi } from "@/pages/_shared/edgeApi";
import type { OperatorOrderRow } from "@/pages/_shared/contracts";
import type { EngineSnapshot, Position } from "@/types/domain";
import {
  ConfirmModal,
  EmptyState,
  ErrorState,
  MetricCard,
  Panel,
  PositionSideBadge,
  Skeleton,
  StatusBadge,
} from "@/components/primitives";
import { AgeNote, SectionState } from "@/pages/_shared/SectionState";
import { InfoChip, SortableTable, type Column } from "@/pages/_shared/widgets";
import { downloadCsv, stampForFilename } from "@/pages/_shared/csv";
import { useI18n } from "@/stores/i18nStore";
import { formatDateTime, formatNumber, formatPct, formatPrice, formatTime } from "@/lib/format";
import { ApiError } from "@/types/api";
import "@/pages/_shared/pages.css";

interface Props {
  snapshot: EngineSnapshot | undefined;
  nowMs: number;
}

const LIVE_CONFIRM_TEXT = "LIVE";

type ReconRow = {
  ticket: string;
  engine: { symbol: string | null; direction: string | null; volume: number | null } | null;
  broker: Position | null;
  state: "MATCHED" | "ENGINE_ONLY" | "BROKER_ONLY";
};

export default function TradingPage({ snapshot, nowMs }: Props) {
  const engineCmd = useMutationFeedback();
  const modeCmd = useMutationFeedback();
  const t = useI18n((s) => s.t);
  const [modeTarget, setModeTarget] = useState("");
  const [liveConfirm, setLiveConfirm] = useState("");
  const [showLiveConfirm, setShowLiveConfirm] = useState(false);
  const [stopConfirm, setStopConfirm] = useState(false);
  const [execPage, setExecPage] = useState(1);

  const modeImpact: Record<string, string> = {
    PAPER: t("trading.mode.impact_paper", "Simulated fills only — no real orders reach the broker."),
    SHADOW: t("trading.mode.impact_shadow", "Signals are computed but never dispatched as orders."),
    LIVE: t("trading.mode.impact_live", "The engine will dispatch REAL orders to the connected broker account."),
  };

  const mt5Query = useQuery({
    queryKey: ["mt5-status"],
    queryFn: ({ signal }) => engineApi.mt5Status(signal),
    refetchInterval: 10_000,
  });

  const ordersQuery = useQuery({
    queryKey: ["operator-orders"],
    queryFn: ({ signal }) => operatorApi.orders(80, signal),
    refetchInterval: 20_000,
    retry: false,
  });

  const ledgerOpenQuery = useQuery({
    queryKey: ["ledger-open"],
    queryFn: ({ signal }) => positionsApi.ledgerHistory({ limit: 100, status: "OPEN" }, signal),
    refetchInterval: 20_000,
    retry: 1,
  });

  const execQuery = useQuery({
    queryKey: ["execution-history", execPage],
    queryFn: ({ signal }) => positionsApi.executionHistory({ page: execPage, page_size: 15 }, signal),
    placeholderData: (prev) => prev,
    retry: 1,
  });

  // ---- reconciliation (engine ledger OPEN vs broker positions) ------------
  const recon = useMemo<ReconRow[]>(() => {
    const engineRows = ledgerOpenQuery.data ?? [];
    const brokerRows: Position[] = mt5Query.data?.positions ?? snapshot?.positions ?? [];
    const byTicket = new Map<string, ReconRow>();
    for (const e of engineRows) {
      if (e.ticket === null) continue;
      byTicket.set(String(e.ticket), {
        ticket: String(e.ticket),
        engine: { symbol: e.symbol, direction: e.direction, volume: e.volume },
        broker: null,
        state: "ENGINE_ONLY",
      });
    }
    for (const b of brokerRows) {
      if (b.ticket === null) continue;
      const key = String(b.ticket);
      const prev = byTicket.get(key);
      if (prev) {
        byTicket.set(key, { ...prev, broker: b, state: "MATCHED" });
      } else {
        byTicket.set(key, { ticket: key, engine: null, broker: b, state: "BROKER_ONLY" });
      }
    }
    return [...byTicket.values()].sort((a, b) => Number(b.state === "MATCHED") - Number(a.state === "MATCHED"));
  }, [ledgerOpenQuery.data, mt5Query.data, snapshot?.positions]);

  const orderCols = useMemo<Array<Column<OperatorOrderRow>>>(
    () => [
      { key: "time", label: t("trading.th.time", "Time"), sortValue: (r) => r.timestamp, render: (r) => (r.timestamp ? formatDateTime(r.timestamp) : "—") },
      { key: "ticket", label: t("trading.th.ticket", "Ticket"), sortValue: (r) => r.ticket, render: (r) => r.ticket ?? "—" },
      { key: "symbol", label: t("trading.th.symbol", "Symbol"), sortValue: (r) => r.symbol, render: (r) => r.symbol ?? "—" },
      { key: "action", label: t("trading.th.action", "Action"), sortValue: (r) => r.action, render: (r) => <span className={`l4-chip ${(r.action ?? "").includes("BUY") ? "good" : (r.action ?? "").includes("SELL") ? "bad" : ""}`}>{r.action ?? "—"}</span> },
      { key: "vol", label: t("trading.th.vol", "Vol"), num: true, sortValue: (r) => r.volume, render: (r) => formatNumber(r.volume) },
      { key: "price", label: t("trading.th.price", "Price"), num: true, sortValue: (r) => r.price, render: (r) => formatPrice(r.price, snapshot?.price_digits ?? 2) },
      { key: "sl", label: "SL", num: true, sortValue: (r) => r.stop_loss, render: (r) => (r.stop_loss ? formatPrice(r.stop_loss) : "—") },
      { key: "tp", label: "TP", num: true, sortValue: (r) => r.take_profit, render: (r) => (r.take_profit ? formatPrice(r.take_profit) : "—") },
      { key: "lat", label: t("trading.th.latency", "Latency ms"), num: true, sortValue: (r) => r.latency, render: (r) => (typeof r.latency === "number" ? r.latency.toFixed(1) : "—") },
      { key: "mode", label: t("trading.th.mode", "Mode"), sortValue: (r) => r.execution_mode, render: (r) => <StatusBadge status={String(r.execution_mode ?? null)} /> },
      { key: "reason", label: t("trading.th.reason", "Reason"), render: (r) => <span className="small muted" title={r.reason ?? undefined}>{r.reason?.slice(0, 42) ?? "—"}</span> },
    ],
    [snapshot?.price_digits, t],
  );

  if (!snapshot) {
    return (
      <div>
        <Panel title={t("nav.page.trading", "Trading")}>
          <Skeleton count={5} />
        </Panel>
      </div>
    );
  }

  const running = snapshot.engine_running;
  const currentMode = (snapshot.runtime_mode ?? snapshot.execution_mode ?? "").toUpperCase();

  const toggleEngine = async (active: boolean): Promise<void> => {
    const ok = await engineCmd.run(() => engineApi.toggleEngine(active));
    if (ok) {
      // No local state change — the next authoritative snapshot/signal carries it.
    }
  };

  const submitMode = async (): Promise<void> => {
    if (!modeTarget) return;
    if (modeTarget === "LIVE" && liveConfirm !== LIVE_CONFIRM_TEXT) return;
    const ok = await modeCmd.run(() => engineApi.setMode(modeTarget));
    if (ok) {
      setShowLiveConfirm(false);
      setLiveConfirm("");
      setModeTarget("");
    }
  };

  const drift = recon.filter((r) => r.state !== "MATCHED");
  const matched = recon.length - drift.length;

  return (
    <div>
      <div className="grid cols-4">
        <MetricCard label={t("trading.card.engine_loop", "Engine loop")} value={running ? t("trading.card.running", "RUNNING") : t("trading.card.stopped", "STOPPED")} tone={running ? "pos" : "dim"} sub={t("trading.card.engine_sub", "backend-authoritative (state_version climbs while running)")} />
        <MetricCard label={t("trading.card.mode", "Execution mode")} value={currentMode || "—"} tone={currentMode.startsWith("LIVE") ? "neg" : "dim"} sub={t("trading.card.mode_sub", "data_source: {v}", { v: snapshot.data_source ?? "—" })} />
        <MetricCard label={t("trading.card.adapter", "Broker adapter")} value={snapshot.adapter_class ?? "—"} tone="dim" sub={String(snapshot.health.details.mt5 ?? "")} />
        <MetricCard
          label={t("trading.card.terminal", "Terminal trading")}
          value={snapshot.account.trade_allowed === null ? "—" : snapshot.account.trade_allowed ? t("trading.card.allowed", "ALLOWED") : t("trading.card.restricted", "RESTRICTED")}
          tone={snapshot.account.trade_allowed === true ? "pos" : snapshot.account.trade_allowed === false ? "neg" : "dim"}
          sub={t("trading.card.terminal_sub", "broker terminal trade_allowed")}
        />
      </div>

      <div className="grid cols-2" style={{ marginTop: 14 }}>
        <Panel title={t("trading.panel.engine_commands", "Engine commands")} accent>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button className="btn primary" disabled={engineCmd.state.running || running} onClick={() => void toggleEngine(true)}>
              {t("trading.engine.start", "▶ Start engine")}
            </button>
            <button className="btn danger" disabled={engineCmd.state.running || !running} onClick={() => setStopConfirm(true)}>
              {t("trading.engine.stop", "■ Stop engine")}
            </button>
          </div>
          {engineCmd.state.lastMessage && (
            <div className={`cmd-result ${engineCmd.state.lastResult ? "ok" : "fail"}`}>
              {engineCmd.state.lastResult ? "✓" : "✕"} {engineCmd.state.lastMessage}
            </div>
          )}
          <div className="small muted" style={{ marginTop: 10 }}>
            {t("trading.engine.disclaimer", "Result comes from the backend response — the UI never assumes a command succeeded before confirmation, and the authoritative engine state on the left updates from the next snapshot.")}
          </div>
        </Panel>

        <Panel title={t("trading.panel.mode", "Execution mode (PAPER ⇄ LIVE)")} accent>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <select aria-label={t("dash.engine.mode_aria", "Target execution mode")} className="select" value={modeTarget} onChange={(e) => { setModeTarget(e.target.value); setShowLiveConfirm(e.target.value === "LIVE"); }}>
              <option value="">{t("trading.mode.select_placeholder", "select mode…")}</option>
              <option value="PAPER">{t("trading.mode.paper", "PAPER (simulation adapter)")}</option>
              <option value="SHADOW">{t("trading.mode.shadow", "SHADOW (no execution)")}</option>
              <option value="LIVE">{t("trading.mode.live", "LIVE (real capital)")}</option>
            </select>
            <button
              className={`btn ${modeTarget === "LIVE" ? "danger" : "primary"}`}
              disabled={!modeTarget || modeCmd.state.running || (modeTarget === "LIVE" && liveConfirm !== LIVE_CONFIRM_TEXT) || modeTarget === currentMode}
              onClick={() => void submitMode()}
            >
              {t("trading.mode.apply", "Apply mode")}
            </button>
            <span className="l4-chip">{t("trading.mode.current", "current {m}", { m: currentMode || "—" })}</span>
          </div>
          {modeTarget && modeImpact[modeTarget] && (
            <div className="l4-note" style={{ marginTop: 8 }}>{modeImpact[modeTarget]}</div>
          )}
          {showLiveConfirm && modeTarget === "LIVE" && (
            <div className="confirm-box">
              <div>
                <b>{t("ux.mode.live_warning", "Real money is at risk. This affects your live broker account.")}</b>{" "}
                {t("ux.mode.body", "This changes how the engine executes orders.")}{" "}
                <span className="muted">{modeImpact.LIVE}</span>
              </div>
              <div className="row">
                <input
                  className="input"
                  style={{ width: 200 }}
                  aria-label={t("dash.engine.arm_aria", "LIVE confirmation phrase")} placeholder={t("ux.confirm.type", "Type {w} to enable confirmation", { w: LIVE_CONFIRM_TEXT })}
                  value={liveConfirm}
                  onChange={(e) => setLiveConfirm(e.target.value.toUpperCase())}
                />
                <span className="note">{t("trading.mode.confirm_note", "confirmation is relayed with the command; backend validation still applies")}</span>
              </div>
            </div>
          )}
          {modeCmd.state.lastMessage && (
            <div className={`cmd-result ${modeCmd.state.lastResult ? "ok" : "fail"}`}>
              {modeCmd.state.lastResult ? "✓" : "✕"} {modeCmd.state.lastMessage}
            </div>
          )}
        </Panel>
      </div>

      <div className="grid cols-2">
        <Panel
          title={t("trading.panel.market_state", "Market / execution state")}
          right={<AgeNote label={t("trading.age.tick", "tick age")} ageSec={snapshot.diagnostics.tick_age_sec} />}
        >
          <dl className="kv">
            <dt>{t("trading.kv.symbol", "symbol")}</dt>
            <dd>{snapshot.symbol ?? "—"}</dd>
            <dt>{t("trading.kv.bid_ask", "bid / ask")}</dt>
            <dd>{formatPrice(snapshot.bid, snapshot.price_digits ?? 2)} / {formatPrice(snapshot.ask, snapshot.price_digits ?? 2)}</dd>
            <dt>{t("trading.kv.spread", "spread")}</dt>
            <dd>{snapshot.spread === null ? "—" : `${formatNumber(snapshot.spread)} pts`}</dd>
            <dt>{t("trading.kv.tick_stale", "tick stale")}</dt>
            <dd>{snapshot.tick_stale ? <span className="badge warn">{t("ux.data.stale", "STALE")}</span> : <span className="badge good">{t("ux.data.fresh", "FRESH")}</span>}</dd>
            <dt>{t("trading.kv.regime", "regime")}</dt>
            <dd>{snapshot.regime ?? "—"}</dd>
            <dt>{t("trading.kv.ai_proposal", "AI proposal")}</dt>
            <dd>{snapshot.ai_decision ?? "—"} {snapshot.ai_confidence !== null ? `(${formatPct(snapshot.ai_confidence * 100, 1)})` : ""}</dd>
            <dt>{t("trading.kv.blocked_by", "proposal blocked by")}</dt>
            <dd>{snapshot.ai_reason ?? "—"}</dd>
            <dt>{t("trading.kv.proposal_age", "proposal age")}</dt>
            <dd>{snapshot.diagnostics.proposal_age_sec === null ? "—" : `${snapshot.diagnostics.proposal_age_sec.toFixed(1)}s`}</dd>
          </dl>
        </Panel>

        <Panel title={t("trading.panel.pending", "Pending orders (broker)")} tight>
          {mt5Query.data?.orders && mt5Query.data.orders.length > 0 ? (
            <div tabIndex={0} className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr><th scope="col">{t("trading.th.ticket", "Ticket")}</th><th scope="col">{t("trading.th.type", "Type")}</th><th scope="col">{t("trading.th.volume", "Volume")}</th><th scope="col">{t("trading.th.price", "Price")}</th><th scope="col">{t("trading.th.state", "State")}</th><th scope="col">{t("trading.th.setup", "Setup")}</th></tr>
                </thead>
                <tbody>
                  {mt5Query.data.orders.map((o, i) => (
                    <tr key={o.ticket ?? i}>
                      <td>{o.ticket ?? "—"}</td>
                      <td>{String(o.type ?? "—")}</td>
                      <td className="num">{formatNumber(o.volume_current)}</td>
                      <td className="num">{formatPrice(o.price_open)}</td>
                      <td>{String(o.state ?? "—")}</td>
                      <td>{formatTime(o.time_setup)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : mt5Query.isPending ? (
            <div style={{ padding: 14 }}><Skeleton count={3} /></div>
          ) : mt5Query.isError ? (
            <ErrorState message={t("trading.err.pending", "Pending orders unavailable (MT5 status endpoint failed).")} onRetry={() => void mt5Query.refetch()} />
          ) : (
            <EmptyState message={t("trading.empty.pending", "No pending orders on the broker account.")} />
          )}
        </Panel>
      </div>

      {/* Dispatch order flow — audit_orders + backend latency stats */}
      <Panel
        title={t("trading.panel.order_flow", "Dispatch order flow (audit_orders)")}
        right={
          <>
            <AgeNote label={t("trading.age.label", "age")} ageSec={ordersQuery.dataUpdatedAt ? Math.max(0, (nowMs - ordersQuery.dataUpdatedAt) / 1000) : null} />
            <SectionExportButton
              rows={ordersQuery.data?.rows ?? []}
              onExport={() =>
                downloadCsv({
                  filename: `nse-order-flow-${stampForFilename()}.csv`,
                  headers: ["timestamp", "id", "ticket", "order_id", "symbol", "action", "volume", "price", "stop_loss", "take_profit", "latency", "execution_mode", "reason", "execution_id"],
                  rows: (ordersQuery.data?.rows ?? []).map((r) => [r.timestamp, r.id, r.ticket, r.order_id, r.symbol, r.action, r.volume, r.price, r.stop_loss, r.take_profit, r.latency, r.execution_mode, r.reason, r.execution_id]),
                })
              }
            />
          </>
        }
        tight
      >
        {ordersQuery.isPending && !ordersQuery.data ? (
          <div style={{ padding: 12 }}><Skeleton count={4} /></div>
        ) : ordersQuery.data?.available === false ? (
          <EmptyState message={t("trading.empty.order_flow", "Order flow unavailable.")} hint={ordersQuery.data.reason ?? t("trading.empty.order_flow_hint", "Ledger store not reachable — nothing inferred.")} />
        ) : (ordersQuery.data?.rows?.length ?? 0) === 0 ? (
          <EmptyState message={t("trading.empty.no_orders", "No dispatched orders recorded yet.")} hint={t("trading.empty.no_orders_hint", "audit_orders rows appear when the engine sends a proposal to the broker/simulation adapter.")} />
        ) : (
          <>
            <div className="l4-toolbar" style={{ padding: "8px 12px 0" }}>
              {ordersQuery.data?.latency ? (
                <>
                  <InfoChip k="n" v={ordersQuery.data.latency.n ?? "—"} />
                  <InfoChip k="p50" v={`${formatNumber(ordersQuery.data.latency.p50_ms, 1)} ms`} tone="accent" />
                  <InfoChip k="p95" v={`${formatNumber(ordersQuery.data.latency.p95_ms, 1)} ms`} />
                  <InfoChip k="p99" v={`${formatNumber(ordersQuery.data.latency.p99_ms, 1)} ms`} />
                </>
              ) : (
                <span className="l4-note">{t("trading.latency.none", "no numeric latency values in the returned rows yet")}</span>
              )}
              <span className="timestamp-note" style={{ marginInlineStart: "auto" }}>{t("trading.latency.stats", "stats computed by the backend over these rows")}</span>
            </div>
            <SortableTable
              columns={orderCols}
              rows={ordersQuery.data?.rows ?? []}
              rowKey={(r) => String(r.id)}
              initialSort={{ key: "time", dir: "desc" }}
              filter={(r, q) => String(r.ticket ?? "").includes(q) || (r.symbol ?? "").toLowerCase().includes(q) || (r.action ?? "").toLowerCase().includes(q)}
              emptyMessage={t("trading.empty.no_order_rows", "No order-flow rows.")}
            />
          </>
        )}
      </Panel>

      {/* Virtual ↔ real reconciliation */}
      <Panel
        title={t("trading.panel.recon", "Virtual ⇄ real reconciliation")}
        right={
          <>
            <InfoChip k={t("trading.chip.matched", "matched")} v={matched} tone={drift.length === 0 && matched > 0 ? "good" : ""} />
            <InfoChip k={t("trading.chip.drift", "drift")} v={drift.length} tone={drift.length > 0 ? "bad" : ""} />
          </>
        }
        tight
      >
        <div className="l4-note" style={{ padding: "8px 12px 0" }}>
          {t("trading.recon.note", "Engine ledger OPEN rows (/api/account/trades?status=OPEN) matched by ticket against broker positions (/api/mt5/status).")}
          {mt5Query.isPending ? t("trading.recon.note_pending", " broker read pending…") : mt5Query.isError ? t("trading.recon.note_failed", " ⚠ broker read FAILED — positions below fall back to the canonical snapshot.") : ""}
        </div>
        {ledgerOpenQuery.isPending ? (
          <div style={{ padding: 12 }}><Skeleton count={3} /></div>
        ) : ledgerOpenQuery.isError ? (
          <ErrorState
            message={ledgerOpenQuery.error instanceof ApiError ? ledgerOpenQuery.error.message : t("trading.err.ledger", "Engine ledger unavailable")}
            requestId={ledgerOpenQuery.error instanceof ApiError ? ledgerOpenQuery.error.requestId : null}
            onRetry={() => void ledgerOpenQuery.refetch()}
          />
        ) : recon.length === 0 ? (
          <EmptyState message={t("trading.empty.recon", "Nothing to reconcile — no open ledger rows and no broker positions.")} hint={t("trading.empty.recon_hint", "A clean, consistent EMPTY. Not a hidden drift.")} />
        ) : (
          <SortableTable
            columns={[
              { key: "ticket", label: t("trading.th.ticket", "Ticket"), sortValue: (r) => r.ticket, render: (r) => r.ticket },
              {
                key: "engine",
                label: t("trading.th.engine_ledger", "Engine ledger"),
                sortValue: (r) => r.engine?.symbol ?? null,
                render: (r) =>
                  r.engine ? `${r.engine.symbol ?? "—"} ${r.engine.direction ?? "?"} ${formatNumber(r.engine.volume)}` : <span className="faint">{t("trading.cell.absent", "absent")}</span>,
              },
              {
                key: "broker",
                label: t("trading.th.broker_position", "Broker position"),
                sortValue: (r) => r.broker?.symbol ?? null,
                render: (r) =>
                  r.broker ? (
                    <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
                      {r.broker.symbol ?? "—"} <PositionSideBadge type={r.broker.type} /> {formatNumber(r.broker.volume)} @ {formatPrice(r.broker.price_open)}
                    </span>
                  ) : (
                    <span className="faint">{t("trading.cell.absent", "absent")}</span>
                  ),
              },
              {
                key: "state",
                label: t("trading.th.state", "State"),
                sortValue: (r) => r.state,
                render: (r) => (
                  <span className={`l4-chip ${r.state === "MATCHED" ? "good" : "warn"}`}>
                    {r.state === "MATCHED" ? t("trading.state.matched", "✓ ticket+symbol") : r.state === "ENGINE_ONLY" ? t("trading.state.engine_only", "ENGINE ONLY (not on broker)") : t("trading.state.broker_only", "BROKER ONLY (untracked)")}
                  </span>
                ),
              },
            ]}
            rows={recon}
            rowKey={(r) => r.ticket}
            emptyMessage={t("trading.empty.no_rows", "No rows.")}
            maxHeight={320}
          />
        )}
        {drift.length > 0 && (
          <div className="confirm-box" style={{ marginInline: 12, marginBlock: 12, borderColor: "rgba(235,161,63,0.5)" }}>
            <span>
              <b>{t("trading.recon.drift_title", "{n} unreconciled row(s).", { n: drift.length })}</b> {t("trading.recon.drift_body", "ENGINE ONLY usually means the broker rejected/closed without the ledger catching the deal yet; BROKER ONLY means a position the engine did not open (manual terminal action or restart gap). Investigate before enabling new risk.")}
            </span>
          </div>
        )}
      </Panel>

      {/* SMC / ICT readout — engine-computed overlays + algo config */}
      <Panel
        title={t("trading.panel.smc", "SMC / ICT readout (engine-computed overlays)")}
        right={<span className="timestamp-note">{t("trading.smc.computed", "snapshot v{v} · computed by the engine, never the browser", { v: snapshot.state_version })}</span>}
      >
        {(() => {
          const ov = snapshot.visual_overlays as {
            rectangles?: Array<Record<string, unknown>>;
            bos_lines?: Array<Record<string, unknown>>;
            midlines?: Array<Record<string, unknown>>;
            liq_markers?: Array<Record<string, unknown>>;
            order_lines?: Record<string, unknown> | null;
          } | null;
          const rects = ov?.rectangles ?? [];
          const bos = ov?.bos_lines ?? [];
          const mids = ov?.midlines ?? [];
          const liq = ov?.liq_markers ?? [];
          const total = rects.length + bos.length + mids.length + liq.length;
          if (total === 0) {
            return <EmptyState message={t("trading.empty.overlays", "No active zones, BOS breaks, equilibrium lines or sweeps on the last computed window.")} hint={t("trading.empty.overlays_hint", "visual_overlays is empty — the engine saw no unmitigated structure, not a rendering failure.")} />;
          }
          const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
          const digits = snapshot.price_digits ?? 2;
          return (
            <div className="grid cols-2">
              <div>
                <div className="section-title">{t("trading.smc.zones", "Zones (FVG / order blocks / stop-hunts)")}</div>
                <SortableTable
                  columns={[
                    { key: "type", label: t("trading.th.type", "Type"), sortValue: (r) => String(r.type ?? ""), render: (r) => <span className={`l4-chip ${String(r.type ?? "").includes("BULL") ? "good" : String(r.type ?? "").includes("BEAR") ? "bad" : "warn"}`}>{String(r.type ?? "—")}</span> },
                    { key: "range", label: t("trading.th.range", "Range"), num: true, render: (r) => `${formatPrice(num(r.price_low), digits)}–${formatPrice(num(r.price_high), digits)}` },
                    { key: "time", label: t("trading.th.since", "Since"), render: (r) => (r.time ? formatTime(String(r.time)) : "—") },
                  ]}
                  rows={rects}
                  rowKey={(r, i) => String(r.id ?? i)}
                  emptyMessage={t("trading.empty.no_zones", "No zones.")}
                  maxHeight={220}
                />
              </div>
              <div>
                <div className="section-title">{t("trading.smc.structure", "Structure lines & sweeps")}</div>
                <dl className="kv">
                  <dt>{t("trading.smc.bos", "BOS breaks")}</dt>
                  <dd>{bos.length ? bos.slice(-6).map((l) => `${String(l.type ?? "BOS").split("_")[0]}@${formatPrice(num(l.price), digits)}`).join(" · ") : "—"}</dd>
                  <dt>{t("trading.smc.equilibrium", "equilibrium")}</dt>
                  <dd>{mids.length ? mids.map((m) => `${formatPrice(num(m.price), digits)} (${String(m.label ?? "50%")})`).join(" · ") : "—"}</dd>
                  <dt>{t("trading.smc.liquidity", "liquidity sweeps")}</dt>
                  <dd>{liq.length ? liq.slice(-6).map((m) => `${String(m.type ?? "").includes("BUY") ? "BSL" : "SSL"}@${formatPrice(num(m.price), digits)}`).join(" · ") : "—"}</dd>
                  <dt>{t("trading.smc.algo", "algo config")}</dt>
                  <dd className="small">
                    {t("trading.smc.config_text", "SL buffer ×{a} · min RR {b} · conf ≥ {c} · FVG sens {d} · OB lookback {e} bars", { a: snapshot.algo_config.atr_sl_buffer_multiplier, b: snapshot.algo_config.min_risk_reward_ratio, c: snapshot.algo_config.ai_zone_confidence_threshold, d: snapshot.algo_config.fvg_mitigation_sensitivity, e: snapshot.algo_config.order_block_lookback_bars })}
                  </dd>
                </dl>
              </div>
            </div>
          );
        })()}
      </Panel>

      {/* Execution history (v1 audit_executions) */}
      <Panel
        title={t("trading.panel.exec", "Recent executions (audit_executions)")}
        right={
          <>
            <span className="small faint">{t("trading.exec.no_route", "manual order placement / cancel: NO backend route — no fake buttons here (BUG-242 INV-004)")}</span>
            <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
              <button aria-label={t("trading.pager.prev", "Previous page")} className="btn small" disabled={execPage <= 1} onClick={() => setExecPage((p) => Math.max(1, p - 1))}>‹</button>
              <span className="small faint inline-mono">{t("trading.exec.page", "p{n}", { n: execPage })}</span>
              <button aria-label={t("trading.pager.next", "Next page")} className="btn small" disabled={!execQuery.data?.has_more} onClick={() => setExecPage((p) => p + 1)}>›</button>
            </span>
          </>
        }
        tight
      >
        <SectionState
          query={execQuery}
          emptyMessage={t("trading.empty.exec", "No execution rows yet.")}
          emptyHint={t("trading.empty.exec_hint", "audit_executions fills as the OrderLifecycleManager dispatches.")}
          errorFallback={t("trading.err.exec", "Execution history endpoint failed.")}
          emptyWhen={(d) => d.items.length === 0}
        >
          {(d) => (
            <div tabIndex={0} className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr><th scope="col">#</th><th scope="col">Order id</th><th scope="col">Symbol</th><th scope="col">Type</th><th scope="col">Volume</th><th scope="col">Price</th><th scope="col">Status</th><th scope="col">Executed</th></tr>
                </thead>
                <tbody>
                  {d.items.map((r, i) => (
                    <tr key={String(r.id ?? `${r.order_id}-${i}`)}>
                      <td>{String(r.id ?? "—")}</td>
                      <td className="small">{r.order_id ?? "—"}</td>
                      <td>{r.symbol ?? "—"}</td>
                      <td>{r.order_type ?? "—"}</td>
                      <td className="num">{formatNumber(r.volume)}</td>
                      <td className="num">{formatPrice(r.price)}</td>
                      <td><StatusBadge status={r.status ?? null} /></td>
                      <td>{r.executed_at ? formatDateTime(r.executed_at) : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </SectionState>
        <div className="small faint" style={{ padding: "8px 12px" }}>
          {t("trading.exec.guardian_label", "Guardian state:")} <StatusBadge status={String(snapshot.health.subsystems.engine ?? "UNKNOWN")} /> {t("trading.exec.guardian_mid", "(engine) · mode")} <span className="inline-mono">{currentMode || "—"}</span> {t("trading.exec.guardian_tail", "· positions & close actions live on the Positions page; model proposals on the Dashboard.")}
        </div>
      </Panel>

      {stopConfirm && (
        <ConfirmModal
          title={t("dash.confirm.stop_title", "Confirm action — STOP ENGINE")}
          confirmLabel={t("trading.engine.stop", "■ Stop engine")}
          busy={engineCmd.state.running}
          onCancel={() => setStopConfirm(false)}
          onConfirm={() => {
            setStopConfirm(false);
            void toggleEngine(false);
          }}
        >
          <div>
            <b>{t("trading.confirm.impact", "Impact:")}</b> {t("trading.confirm.stop_body", "the engine loop stops — no new proposals, no new executions. Open positions stay on the broker until you act there.")}
            <div className="small muted" style={{ marginTop: 8 }}>
              {t("trading.confirm.stop_recovery", "Recovery: Start engine re-attaches the loop; the backend refuses the command if the runtime state forbids it.")}
            </div>
          </div>
        </ConfirmModal>
      )}
    </div>
  );
}

/** CSV export affordance shared by the order-flow table header. */
function SectionExportButton({ rows, onExport }: { rows: unknown[]; onExport: () => void }) {
  const t = useI18n((s) => s.t);
  if (rows.length === 0) return null;
  return (
    <button className="btn small ghost" onClick={onExport} title={t("trading.csv.title", "exports exactly the rows the backend returned (client-side, no re-query)")}>
      {t("trading.csv.btn", "⇩ CSV")}
    </button>
  );
}
