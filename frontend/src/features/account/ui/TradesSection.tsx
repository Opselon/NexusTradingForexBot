/**
 * Closed trades table + forensic detail drawer — analytics-studio pass.
 *
 * PURPOSE:  the trades table as a sortable, heat-ramped instrument grid with
 *           a persisted density toggle; the forensic drawer (PnL waterfall,
 *           identity chain, raw blocks) is unchanged in behavior and styling.
 * OWNER:    uiux-w6-account
 * CONSUMES: useAccountTrades/useTradeForensics, TradeVM (model), components/viz
 *           (PnlWaterfall), components/primitives, useDialogA11y, shared
 *           formatters, studio-math (heatIntensity/peakAbs), usePersistedState
 * PROVIDES: TradesSection (page section)
 * INVARIANTS: rows come from /api/account/trades (broker-reconstructed, ledger
 *           fallback — the backend decides); heat intensity scales ONLY
 *           against |net PnL| on the current page and the signed TOKEN color
 *           still carries the sign, so a $2 loss cannot look like a $2k one;
 *           density changes padding only — no column is ever hidden;
 *           the drawer still opens on ticket click and Esc still closes it.
 * EXTEND:   new columns must exist on TradeVM (model.ts) — never widen the
 *           raw DTO here.
 */

import type { CSSProperties } from "react";
import { useMemo, useRef, useState } from "react";
import { useDialogA11y } from "../../../components/useDialogA11y";
import { EmptyState, ErrorState, Panel, Skeleton, StatusBadge } from "@/components/primitives";
import { PnlWaterfall, buildTradeWaterfall } from "@/components/viz";
import { formatDateTime, formatNumber, formatPrice } from "@/lib/format";
import { useAccountTrades, useTradeForensics } from "../hooks";
import type { TradeVM } from "../model";
import { DASH, FreshnessNote, asErrorText, jsonInline, jsonPretty, moneyOrDash, numOrDash } from "./shared";
import { heatIntensity, peakAbs } from "./studio-math";
import {
  DEFAULT_DENSITY,
  DENSITY_VALUES,
  isDensity,
  usePersistedState,
  type Density,
} from "./usePersistedState";
import "./account.css";
import "./account-studio.css";
import "./account-studio-grid.css";

const PAGE = 25;

type SortKey = "ticket" | "symbol" | "direction" | "volume" | "netPnl" | "realizedR" | "closedAt";
type SortDir = 1 | -1;
interface SortState {
  key: SortKey;
  dir: SortDir;
}

/**
 * Header model — mirrors the LEGACY column set exactly (every original
 * column survives; `prices` is display-only because the DTO has no single
 * scalar to sort on without client math on entry/exit).
 */
const COLUMNS: Array<{ key: SortKey; label: string; num: boolean }> = [
  { key: "ticket", label: "ticket", num: false },
  { key: "symbol", label: "symbol", num: false },
  { key: "direction", label: "dir", num: false },
  { key: "volume", label: "vol", num: true },
  { key: "netPnl", label: "net PnL", num: true },
  { key: "realizedR", label: "R", num: true },
  { key: "closedAt", label: "closed", num: false },
];

function readSort(v: unknown): SortState | null {
  if (typeof v !== "object" || v === null) return null;
  const o = v as Record<string, unknown>;
  if (typeof o.key !== "string") return null;
  if (o.dir !== 1 && o.dir !== -1) return null;
  return COLUMNS.some((c) => c.key === o.key) ? { key: o.key as SortKey, dir: o.dir } : null;
}

/** The raw sortable value for a column; null = "no data" (never 0). */
function sortValue(t: TradeVM, key: SortKey): number | string | null {
  switch (key) {
    case "ticket":
      return t.numericId;
    case "symbol":
      return t.symbol;
    case "direction":
      return t.direction;
    case "volume":
      return t.volume;
    case "netPnl":
      return t.netPnl;
    case "realizedR":
      return t.realizedR;
    case "closedAt":
      return t.closedAt;
  }
}

/**
 * Comparator over the view model. No-data rows ALWAYS sink — the sink is
 * decided before the direction multiplier, so a null never rises to the top
 * of a descending net-PnL sort and reads as a huge loss (BUG-020 lineage).
 */
