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
 * Wave-2 architecture: this file is the composition root — it owns the
 * queries, the reconciliation and the honest states; the presentation lives
 * in lane-owned components:
 *  - ./TradingHero        hero header + KPI strip (lane A)
 *  - ./CommandDeck        engine + execution-mode controls (lane B)
 *  - ./MarketReadout      market state, pending orders, SMC/ICT overlays (lane C)
 * The dispatch order flow, the reconciliation table and the execution
 * history stay in this file (sortable tables + honest states; a styling
 * pass is a follow-up, not a deletion).
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
import { ConfirmModal, EmptyState, Panel, Skeleton, StatusBadge } from "@/components/primitives";
import { AgeNote, SectionState } from "@/pages/_shared/SectionState";
import { ReconPanel } from "./ReconPanel";
import { InfoChip, SortableTable, type Column } from "@/pages/_shared/widgets";
import { downloadCsv, stampForFilename } from "@/pages/_shared/csv";
import CommandDeck from "@/pages/Trading/CommandDeck";
import MarketReadout from "@/pages/Trading/MarketReadout";
import TradingHero from "@/pages/Trading/TradingHero";
import { LIVE_CONFIRM_TEXT } from "@/pages/Trading/tradingConsts";
import { useI18n } from "@/stores/i18nStore";
import { formatDateTime, formatNumber, formatPrice } from "@/lib/format";
import "@/pages/_shared/pages.css";
import "@/pages/Trading/trading.css";
import "@/pages/Trading/evidence.css";

type ReconRow = {
  ticket: string;
  engine: { symbol: string | null; direction: string | null; volume: number | null } | null;
  broker: Position | null;
  state: "MATCHED" | "ENGINE_ONLY" | "BROKER_ONLY";
};

interface Props {
  snapshot: EngineSnapshot | undefined;
  nowMs: number;
}


