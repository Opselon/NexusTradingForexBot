/**
 * Closed trades table + forensic detail drawer with the PnL waterfall.
 *
 * Rows come from /api/account/trades (broker-reconstructed, ledger fallback —
 * the backend decides the producer). Opening a drawer fetches
 * /api/account/trades/{ticket}; the waterfall decomposes only the components
 * the trace actually reports (a missing commission is an UNKNOWN bar).
 */

import { useMemo, useRef, useState } from "react";
import { useDialogA11y } from "../../../components/useDialogA11y";
import { DataTable, EmptyState, ErrorState, Panel, Skeleton, StatusBadge } from "@/components/primitives";
import { PnlWaterfall, buildTradeWaterfall } from "@/components/viz";
import { formatDateTime, formatNumber, formatPrice } from "@/lib/format";
import { useAccountTrades, useTradeForensics } from "../hooks";
import { DASH, FreshnessNote, asErrorText, jsonInline, jsonPretty, moneyOrDash, numOrDash } from "./shared";
import { useI18n } from "@/stores/i18nStore";

const PAGE = 25;

export function TradesSection() {
  const t = useI18n((s) => s.t);
  const [offset, setOffset] = useState(0);
  const [ticket, setTicket] = useState<number | null>(null);
  const trades = useAccountTrades(PAGE, offset);

  const rows = trades.data?.trades ?? [];

  return (
    <Panel
      title={t("account.trades.title", "Closed trades ({count})", { count: String(rows.length) })}
      right={
        <>
          <button className="btn small ghost" onClick={() => setOffset((o) => Math.max(0, o - PAGE))} disabled={offset === 0 || trades.isFetching}>
            {t("account.trades.newer", "← newer")}
          </button>
          <span className="timestamp-note">{t("account.trades.offset", "offset {offset}", { offset: String(offset) })}</span>
          <button className="btn small ghost" onClick={() => setOffset((o) => o + PAGE)} disabled={rows.length < PAGE || trades.isFetching}>
            {t("account.trades.older", "older →")}
          </button>
          <FreshnessNote updatedAtMs={trades.dataUpdatedAt ?? null} label={t("account.fresh.trades", "trades")} staleAfterMs={180_000} />
        </>
      }
    >
      {trades.isPending ? (
        <Skeleton count={6} height={20} />
      ) : trades.isError ? (
        <ErrorState message={asErrorText(trades.error, t)} onRetry={() => trades.refetch()} />
      ) : rows.length === 0 ? (
        <EmptyState message={t("account.trades.empty", "No historical trades found yet.")} hint={t("account.trades.empty_hint", "Broker history sync pending — no rows are invented in the meantime.")} />
      ) : (
        <DataTable
          headers={[
            { label: t("account.trades.th_ticket", "ticket") },
            { label: t("account.trades.th_symbol", "symbol") },
            { label: t("account.trades.th_dir", "dir") },
            { label: t("account.trades.th_vol", "vol"), num: true },
            { label: t("account.trades.th_entry_exit", "entry → exit") },
            { label: t("account.trades.th_net_pnl", "net PnL"), num: true },
            { label: "R", num: false },
            { label: t("account.trades.th_status", "status") },
            { label: t("account.trades.th_closed", "closed") },
          ]}
        >
          {rows.map((tr, i) => (
            <tr key={`${tr.id}-${i}`}>
              <td>
                {tr.numericId !== null ? (
                  <button className="btn small ghost" onClick={() => setTicket(tr.numericId)}>
                    #{tr.id}
                  </button>
                ) : (
                  tr.id
                )}
              </td>
              <td>{tr.symbol}</td>
              <td>
                <span className={`badge ${tr.direction === "BUY" ? "good" : tr.direction === "SELL" ? "bad" : "unknown"}`}>{tr.direction}</span>
              </td>
              <td className="num">{formatNumber(tr.volume)}</td>
              <td className="inline-mono">
                {tr.entryPrice !== null ? formatPrice(tr.entryPrice) : DASH} → {tr.exitPrice !== null ? formatPrice(tr.exitPrice) : DASH}
              </td>
              <td className={`num ${tr.netPnl === null ? "" : tr.netPnl >= 0 ? "pnl-pos" : "pnl-neg"}`}>{moneyOrDash(tr.netPnl, true)}</td>
              <td className="tiny faint">{tr.realizedR === null ? DASH : `${tr.realizedR.toFixed(2)}R`}</td>
              <td>{tr.status}</td>
              <td>{tr.closedAt ? formatDateTime(tr.closedAt) : DASH}</td>
            </tr>
          ))}
        </DataTable>
      )}
      {ticket !== null && <TradeDetailDrawer ticket={ticket} onClose={() => setTicket(null)} />}
    </Panel>
  );
}