function compareRows(a: TradeVM, b: TradeVM, key: SortKey, dir: SortDir): number {
  const av = sortValue(a, key);
  const bv = sortValue(b, key);
  const aN = av === null || av === undefined;
  const bN = bv === null || bv === undefined;
  if (aN && bN) return 0;
  if (aN) return 1;
  if (bN) return -1;
  const c =
    typeof av === "number" && typeof bv === "number"
      ? av - bv
      : String(av).localeCompare(String(bv));
  return c * dir;
}

export function TradesSection() {
  const [offset, setOffset] = useState(0);
  const [ticket, setTicket] = useState<number | null>(null);
  const [density, setDensity] = usePersistedState<Density>("density", DEFAULT_DENSITY, { isValid: isDensity });
  const [sort, setSort] = usePersistedState<SortState>("trades.sort", { key: "closedAt", dir: -1 }, {
    isValid: (v) => readSort(v) !== null,
  });
  const trades = useAccountTrades(PAGE, offset);

  const rows = trades.data?.trades ?? [];

  const sorted = useMemo(() => {
    const out = [...rows];
    out.sort((a, b) => compareRows(a, b, sort.key, sort.dir));
    return out;
  }, [rows, sort]);

  // peak abs net PnL on the CURRENT page — heat scale, no outside data
  const heatPeak = useMemo(() => peakAbs(rows.map((t) => t.netPnl)), [rows]);

  const toggleSort = (key: SortKey) => {
    setSort((s) => (s.key === key ? { key, dir: (s.dir * -1) as SortDir } : { key, dir: -1 }));
  };

  return (
    <Panel
      title={`Closed trades (${rows.length})`}
      right={
        <>
          <div className="acc-toggle" role="group" aria-label="Row density (padding only)">
            {DENSITY_VALUES.map((d) => (
              <button
                key={d}
                className={density === d ? "active" : ""}
                onClick={() => setDensity(d)}
                aria-pressed={density === d}
                title={`${d} rows — padding only, no data hidden`}
              >
                {d}
              </button>
            ))}
          </div>
          <button className="btn small ghost" onClick={() => setOffset((o) => Math.max(0, o - PAGE))} disabled={offset === 0 || trades.isFetching}>
            ← newer
          </button>
          <span className="timestamp-note">offset {offset}</span>
          <button className="btn small ghost" onClick={() => setOffset((o) => o + PAGE)} disabled={rows.length < PAGE || trades.isFetching}>
            older →
          </button>
          <FreshnessNote updatedAtMs={trades.dataUpdatedAt ?? null} label="trades" staleAfterMs={180_000} />
        </>
      }
    >
      {trades.isPending ? (
        <Skeleton count={6} height={20} />
      ) : trades.isError ? (
        <ErrorState message={asErrorText(trades.error)} onRetry={() => trades.refetch()} />
      ) : rows.length === 0 ? (
        <EmptyState message="No historical trades found yet." hint="Broker history sync pending — no rows are invented in the meantime." />
      ) : (
        <>
          <div className="acc-tt-wrap" data-density={density}>
            <table className="acc-tt" data-density={density}>
              <thead>
                <tr>
                  {COLUMNS.slice(0, 4).map((c) => (
                    <th
                      key={c.key}
                      scope="col"
                      className={c.num ? "num sortable" : "sortable"}
                      aria-sort={sort.key === c.key ? (sort.dir === 1 ? "ascending" : "descending") : "none"}
                    >
                      <button type="button" onClick={() => toggleSort(c.key)} title={`sort by ${c.label}`}>
                        <span>{c.label}</span>
                        <span className="acc-tt-th-ico">{sort.key === c.key ? (sort.dir === 1 ? "▲" : "▼") : "↕"}</span>
                      </button>
                    </th>
                  ))}
                  <th scope="col">entry → exit</th>
                  <th scope="col" className="num sortable" aria-sort={sort.key === "netPnl" ? (sort.dir === 1 ? "ascending" : "descending") : "none"}>
                    <button type="button" onClick={() => toggleSort("netPnl")} title="sort by net PnL">
                      <span>net PnL</span>
                      <span className="acc-tt-th-ico">{sort.key === "netPnl" ? (sort.dir === 1 ? "▲" : "▼") : "↕"}</span>
                    </button>
                  </th>
                  <th
                    scope="col"
                    className="sortable"
                    aria-sort={sort.key === "realizedR" ? (sort.dir === 1 ? "ascending" : "descending") : "none"}
                  >
                    <button type="button" onClick={() => toggleSort("realizedR")} title="sort by R">
                      <span>R</span>
                      <span className="acc-tt-th-ico">{sort.key === "realizedR" ? (sort.dir === 1 ? "▲" : "▼") : "↕"}</span>
                    </button>
                  </th>
                  <th scope="col">status</th>
                  <th
                    scope="col"
                    className="sortable"
                    aria-sort={sort.key === "closedAt" ? (sort.dir === 1 ? "ascending" : "descending") : "none"}
                  >
                    <button type="button" onClick={() => toggleSort("closedAt")} title="sort by closed time">
                      <span>closed</span>
                      <span className="acc-tt-th-ico">{sort.key === "closedAt" ? (sort.dir === 1 ? "▲" : "▼") : "↕"}</span>
                    </button>
                  </th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((t, i) => {
                  const heat = heatIntensity(t.netPnl, heatPeak);
                  const pos = t.netPnl === null ? null : t.netPnl >= 0;
                  return (
                    <tr
                      key={`${t.id}-${i}`}
                      data-sel={ticket !== null && t.numericId === ticket ? "1" : "0"}
                    >
                      <td>
                        {t.numericId !== null ? (
                          <button className="acc-tt-ticket" onClick={() => setTicket(t.numericId)} title={`open forensics for #${t.id}`}>
                            #{t.id}
                          </button>
                        ) : (
                          <span className="acc-tt-ticket plain">#{t.id}</span>
                        )}
                      </td>
                      <td>{t.symbol}</td>
                      <td>
                        <span className={`badge ${t.direction === "BUY" ? "good" : t.direction === "SELL" ? "bad" : "unknown"}`}>{t.direction}</span>
                      </td>
                      <td className="num">{formatNumber(t.volume)}</td>
                      <td className="inline-mono">
                        {t.entryPrice !== null ? formatPrice(t.entryPrice) : DASH} → {t.exitPrice !== null ? formatPrice(t.exitPrice) : DASH}
                      </td>
                      <td
                        className="num"
                        data-heat={pos === null ? undefined : pos ? "pos" : "neg"}
                        style={heat > 0 ? ({ "--heat-w": `${heat * 100}%` } as CSSProperties) : undefined}
                      >
                        <span className={pos === null ? "acc-tt-dim" : pos ? "acc-tt-pos" : "acc-tt-neg"}>
                          {moneyOrDash(t.netPnl, true)}
                        </span>
                      </td>
                      <td className="num">
                        <span className="acc-tt-dim">{t.realizedR === null ? DASH : `${t.realizedR.toFixed(2)}R`}</span>
                      </td>
                      <td>{t.status}</td>
                      <td>{t.closedAt ? formatDateTime(t.closedAt) : DASH}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="tiny faint" style={{ marginBlockStart: 6 }}>
            heat ramp: tint width ∝ |net PnL| ÷ largest |net PnL| on this page (derived from on-screen values); sign still carries the token color.
          </div>
        </>
      )}
      {ticket !== null && <TradeDetailDrawer ticket={ticket} onClose={() => setTicket(null)} />}
    </Panel>
  );
}

function TradeDetailDrawer({ ticket, onClose }: { ticket: number; onClose: () => void }) {
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
            <ErrorState message={asErrorText(trace.error)} onRetry={() => trace.refetch()} />
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
                    {entryRiskKv}
                  </dl>
                  <dl className="kv">
                    {pathExitKv}
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

              {orderEvents.length > 0 && (
                <section>
                  <div className="section-title">order events</div>
                  <div className="table-wrap" style={{ maxHeight: 260 }}>
                    <table className="data-table">
                      <thead>
                        <tr>
                          <th scope="col">at</th>
                          <th scope="col">event</th>
                          <th scope="col">retcode</th>
                          <th scope="col">detail</th>
                        </tr>
                      </thead>
                      <tbody>
                        {orderEvents.map(({ ev, text }, i) => (
                          <tr key={i}>
                            <td>{ev.timestamp ?? ev.at ? formatDateTime(String(ev.timestamp ?? ev.at)) : DASH}</td>
                            <td>{String(ev.event ?? ev.kind ?? "—")}</td>
                            <td>{String(ev.retcodes ?? ev.retcod ?? ev.retcode ?? DASH)}</td>
                            <td className="tiny muted">{text}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
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
