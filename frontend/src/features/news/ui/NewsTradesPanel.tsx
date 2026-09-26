/**
 * Trade linkage probe — GET /api/news/trades/{trade_id}.
 *
 * Ticket input is validated (digits, required) before the query runs; the
 * result list is whatever the news DB links, including an explicit empty.
 */

import { useMemo, useState } from "react";
import { EmptyState, ErrorState, Panel } from "@/components/primitives";
import { formatDateTime } from "@/lib/format";
import { useI18n } from "@/stores/i18nStore";
import { useNewsTradeLinks } from "../hooks";
import { NewsSortTable, type SortColumn } from "./NewsSortTable";
import { FreshnessNote, asErrorText, type TFunc } from "./shared";

/** Sort key for an unknown payload field: only a real string/number orders; else null (sinks last). */
function scalar(v: unknown): string | number | null {
  if (typeof v === "string") return v !== "" ? v : null;
  if (typeof v === "number" && Number.isFinite(v)) return v;
  return null;
}

function ticketError(raw: string, t: TFunc): string | null {
  const v = raw.trim();
  if (v === "") return t("news.trades.err_required", "ticket is required");
  if (!/^\d{1,15}$/.test(v)) return t("news.trades.err_digits", "ticket must be digits (broker ticket)");
  return null;
}

export function NewsTradesPanel() {
  const t = useI18n((s) => s.t);
  const [draft, setDraft] = useState("");
  const [ticket, setTicket] = useState<string | null>(null);
  const links = useNewsTradeLinks(ticket);
  const err = ticketError(draft, t);

  // link rows are Record<string, unknown> — read only the documented keys and
  // never invent a sort key: an absent field sorts last, not as zero.
  const columns = useMemo<SortColumn<Record<string, unknown>>[]>(
    () => [
      { key: "article", label: t("news.trades.h_article", "article"), sortValue: (l) => scalar(l.article_id) },
      { key: "strategy", label: t("news.trades.h_strategy", "strategy"), sortValue: (l) => scalar(l.strategy_id) },
      { key: "linked", label: t("news.trades.h_linked_at", "linked at"), sortValue: (l) => (l.linked_at ? Date.parse(String(l.linked_at)) : null) },
      { key: "detail", label: t("news.trades.h_detail", "detail"), sortValue: (l) => scalar(l.note) ?? scalar(l.reason) },
    ],
    [t],
  );

  return (
    <Panel
      title={t("news.trades.title", "News ↔ trade linkage")}
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
              placeholder={t("news.trades.ticket_placeholder", "trade ticket…")}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              aria-label={t("news.trades.ticket_label", "trade ticket")}
              aria-invalid={draft !== "" && !!err}
            />
            <button className="btn small primary" type="submit" disabled={!!err}>
              {t("news.trades.lookup", "Lookup")}
            </button>
          </form>
          {ticket !== null && (
            <button className="btn small ghost" onClick={() => setTicket(null)}>
              {t("news.trades.clear", "Clear")}
            </button>
          )}
        </>
      }
    >
      {draft !== "" && err && <div className="news-field-error">{err}</div>}
      {ticket === null ? (
        <EmptyState
          message={t(
            "news.trades.empty",
            "Enter a broker ticket to see which news articles were linked to that decision.",
          )}
          hint={t(
            "news.trades.empty_hint",
            "Links are recorded by the news gate when an article influenced a signal.",
          )}
        />
      ) : links.isPending ? (
        <div className="viz-empty">{t("news.trades.loading", "querying trade links…")}</div>
      ) : links.isError ? (
        <ErrorState message={asErrorText(links.error, t)} onRetry={() => links.refetch()} />
      ) : (links.data ?? []).length === 0 ? (
        <EmptyState
          message={t("news.trades.no_links", "No news links recorded for trade #{n}.", { n: ticket })}
          hint={t("news.trades.no_links_hint", "The gate logged no news evidence for this ticket.")}
        />
      ) : (
        <>
          <NewsSortTable
            columns={columns}
            rows={links.data ?? []}
            rowKey={(l, i) => (l.article_id != null ? String(l.article_id) : `link-${i}`)}
            label={t("news.trades.title", "News ↔ trade linkage")}
            renderCells={(l) => (
              <>
                <td className="inline-mono">{String(l.article_id ?? "—")}</td>
                <td>{String(l.strategy_id ?? "—")}</td>
                <td>{l.linked_at ? formatDateTime(String(l.linked_at)) : "—"}</td>
                <td className="tiny muted">{String(l.note ?? l.reason ?? "—")}</td>
              </>
            )}
          />
          <FreshnessNote updatedAtMs={links.dataUpdatedAt ?? null} label={t("news.fresh.links", "links")} />
        </>
      )}
    </Panel>
  );
}