function TradeDetailDrawer({ ticket, onClose }: { ticket: number; onClose: () => void }) {
  const t = useI18n((s) => s.t);
  const trace = useTradeForensics(ticket);

  const boxRef = useRef<HTMLElement | null>(null);
  useDialogA11y(boxRef, onClose);

  const d = trace.data;

  // perf: the forensic JSON serializations run once per trace fetch instead of
  // on every drawer render — deps are exactly the fields each body reads.
  const orderEvents = useMemo(
    () => (d?.order_events ?? []).map((ev) => ({ ev, text: jsonInline(ev).slice(0, 120) })),
    [d?.order_events],
  );
  const contextJson = useMemo(
    () =>
      d
        ? jsonPretty({
            strategy_context: d.strategy_context,
            model_context: d.model_context,
            quality: d.quality,
          })
        : "",
    [d],
  );
  const entryRiskKv = useMemo(
    () => (
      <>
        {kvTitle("entry", d?.entry)}
        {kvTitle("risk", d?.risk)}
      </>
    ),
    [d?.entry, d?.risk],
  );
  const pathExitKv = useMemo(
    () => (
      <>
        {kvTitle("position_path", d?.position_path)}
        {kvTitle("exit", d?.exit)}
      </>
    ),
    [d?.position_path, d?.exit],
  );

  return (
    <div className="acct-drawer-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <aside ref={boxRef} className="acct-drawer" role="dialog" aria-modal="true" aria-label={t("account.trades.drawer_aria", "Trade {ticket} forensics", { ticket: String(ticket) })}>
        <header aria-label={t("account.nav.trades", "Trades")}>
          <span>{t("account.trades.forensics", "Trade forensics")}</span>
          <span className="inline-mono tiny faint">#{ticket}</span>
          <button className="btn small ghost" style={{ marginInlineStart: "auto" }} onClick={() => trace.refetch()}>
            {t("account.trades.reload", "Reload")}
          </button>
          <button className="btn small" onClick={onClose}>
            {t("account.trades.close", "Close")} <kbd>esc</kbd>
          </button>
        </header>
        <div className="body">
          {trace.isPending ? (
            <Skeleton count={5} height={40} />
          ) : trace.isError ? (
            <ErrorState message={asErrorText(trace.error, t)} onRetry={() => trace.refetch()} />
          ) : !d ? (
            <EmptyState message={t("account.trades.no_trace", "No trace returned.")} />
          ) : (
            <>
              <section>
                <div className="statline">
                  <span>{d.trade?.symbol ?? "—"}</span>
                  <StatusBadge status={String(d.trade?.direction ?? "UNKNOWN")} />
                  <span>{t("account.trades.vol", "vol {vol}", { vol: numOrDash(d.trade?.volume) })}</span>
                  <span>{d.trade?.opened_at ? formatDateTime(d.trade?.opened_at as string) : DASH} → {d.trade?.closed_at ? formatDateTime(d.trade?.closed_at as string) : DASH}</span>
                  <span>{d.trade?.duration_sec != null ? `${Math.round(Number(d.trade.duration_sec))}s` : DASH}</span>
                </div>
                {d.outcome?.outcome && <span className={`badge ${d.outcome.outcome === "WIN" ? "good" : d.outcome.outcome === "LOSS" ? "bad" : "neutral"}`} style={{ marginTop: 6 }}>{d.outcome.outcome}</span>}
                {d.loss_attribution && <span className="acct-chip warn" style={{ marginInlineStart: 8 }}>{t("account.trades.loss_label", "loss: {loss}", { loss: String(d.loss_attribution) })}</span>}
              </section>

              <section>
                <div className="section-title">{t("account.trades.waterfall_title", "PnL waterfall (backend components)")}</div>
                <PnlWaterfall steps={buildTradeWaterfall(d.outcome ?? {})} formatValue={(v) => moneyOrDash(v)} emptyHint={t("account.trades.waterfall_empty", "outcome block missing — no decomposition to draw")} />
                <div className="statline" style={{ marginTop: 6 }}>
                  <span>{t("account.trades.net", "net {net}", { net: moneyOrDash(d.outcome?.net_pnl, true) })}</span>
                  <span>{t("account.trades.realized", "realized {realized}", { realized: d.outcome?.realized_r != null ? `${formatNumber(d.outcome.realized_r, 2)}R` : DASH })}</span>
                  <span>{t("account.trades.balance_after", "balance after {balance}", { balance: moneyOrDash(d.outcome?.balance_after) })}</span>
                  <span>{t("account.trades.equity_after", "equity after {equity}", { equity: moneyOrDash(d.outcome?.equity_after) })}</span>
                </div>
              </section>

              <section>
                <div className="section-title">{t("account.trades.identity_title", "identity chain")}</div>
                <dl className="kv">
                  {Object.entries(d.identity ?? {}).map(([k, v]) => (
                    <div key={k} style={{ display: "contents" }}>
                      <dt>{k}</dt>
                      <dd>{v === null || v === undefined || v === "" ? DASH : String(v)}</dd>
                    </div>
                  ))}
                </dl>
              </section>

              <section>
                <div className="section-title">{t("account.trades.path_title", "entry · risk · path · exit")}</div>
                <div className="grid cols-2">
                  <dl className="kv">
                    {entryRiskKv}
                  </dl>
                  <dl className="kv">
                    {pathExitKv}
                  </dl>
                </div>
              </section>

              {d.behavioral_flags && d.behavioral_flags.length > 0 && (
                <section>
                  <div className="section-title">{t("account.trades.flags_title", "behavioral flags")}</div>
                  <div className="statline">
                    {d.behavioral_flags.map((f) => (
                      <span className="acct-chip warn" key={f}>
                        {f}
                      </span>
                    ))}
                  </div>
                </section>
              )}

              {orderEvents.length > 0 && (
                <section>
                  <div className="section-title">{t("account.trades.events_title", "order events")}</div>
                  <DataTable headers={[{ label: t("account.trades.th_at", "at") }, { label: t("account.trades.th_event", "event") }, { label: t("account.trades.th_retcode", "retcode") }, { label: t("account.trades.th_detail", "detail") }]}>
                    {orderEvents.map(({ ev, text }, i) => (
                      <tr key={i}>
                        <td>{ev.timestamp ?? ev.at ? formatDateTime(String(ev.timestamp ?? ev.at)) : DASH}</td>
                        <td>{String(ev.event ?? ev.kind ?? "—")}</td>
                        <td>{String(ev.retcodes ?? ev.retcod ?? ev.retcode ?? DASH)}</td>
                        <td className="tiny muted">{text}</td>
                      </tr>
                    ))}
                  </DataTable>
                </section>
              )}

              {(d.notes ?? []).length > 0 && (
                <section>
                  <div className="section-title">{t("account.trades.notes_title", "backend notes (gaps are real)")}</div>
                  <ul className="small muted" style={{ paddingInlineStart: 16, margin: 0 }}>
                    {(d.notes ?? []).map((n) => (
                      <li key={n}>{n}</li>
                    ))}
                  </ul>
                </section>
              )}

              <details>
                <summary className="tiny muted" style={{ cursor: "pointer" }}>
                  {t("account.trades.ctx_summary", "strategy / model context + quality (raw)")}
                </summary>
                <pre>{contextJson}</pre>
              </details>
            </>
          )}
        </div>
      </aside>
    </div>
  );
}

/** One <dl> block per forensic section: label + value rows, missing = DASH. */
function kvTitle(title: string, obj: Record<string, unknown> | undefined) {
  const entries = Object.entries(obj ?? {});
  return (
    <>
      {entries.map(([k, v]) => (
        <div key={k} style={{ display: "contents" }}>
          <dt>
            {title}.{k}
          </dt>
          <dd>{v === null || v === undefined || v === "" ? DASH : typeof v === "object" ? jsonInline(v) : String(v)}</dd>
        </div>
      ))}
    </>
  );
}
