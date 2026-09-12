/**
 * AccountMicroCard — account micro-summary for the pro dashboard.
 *
 * Read-only mirror of `snapshot.account` (backend `AccountState`). Every field
 * is null-safe: absent broker data renders "—" (UNKNOWN) and never a
 * fabricated zero. `available: false` is surfaced explicitly — a confident
 * card over an unavailable account would be a lie. Reuses the existing `.kv`
 * grid; layout extras are inline so this lane adds no shared CSS.
 */

import type { AccountState } from "@/types/domain";
import { formatMoney, formatNumber, formatPct, formatPnl } from "@/lib/format";

export function AccountMicroCard({ acct }: { acct: AccountState }) {
  const idBits = [acct.company, acct.server, acct.login].filter(
    (v) => v !== null && v !== undefined && v !== "",
  );
  const rows: Array<{ k: string; v: string; cls?: string }> = [
    { k: "balance", v: formatMoney(acct.balance) },
    { k: "equity", v: formatMoney(acct.equity) },
    {
      k: "floating",
      v: formatPnl(acct.floating),
      cls: acct.floating === null ? undefined : acct.floating < 0 ? "pnl-neg" : "pnl-pos",
    },
    { k: "margin / free", v: `${formatMoney(acct.margin)} / ${formatMoney(acct.margin_free)}` },
    { k: "margin level", v: acct.margin_level === null ? "—" : `${formatNumber(acct.margin_level, 1)}%` },
    { k: "leverage", v: acct.leverage === null ? "—" : `1:${formatNumber(acct.leverage, 0)}` },
    {
      k: "drawdown",
      v: formatPct(acct.drawdown),
      cls: acct.drawdown !== null && acct.drawdown > 5 ? "pnl-neg" : undefined,
    },
    { k: "win rate", v: acct.win_rate === null ? "—" : formatPct(acct.win_rate, 1) },
    { k: "positions / orders", v: `${acct.open_positions ?? "—"} / ${acct.pending_orders ?? "—"}` },
  ];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        {acct.available ? (
          <span className="inline-mono small" title="broker identity from backend account section">
            {idBits.length > 0 ? idBits.join(" · ") : "—"}
            {acct.currency ? ` (${acct.currency})` : ""}
          </span>
        ) : (
          <span className="badge unknown">ACCOUNT UNAVAILABLE</span>
        )}
        <span className="badge" title="terminal trade_allowed (backend)">
          {acct.trade_allowed === null ? "—" : acct.trade_allowed ? "TRADE ALLOWED" : "TRADE RESTRICTED"}
        </span>
        <span className="timestamp-note" style={{ marginLeft: "auto" }}>
          source {acct.source ?? "—"}
        </span>
      </div>
      <dl className="kv">
        {rows.map((r) => (
          <div key={r.k} style={{ display: "contents" }}>
            <dt>{r.k}</dt>
            <dd className={r.cls}>{r.v}</dd>
          </div>
        ))}
      </dl>
      {acct.credit !== null && (
        <div className="tiny faint inline-mono">credit {formatMoney(acct.credit)}</div>
      )}
    </div>
  );
}
