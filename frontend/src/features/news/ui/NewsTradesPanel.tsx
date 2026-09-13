/**
 * Trade linkage probe — GET /api/news/trades/{trade_id}.
 *
 * Ticket input is validated (digits, required) before the query runs; the
 * result list is whatever the news DB links, including an explicit empty.
 */

import { useState } from "react";
import { DataTable, EmptyState, ErrorState, Panel } from "@/components/primitives";
import { formatDateTime } from "@/lib/format";
import { useNewsTradeLinks } from "../hooks";
import { FreshnessNote, asErrorText } from "./shared";

function ticketError(raw: string): string | null {
  const v = raw.trim();
  if (v === "") return "ticket is required";
  if (!/^\d{1,15}$/.test(v)) return "ticket must be digits (broker ticket)";
  return null;
}

export function NewsTradesPanel() {
  const [draft, setDraft] = useState("");
  const [ticket, setTicket] = useState<string | null>(null);
  const links = useNewsTradeLinks(ticket);
  const err = ticketError(draft);

  return (
    <Panel
      title="News ↔ trade linkage"
      right={
        <>
          <form
            className="news-inline-input"
            onSubmit={(e) => {
              e.preventDefault();
              if (!err) setTicket(draft.trim());
            }}
          >
            <input
              className={`input ${draft !== "" && err ? "invalid" : ""}`}
              style={{ width: 130 }}
              placeholder="trade ticket…"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              aria-label="trade ticket"
              aria-invalid={draft !== "" && !!err}
            />
            <button className="btn small primary" type="submit" disabled={!!err}>
              Lookup
            </button>
          </form>
          {ticket !== null && (
            <button className="btn small ghost" onClick={() => setTicket(null)}>
              Clear
            </button>
          )}
        </>
      }
    >
      {draft !== "" && err && <div className="news-field-error">{err}</div>}
      {ticket === null ? (
        <EmptyState message="Enter a broker ticket to see which news articles were linked to that decision." hint="Links are recorded by the news gate when an article influenced a signal." />
      ) : links.isPending ? (
        <div className="viz-empty">querying trade links…</div>
      ) : links.isError ? (
        <ErrorState message={asErrorText(links.error)} onRetry={() => links.refetch()} />
      ) : (links.data ?? []).length === 0 ? (
        <EmptyState message={`No news links recorded for trade #${ticket}.`} hint="The gate logged no news evidence for this ticket." />
      ) : (
        <>
          <DataTable headers={[{ label: "article" }, { label: "strategy" }, { label: "linked at" }, { label: "detail" }]}>
            {(links.data ?? []).map((l, i) => (
              <tr key={i}>
                <td className="inline-mono">{String(l.article_id ?? "—")}</td>
                <td>{String(l.strategy_id ?? "—")}</td>
                <td>{l.linked_at ? formatDateTime(String(l.linked_at)) : "—"}</td>
                <td className="tiny muted">{String(l.note ?? l.reason ?? "—")}</td>
              </tr>
            ))}
          </DataTable>
          <FreshnessNote updatedAtMs={links.dataUpdatedAt ?? null} label="links" />
        </>
      )}
    </Panel>
  );
}
