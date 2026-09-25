/**
 * Trace tab — /api/debug/trace/{execution_id}: read-only forensic join
 * (audit_signals + audit_orders) rendered as a chronological timeline.
 *
 * When the backend answers with rows-less payloads it carries its own
 * reason (e.g. NO_AUDIT_DB) — that reason is surfaced verbatim instead of a
 * misleading "0 stages" empty state.
 */

import { useMemo, useState } from "react";
import { EmptyState, ErrorState, Panel, Skeleton } from "@/components/primitives";
import { useI18n } from "@/stores/i18nStore";
import { useTraceQuery } from "../../hooks";
import { traceTimeline } from "../../model";

export function TraceTab() {
  const t = useI18n((s) => s.t);
  const [id, setId] = useState("");
  const [submitted, setSubmitted] = useState<string | null>(null);
  const trace = useTraceQuery(submitted);
  const shapeOk = id.trim() === "" || /^[A-Za-z0-9_-]{4,}$/.test(id.trim());
  // traceTimeline walks every signal/order row (parse + sort): memo deps are
  // exactly the payload it reads, so unrelated re-renders never rebuild it.
  const timeline = useMemo(() => (trace.data ? traceTimeline(trace.data) : []), [trace.data]);
  const rowCount = (trace.data?.signal?.length ?? 0) + (trace.data?.orders?.length ?? 0);

  return (
    <Panel
      title={t("debug.trace.title", "Execution trace (/api/debug/trace/{execution_id})", { path: "/api/debug/trace/{execution_id}" })}
      accent
      right={<span className="timestamp-note">{t("debug.trace.right", "read-only join: audit_signals + audit_orders")}</span>}
    >
      <div className="dbg-sec">
        <div className="l3-toolbar">
          <input className="input" style={{ minWidth: 280 }} aria-label={t("debug.trace.id_aria", "Execution id")} placeholder="EXEC-…" value={id} aria-invalid={!shapeOk} onChange={(e) => setId(e.target.value)} />
          <button className="btn primary" disabled={!shapeOk || id.trim() === ""} onClick={() => setSubmitted(id.trim())}>
            {t("debug.trace.action", "Trace")}
          </button>
          {!shapeOk && <span className="l3-field-error">{t("debug.trace.id_error", "id must be ≥4 chars of [A-Za-z0-9_-]")}</span>}
        </div>
        {!submitted ? (
          <EmptyState message={t("debug.trace.empty", "No execution id submitted.")} hint={t("debug.trace.empty_hint", "Find ids in audit/orders views or the legacy forensic console.")} />
        ) : trace.isPending ? (
          <Skeleton count={4} />
        ) : trace.isError ? (
          <ErrorState message={trace.error instanceof Error ? trace.error.message : t("debug.trace.failed", "trace failed")} onRetry={() => void trace.refetch()} />
        ) : !trace.data?.available ? (
          <div className="l3-note warn">{t("debug.trace.unavailable", "trace unavailable: {reason}", { reason: String(trace.data?.reason ?? "UNKNOWN") })}</div>
        ) : (
          <>
            <div className="timestamp-note" style={{ marginBottom: 6 }}>
              {t("debug.trace.summary", "{id} · {signal} signal row(s) · {order} order row(s)", {
                id: submitted,
                signal: trace.data.signal?.length ?? 0,
                order: trace.data.orders?.length ?? 0,
              })}
              {trace.data.db_path ? t("debug.trace.db_path", " · db {db}", { db: trace.data.db_path }) : ""}
            </div>
            {rowCount === 0 ? (
              <>
                <div className="l3-note warn">
                  {t("debug.trace.rows_empty", "Rows empty{reason}. The id is valid; the join found nothing to place on the timeline.", {
                    reason: trace.data.reason ? t("debug.trace.rows_reason", " — backend reason: {r}", { r: String(trace.data.reason) }) : "",
                  })}
                </div>
                <EmptyState message={t("debug.trace.nothing", "Nothing to trace for this execution id.")} hint={t("debug.trace.nothing_hint", "Try an id from a window where the audit DB was attached.")} />
              </>
            ) : (
              <div className="l3-timeline dbg-timeline">
                {timeline.map((row, i) => (
                  <div className="l3-tl-row dbg-tl-row" key={i}>
                    <span className="t">{row.ts}</span>
                    <span className="body">
                      <b>{row.stage}</b>
                      <div className="tiny faint l3-cell" title={row.detail}>
                        {row.detail}
                      </div>
                    </span>
                  </div>
                ))}
                {timeline.length === 0 && <EmptyState message={t("debug.trace.no_stages", "Rows found but no timestamped stages to place on the timeline.")} />}
              </div>
            )}
          </>
        )}
      </div>
    </Panel>
  );
}
