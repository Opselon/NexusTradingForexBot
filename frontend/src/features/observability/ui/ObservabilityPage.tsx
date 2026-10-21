/**
 * PURPOSE:  Observability / Log Center — the operator's view over the
 *           persisted operational event sink: severity summary, server-side
 *           search/filter, keyset paging, event detail with a redacted
 *           context/trace, correlation drill-down, safe export, live tail.
 * OWNER:    observability lane
 * CONSUMES: @/components/primitives kit (Panel/SeverityBadge/EmptyState/
 *           ErrorState/Skeleton — the kit has no Button primitive, so buttons
 *           use the global `btn` classes), @tanstack/react-query, ./model +
 *           ./api (the v1 log read plane), ./observability.css (prefix obs-).
 * PROVIDES: default export ObservabilityPage (route /observability).
 * INVARIANTS: every rendered value is a field the backend returned; a failed
 *             source renders ErrorState, never a fake empty list; zero events
 *             renders EmptyState, never an error; exports go through the
 *             authorized export endpoint, not a client-side dump; the
 *             context/trace is rendered exactly as the server redacted it.
 * EXTEND:    add a section component in this folder; keep this page a shell
 *            (queries + section composition) so it stays under 500 lines.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useI18n } from "@/stores/i18nStore";
import { useQuery } from "@tanstack/react-query";
import type { ShellPageProps } from "@/app/featureModule";
import {
  EmptyState,
  ErrorState,
  Panel,
  SeverityBadge,
  Skeleton,
} from "@/components/primitives";
import { logsApi } from "../api";
import {
  defaultWindow,
  eventTone,
  formatTimestamp,
  isQueryActive,
  parseContext,
  SEVERITIES,
  updateQuery,
  type LogEventDto,
  type LogQuery,
  type Severity,
} from "../model";
import "./observability.css";

const PAGE_SIZE = 50;
/** Live-tail interval; comfortably above the sink's write cadence. */
const LIVE_TAIL_MS = 5_000;