export default function TradingPage({ snapshot, nowMs }: Props) {
  const engineCmd = useMutationFeedback();
  const modeCmd = useMutationFeedback();
  const t = useI18n((s) => s.t);
  const [modeTarget, setModeTarget] = useState("");
  const [liveConfirm, setLiveConfirm] = useState("");
  const [showLiveConfirm, setShowLiveConfirm] = useState(false);
  const [stopConfirm, setStopConfirm] = useState(false);
  const [execPage, setExecPage] = useState(1);

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

  const execQuery = useQuery({
    queryKey: ["execution-history", execPage],
    queryFn: ({ signal }) => positionsApi.executionHistory({ page: execPage, page_size: 15 }, signal),
    placeholderData: (prev) => prev,
    retry: 1,
  });

  // Latency strings derived once per order slice — the same toFixed(1) read
  // the render did, hoisted so a table-wide re-render does not redo it. Keyed
  // by ROW REFERENCE (SortableTable sorts/filters, so the render index is not
  // the row's index in this array); the fallback recomputes the exact original
  // expression, so a miss renders byte-identically too. Deps read only the
  // query data above.
  const latencyText = useMemo(() => {
    const byRow = new Map<OperatorOrderRow, string>();
    for (const r of ordersQuery.data?.rows ?? []) {
      byRow.set(r, typeof r.latency === "number" ? r.latency.toFixed(1) : "—");
    }
    return byRow;
  }, [ordersQuery.data]);

  const orderCols = useMemo<Array<Column<OperatorOrderRow>>>(
    () => [
      { key: "time", label: t("trading.th.time", "Time"), sortValue: (r) => r.timestamp, render: (r) => (r.timestamp ? formatDateTime(r.timestamp) : "—") },
      { key: "ticket", label: t("trading.th.ticket", "Ticket"), sortValue: (r) => r.ticket, render: (r) => r.ticket ?? "—" },
      { key: "symbol", label: t("trading.th.symbol", "Symbol"), sortValue: (r) => r.symbol, render: (r) => r.symbol ?? "—" },
      { key: "action", label: t("trading.th.action", "Action"), sortValue: (r) => r.action, render: (r) => <span className={`l4-chip ${(r.action ?? "").includes("BUY") ? "good" : (r.action ?? "").includes("SELL") ? "bad" : ""}`}>{r.action ?? "—"}</span> },
      { key: "vol", label: t("trading.th.vol", "Vol"), num: true, sortValue: (r) => r.volume, render: (r) => formatNumber(r.volume) },
      { key: "price", label: t("trading.th.price", "Price"), num: true, sortValue: (r) => r.price, render: (r) => formatPrice(r.price, snapshot?.price_digits ?? 2) },
      { key: "sl", label: t("trading.th.sl", "SL"), num: true, sortValue: (r) => r.stop_loss, render: (r) => (r.stop_loss ? formatPrice(r.stop_loss) : "—") },
      { key: "tp", label: t("trading.th.tp", "TP"), num: true, sortValue: (r) => r.take_profit, render: (r) => (r.take_profit ? formatPrice(r.take_profit) : "—") },
      { key: "lat", label: t("trading.th.latency", "Latency ms"), num: true, sortValue: (r) => r.latency, render: (r) => latencyText.get(r) ?? (typeof r.latency === "number" ? r.latency.toFixed(1) : "—") },
      { key: "mode", label: t("trading.th.mode", "Mode"), sortValue: (r) => r.execution_mode, render: (r) => <StatusBadge status={String(r.execution_mode ?? null)} /> },
      { key: "reason", label: t("trading.th.reason", "Reason"), render: (r) => <span className="small muted" title={r.reason ?? undefined}>{r.reason?.slice(0, 42) ?? "—"}</span> },
    ],
    [snapshot?.price_digits, latencyText, t],
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


  const ledgerOpenQuery = useQuery({
    queryKey: ["ledger-open"],
    queryFn: ({ signal }) => positionsApi.ledgerHistory({ limit: 100, status: "OPEN" }, signal),
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

  const drift = recon.filter((r) => r.state !== "MATCHED");
  const matched = recon.length - drift.length;

  /** Refetch ONLY the already-wired queries — no new fetches, ever. */
  const refreshAll = (): void => {
    void mt5Query.refetch();
    void ordersQuery.refetch();
    void ledgerOpenQuery.refetch();
    void execQuery.refetch();
  };
  const anyFetching = mt5Query.isFetching || ordersQuery.isFetching || ledgerOpenQuery.isFetching || execQuery.isFetching;

  return (
    <div>

      <TradingHero snapshot={snapshot} mt5Positions={mt5Query.data?.positions?.length ?? 0} pendingOrders={mt5Query.data?.orders?.length ?? 0} matched={matched} drift={drift.length} nowMs={nowMs} refreshAll={refreshAll} anyFetching={anyFetching} engineCmd={engineCmd} />

      <CommandDeck snapshot={snapshot} engineCmd={engineCmd} modeCmd={modeCmd} onStart={() => void toggleEngine(true)} onStop={() => setStopConfirm(true)} onApplyMode={() => void submitMode()} modeTarget={modeTarget} setModeTarget={setModeTarget} liveConfirm={liveConfirm} setLiveConfirm={setLiveConfirm} showLiveConfirm={showLiveConfirm} setShowLiveConfirm={setShowLiveConfirm} currentMode={currentMode} t={t} />

      <MarketReadout snapshot={snapshot} mt5Query={mt5Query} ordersQuery={ordersQuery} />

      {/* D5 — the three evidence panels are grouped in one scoped section so
          the tail of the page carries the same structure the hero does:
          one kicker, numbered panels (CSS counters), shared glass skin.
          Markup wrap only — panel bodies and headers are untouched. */}
      <section className="tf-evidence" aria-labelledby="tf-evidence-title">
        <div className="tf-section-head">
          <span className="tf-kicker" id="tf-evidence-title">
            <span className="tf-kicker-dot" aria-hidden="true" />
            {t("trading.section.evidence.kicker", "Execution evidence")}
            <span className="tf-kicker-rule" aria-hidden="true" />
          </span>
          <span className="tf-section-note">
            {t("trading.section.evidence.note", "audit_orders · audit_executions · broker ledger")}
          </span>
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

      {/* Virtual real reconciliation — extracted verbatim to
          ReconPanel.tsx (line law); mt5Query stays here because the
          Pending-orders panel reads it too. */}
      <ReconPanel
        snapshot={snapshot}
        mt5Query={mt5Query}
      />

      {/* Execution history (v1 audit_executions).
          Manual order placement / cancel have NO backend route (BUG-242
          INV-004) — that internal note is kept here in the code as the audit
          trail, and kept OUT of the operator-facing header: the UI states it
          once, in the empty state, as a capability statement. */}
      <Panel
        title={t("trading.panel.exec", "Recent executions (audit_executions)")}
        right={
          <>
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
                  <tr><th scope="col">#</th><th scope="col">{t("trading.th.order_id", "Order id")}</th><th scope="col">{t("trading.th.symbol", "Symbol")}</th><th scope="col">{t("trading.th.type", "Type")}</th><th scope="col">{t("trading.th.vol", "Volume")}</th><th scope="col">{t("trading.th.price", "Price")}</th><th scope="col">{t("trading.th.status", "Status")}</th><th scope="col">{t("trading.th.executed", "Executed")}</th></tr>
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
          {t("trading.exec.no_route", "Manual order placement and cancel are not implemented — there is no backend route for them, so this console shows no buttons for actions it cannot perform.")}
        </div>
        <div className="small faint" style={{ padding: "0 12px 8px" }}>
          {t("trading.exec.guardian_label", "Guardian state:")} <StatusBadge status={String(snapshot.health.subsystems.engine ?? "UNKNOWN")} /> {t("trading.exec.guardian_mid", "(engine) · mode")} <span className="inline-mono">{currentMode || "—"}</span> {t("trading.exec.guardian_tail", "· positions and close actions live on the Positions page; model proposals on the Dashboard.")}
        </div>
      </Panel>
      </section>

      {stopConfirm && (
        <ConfirmModal
          title={t("ux.confirm.title", "Confirm action") + " — STOP ENGINE"}
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
    <button
      className="btn small ghost"
      onClick={onExport}
      aria-label={t("trading.csv.btn_aria", "Export order flow as CSV")}
      title={t("trading.csv.title", "exports exactly the rows the backend returned (client-side, no re-query)")}
    >
      {t("trading.csv.btn", "⇩ CSV")}
    </button>
  );
}
