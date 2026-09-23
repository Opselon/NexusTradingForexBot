/**
 * Trade forensics drawer — ticket detail + PnL waterfall for one closed trade.
 *
 * Split out of ./TradesSection (perf lane): the drawer renders only while a
 * ticket is selected, so it ships as its own chunk behind a local Suspense
 * boundary in TradesSection. Data: GET /api/account/trades/{ticket} via
 * useTradeForensics; the waterfall decomposes only the components the trace
 * actually reports (a missing commission is an UNKNOWN bar).
 */

import { useRef } from "react";
import { useDialogA11y } from "../../../components/useDialogA11y";
import { DataTable, EmptyState, ErrorState, Skeleton, StatusBadge } from "@/components/primitives";
import { PnlWaterfall, buildTradeWaterfall } from "@/components/viz";
import { formatDateTime, formatNumber } from "@/lib/format";
import { useTradeForensics } from "../hooks";
import { DASH, errorProps, moneyOrDash, numOrDash } from "./shared";

export default function TradeForensicsDrawer({ ticket, onClose }: { ticket: number; onClose: () => void }) {
  const trace = useTradeForensics(ticket);

  const boxRef = useRef<HTMLElement | null>(null);
  useDialogA11y(boxRef, onClose);

  const d = trace.data;

  return (
    <div className="acct-drawer-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <aside ref={boxRef} className="acct-drawer" role="dialog" aria-modal="true" aria-label={`Trade ${ticket} forensics`}>
        <header aria-label="Trades">
          <span>Trade forensics</span>
          <span className="inline-mono tiny faint">#{ticket}</span>
          <button className="btn small ghost" style={{ marginInlineStart: "auto" }} onClick={() => trace.refetch()}>
            Reload
          </button>
          <button className="btn small" onClick={onClose}>
            Close <kbd>esc</kbd>
          </button>
        </header>
        <div className="body">
          {trace.isPending ? (
            <Skeleton count={5} height={40} />
          ) : trace.isError ? (
            <ErrorState {...errorProps(trace.error)} onRetry={() => trace.refetch()} />
          ) : !d ? (
            <EmptyState message="No trace returned." />
          ) : (
            <>
              <section>
                <div className="statline">
                  <span>{d.trade?.symbol ?? "—"}</span>
                  <StatusBadge status={String(d.trade?.direction ?? "UNKNOWN")} />
                  <span>vol {numOrDash(d.trade?.volume)}</span>
                  <span>{d.trade?.opened_at ? formatDateTime(d.trade?.opened_at as string) : DASH} → {d.trade?.closed_at ? formatDateTime(d.trade?.closed_at as string) : DASH}</span>
                  <span>{d.trade?.duration_sec != null ? `${Math.round(Number(d.trade.duration_sec))}s` : DASH}</span>
                </div>
                {d.outcome?.outcome && <span className={`badge ${d.outcome.outcome === "WIN" ? "good" : d.outcome.outcome === "LOSS" ? "bad" : "neutral"}`} style={{ marginTop: 6 }}>{d.outcome.outcome}</span>}
                {d.loss_attribution && <span className="acct-chip warn" style={{ marginInlineStart: 8 }}>loss: {d.loss_attribution}</span>}
              </section>

              <section>
                <div className="section-title">PnL waterfall (backend components)</div>
                <PnlWaterfall steps={buildTradeWaterfall(d.outcome ?? {})} formatValue={(v) => moneyOrDash(v)} emptyHint="outcome block missing — no decomposition to draw" />
                <div className="statline" style={{ marginTop: 6 }}>
                  <span>net {moneyOrDash(d.outcome?.net_pnl, true)}</span>
                  <span>realized {d.outcome?.realized_r != null ? `${formatNumber(d.outcome.realized_r, 2)}R` : DASH}</span>
                  <span>balance after {moneyOrDash(d.outcome?.balance_after)}</span>
                  <span>equity after {moneyOrDash(d.outcome?.equity_after)}</span>
                </div>
              </section>

              <section>
                <div className="section-title">identity chain</div>
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
                <div className="section-title">entry · risk · path · exit</div>
                <div className="grid cols-2">
                  <dl className="kv">
                    {kvTitle("entry", d.entry)}
                    {kvTitle("risk", d.risk)}
                  </dl>
                  <dl className="kv">
                    {kvTitle("position_path", d.position_path)}
                    {kvTitle("exit", d.exit)}
                  </dl>
                </div>
              </section>

              {d.behavioral_flags && d.behavioral_flags.length > 0 && (
                <section>
                  <div className="section-title">behavioral flags</div>
                  <div className="statline">
                    {d.behavioral_flags.map((f) => (
                      <span className="acct-chip warn" key={f}>
                        {f}
                      </span>
                    ))}
                  </div>
                </section>
              )}

              {(d.order_events ?? []).length > 0 && (
                <section>
                  <div className="section-title">order events</div>
                  <DataTable headers={[{ label: "at" }, { label: "event" }, { label: "retcode" }, { label: "detail" }]}>
                    {(d.order_events ?? []).map((ev, i) => (
                      <tr key={i}>
                        <td>{ev.timestamp ?? ev.at ? formatDateTime(String(ev.timestamp ?? ev.at)) : DASH}</td>
                        <td>{String(ev.event ?? ev.kind ?? "—")}</td>
                        <td>{String(ev.retcodes ?? ev.retcod ?? ev.retcode ?? DASH)}</td>
                        <td className="tiny muted">{JSON.stringify(ev).slice(0, 120)}</td>
                      </tr>
                    ))}
                  </DataTable>
                </section>
              )}

              {(d.notes ?? []).length > 0 && (
                <section>
                  <div className="section-title">backend notes (gaps are real)</div>
                  <ul className="small muted" style={{ paddingInlineStart: 16, margin: 0 }}>
                    {(d.notes ?? []).map((n) => (
                      <li key={n}>{n}</li>
                    ))}
                  </ul>
                </section>
              )}

              <details>
                <summary className="tiny muted" style={{ cursor: "pointer" }}>
                  strategy / model context + quality (raw)
                </summary>
                <pre>{JSON.stringify({ strategy_context: d.strategy_context, model_context: d.model_context, quality: d.quality }, null, 2)}</pre>
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
          <dd>{v === null || v === undefined || v === "" ? DASH : typeof v === "object" ? JSON.stringify(v) : String(v)}</dd>
        </div>
      ))}
    </>
  );
}