export default function ObservabilityPage(props: ShellPageProps) {
  void props;
  const t = useI18n((s) => s.t);
  const [query, setQuery] = useState<LogQuery>(defaultWindow);
  const [draft, setDraft] = useState<LogQuery>(defaultWindow);
  const [cursor, setCursor] = useState<number | undefined>(undefined);
  const [rows, setRows] = useState<LogEventDto[]>([]);
  const [openId, setOpenId] = useState<number | null>(null);
  const [live, setLive] = useState(false);
  const liveRef = useRef(live);
  liveRef.current = live;

  const summaryQ = useQuery({
    queryKey: ["obs", "summary", query.since, query.until],
    queryFn: ({ signal }) => logsApi.summary({ since: query.since, until: query.until }, signal),
    refetchInterval: LIVE_TAIL_MS,
    retry: false,
  });

  const pageQ = useQuery({
    queryKey: ["obs", "page", query, cursor],
    queryFn: ({ signal }) => logsApi.search(query, PAGE_SIZE, cursor, signal),
    retry: false,
  });

  // Accumulate pages: a cursor change appends; a filter change resets (the
  // apply/reset handlers clear `rows`, so this effect only ever appends).
  useEffect(() => {
    const data = pageQ.data;
    if (!data) return;
    setRows((prev) => (cursor === undefined ? data.items : [...prev, ...data.items]));
  }, [pageQ.data, cursor]);

  const refresh = useCallback(() => setCursor(undefined), []);

  // Live tail: restart from the newest on the interval while enabled, which
  // is the honest semantic for a tail (a cursor walk would not be a tail).
  useEffect(() => {
    if (!live) return;
    const id = window.setInterval(() => {
      if (liveRef.current) refresh();
    }, LIVE_TAIL_MS);
    return () => window.clearInterval(id);
  }, [live, refresh]);

  const applyDraft = useCallback(() => {
    setQuery(draft);
    setCursor(undefined);
    setRows([]);
  }, [draft]);

  const resetFilters = useCallback(() => {
    const w = defaultWindow();
    setDraft(w);
    setQuery(w);
    setCursor(undefined);
    setRows([]);
  }, []);

  const loadMore = useCallback(() => {
    const next = pageQ.data?.next_before_id;
    if (next != null) setCursor(next);
  }, [pageQ.data]);

  const detailQ = useQuery({
    queryKey: ["obs", "detail", openId],
    queryFn: ({ signal }) => logsApi.detail(openId as number, signal),
    enabled: openId != null,
    retry: false,
  });
  const correlateQ = useQuery({
    queryKey: ["obs", "correlate", openId],
    queryFn: ({ signal }) => logsApi.correlate(openId as number, signal),
    enabled: openId != null,
    retry: false,
  });

  const counts = useMemo(() => {
    const c = summaryQ.data?.counts ?? {};
    return SEVERITIES.map((s) => ({ level: s, value: c[s] ?? null }));
  }, [summaryQ.data]);

  const hasError = pageQ.isError;
  const isEmpty = !pageQ.isLoading && rows.length === 0 && !hasError;

  return (
    <div className="obs-root">
      <header className="obs-header">
        <div>
          <h1>{t("observability.title", "Log Center")}</h1>
          <p className="obs-subtitle">
            {t("observability.subtitle", "Persisted operational events")}
          </p>
        </div>
        <div className="obs-header-actions">
          <button className="btn small" onClick={refresh} disabled={pageQ.isFetching}>
            {t("observability.action.refresh", "Refresh")}
          </button>
          <button
            className={`btn small ${live ? "primary" : ""}`}
            aria-pressed={live}
            onClick={() => setLive((v) => !v)}
          >
            {t("observability.action.live", "Live tail")}
          </button>
          <a className="btn small obs-export" href={logsApi.exportUrl(query, "json")} download>
            {t("observability.action.exportJson", "Export JSON")}
          </a>
          <a className="btn small obs-export" href={logsApi.exportUrl(query, "csv")} download>
            {t("observability.action.exportCsv", "Export CSV")}
          </a>
        </div>
      </header>

      <section
        className="obs-summary"
        aria-label={t("observability.summary.counts", "Counts by severity")}
      >
        {counts.map(({ level, value }) => (
          <div key={level} className={`obs-count obs-count-${eventTone(level)}`}>
            <SeverityBadge severity={level} />
            <span className="obs-count-value">
              {value === null ? t("observability.summary.unavailable", "n/a") : value}
            </span>
          </div>
        ))}
      </section>

      <Panel
        title={t("observability.filter.title", "Filters")}
        subtitle={t("observability.window.note", "Default window 24h; max 90 days")}
      >
        <div className="obs-filters">
          <label>
            <span>{t("observability.filter.severity", "Severity")}</span>
            <select
              value={draft.severity}
              onChange={(e) => setDraft((d) => updateQuery(d, "severity", e.target.value))}
            >
              <option value="">{t("observability.filter.all", "All")}</option>
              {SEVERITIES.map((s: Severity) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>{t("observability.filter.component", "Component")}</span>
            <input
              value={draft.component}
              placeholder="engine.broker"
              onChange={(e) => setDraft((d) => updateQuery(d, "component", e.target.value))}
            />
          </label>
          <label>
            <span>{t("observability.filter.event", "Event name")}</span>
            <input
              value={draft.event}
              placeholder="EV_ERROR"
              onChange={(e) => setDraft((d) => updateQuery(d, "event", e.target.value))}
            />
          </label>
          <label>
            <span>{t("observability.filter.search", "Text search")}</span>
            <input
              value={draft.text}
              placeholder="order rejected"
              onChange={(e) => setDraft((d) => updateQuery(d, "text", e.target.value))}
            />
          </label>
          <label>
            <span>{t("observability.filter.since", "Since")}</span>
            <input
              type="datetime-local"
              value={toLocalInput(draft.since)}
              onChange={(e) =>
                setDraft((d) => updateQuery(d, "since", fromLocalInput(e.target.value)))
              }
            />
          </label>
          <label>
            <span>{t("observability.filter.until", "Until")}</span>
            <input
              type="datetime-local"
              value={toLocalInput(draft.until)}
              onChange={(e) =>
                setDraft((d) => updateQuery(d, "until", fromLocalInput(e.target.value)))
              }
            />
          </label>
          <div className="obs-filter-actions">
            <button className="btn small primary" onClick={applyDraft} disabled={pageQ.isFetching}>
              {t("observability.filter.apply", "Apply")}
            </button>
            <button className="btn small" onClick={resetFilters} disabled={!isQueryActive(draft)}>
              {t("observability.filter.reset", "Reset")}
            </button>
          </div>
        </div>
      </Panel>

      <Panel title={t("observability.table.title", "Events")}>
        {pageQ.isLoading && rows.length === 0 ? (
          <Skeleton count={6} />
        ) : hasError ? (
          <>
            <ErrorState
              message={t("observability.state.error", "Log source unavailable")}
              onRetry={refresh}
            />
            {rows.length > 0 && <EventTable rows={rows} onOpen={setOpenId} t={t} />}
          </>
        ) : isEmpty ? (
          <EmptyState
            message={t("observability.state.empty", "No events in this window")}
            hint={t("observability.state.emptyHint", "Widen the time range or clear the filters.")}
          />
        ) : (
          <>
            <EventTable rows={rows} onOpen={setOpenId} t={t} />
            {pageQ.data?.has_more && (
              <div className="obs-more">
                <button className="btn small" onClick={loadMore} disabled={pageQ.isFetching}>
                  {t("observability.action.more", "Load more")}
                </button>
              </div>
            )}
          </>
        )}
      </Panel>

      {openId != null && (
        <DetailDrawer
          event={detailQ.data ?? null}
          correlate={correlateQ.data ?? null}
          loading={detailQ.isLoading}
          onClose={() => setOpenId(null)}
          onPick={setOpenId}
          t={t}
        />
      )}
    </div>
  );
}

/** Local <input type="datetime-local"> needs a naive local string. */
function toLocalInput(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** And back to ISO for the server's ISO-8601 window contract. */
function fromLocalInput(local: string): string {
  if (!local) return "";
  const d = new Date(local);
  return Number.isNaN(d.getTime()) ? "" : d.toISOString();
}

type T = ReturnType<typeof useI18n.getState>["t"];

function EventTable({
  rows,
  onOpen,
  t,
}: {
  rows: LogEventDto[];
  onOpen: (id: number) => void;
  t: T;
}) {
  return (
    <div className="obs-table-wrap">
      <table className="obs-table">
        <thead>
          <tr>
            <th>{t("observability.table.time", "Time")}</th>
            <th>{t("observability.table.level", "Level")}</th>
            <th>{t("observability.table.component", "Component")}</th>
            <th>{t("observability.table.event", "Event")}</th>
            <th>{t("observability.table.message", "Message")}</th>
            <th>{t("observability.table.repeat", "Repeat")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr
              key={r.id}
              className={`obs-row obs-row-${eventTone(String(r.level ?? ""))}`}
              onClick={() => onOpen(r.id)}
            >
              <td className="obs-time">{formatTimestamp(r.timestamp)}</td>
              <td>
                <SeverityBadge severity={String(r.level ?? "")} />
              </td>
              <td className="obs-mono">{r.logger_name || "—"}</td>
              <td className="obs-mono">{r.event_name || "—"}</td>
              <td className="obs-message">{r.error_message || "—"}</td>
              <td className="obs-repeat">
                {(r.repeat_count ?? 1) > 1 ? `×${r.repeat_count}` : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DetailDrawer({
  event,
  correlate,
  loading,
  onClose,
  onPick,
  t,
}: {
  event: LogEventDto | null;
  correlate: { correlation_id: string; events: LogEventDto[]; note?: string } | null;
  loading: boolean;
  onClose: () => void;
  onPick: (id: number) => void;
  t: T;
}) {
  const ctx = parseContext(event?.context_json);
  return (
    <div className="obs-drawer-backdrop" onClick={onClose} role="presentation">
      <aside
        className="obs-drawer"
        role="dialog"
        aria-modal="true"
        aria-label={t("observability.detail.title", "Event detail")}
        onClick={(e) => e.stopPropagation()}
      >
        <header>
          <h2>{t("observability.detail.title", "Event detail")}</h2>
          <button className="btn small" onClick={onClose}>
            {t("observability.detail.close", "Close")}
          </button>
        </header>
        {loading || !event ? (
          <Skeleton count={6} />
        ) : (
          <>
            <dl className="obs-fields">
              <dt>ID</dt>
              <dd>{event.id}</dd>
              <dt>{t("observability.table.time", "Time")}</dt>
              <dd>{formatTimestamp(event.timestamp)}</dd>
              <dt>{t("observability.table.level", "Level")}</dt>
              <dd>
                <SeverityBadge severity={String(event.level)} />
              </dd>
              <dt>{t("observability.detail.correlationId", "Correlation ID")}</dt>
              <dd className="obs-mono">
                {event.correlation_id || t("observability.detail.none", "none")}
              </dd>
              <dt>{t("observability.table.event", "Event")}</dt>
              <dd className="obs-mono">{event.event_name || "—"}</dd>
              <dt>{t("observability.table.message", "Message")}</dt>
              <dd>{event.error_message || "—"}</dd>
            </dl>

            <section>
              <h3>{t("observability.detail.context", "Context (redacted)")}</h3>
              {ctx && Object.keys(ctx).length > 0 ? (
                <pre className="obs-pre">{JSON.stringify(ctx, null, 2)}</pre>
              ) : (
                <p className="obs-muted">—</p>
              )}
            </section>

            <section>
              <h3>{t("observability.detail.trace", "Error trace")}</h3>
              {event.full_trace ? (
                <pre className="obs-pre obs-trace">{event.full_trace}</pre>
              ) : (
                <p className="obs-muted">—</p>
              )}
            </section>

            <section>
              <h3>{t("observability.detail.correlate", "Related events")}</h3>
              {correlate && correlate.events.length > 1 ? (
                <ul className="obs-correlated">
                  {correlate.events.map((e) => (
                    <li key={e.id} className={e.id === event.id ? "obs-current" : ""}>
                      <button type="button" onClick={() => onPick(e.id)}>
                        <span className="obs-mono">#{e.id}</span>
                        <SeverityBadge severity={String(e.level)} />
                        <span className="obs-message">
                          {e.error_message || e.event_name || "—"}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="obs-muted">
                  {correlate?.note ||
                    t("observability.detail.noRelated", "No other events share this correlation id.")}
                </p>
              )}
            </section>
          </>
        )}
      </aside>
    </div>
  );
}
