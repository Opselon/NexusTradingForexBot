/**
 * IPC tab — /api/debug/ipc-telemetry: recent MT5 broker order events.
 *
 * Sortable columns (timestamp / action / state / latency). The state column
 * falls back to `execution_mode` when the event carries no state/status key,
 * so a payload without those keys shows the real mode instead of "—".
 */

import { EmptyState, MetricCard } from "@/components/primitives";
import { FreshnessCaption, PollControl, QuerySection, usePolling } from "@/features/config/ui/kit";
import { useI18n } from "@/stores/i18nStore";
import type { IpcEvent, IpcTelemetry } from "../../api";
import { useIpcTelemetryQuery } from "../../hooks";
import { SortTh, sortRows, useSortState, type SortApi } from "../sorting";

type SortKey = "ts" | "event" | "state" | "latency";

const pickKey = (ev: IpcEvent, re: RegExp): string | undefined => Object.keys(ev).find((k) => re.test(k));
const cellText = (ev: IpcEvent, re: RegExp): string | null => {
  const k = pickKey(ev, re);
  return k === undefined ? null : String(ev[k] ?? "—");
};

function eventTs(ev: IpcEvent): string | null {
  return cellText(ev, /timestamp|time|date/i);
}
function eventName(ev: IpcEvent): string | null {
  return cellText(ev, /event|action|kind/i);
}
function eventState(ev: IpcEvent): string | null {
  // state/status first, then execution_mode (real broker mode), else null → sink
  return cellText(ev, /state|status/i) ?? cellText(ev, /execution_mode/i);
}
function eventLatency(ev: IpcEvent): number | null {
  const k = pickKey(ev, /latency/i);
  if (k === undefined) return null;
  const n = Number(ev[k]);
  return Number.isFinite(n) ? n : null;
}

export function IpcTab() {
  const t = useI18n((s) => s.t);
  const poll = usePolling(20_000);
  const query = useIpcTelemetryQuery(poll.paused);
  const api = useSortState<SortKey>({ key: null, dir: "desc" });

  return (
    <QuerySection<IpcTelemetry>
      title={t("debug.ipc.title", "MT5 IPC telemetry (/api/debug/ipc-telemetry)")}
      accent
      query={query}
      skeletonRows={5}
      emptyMessage={t("debug.ipc.empty", "No broker execution events recorded yet.")}
      right={
        <>
          <FreshnessCaption fetchedAtMs={query.dataUpdatedAt || null} intervalMs={20_000} note={t("debug.ipc.avg_note", "avg latency {v} ms", { v: query.data?.avg_latency_ms ?? "—" })} stale={poll.paused} />
          <PollControl paused={poll.paused} onToggle={poll.togglePaused} intervalMs={20_000} busy={query.isFetching} />
        </>
      }
    >
      {(data) => {
        const rows: IpcEvent[] =
          api.sort.key === null
            ? data.events
            : sortRows(
                data.events,
                (ev) =>
                  api.sort.key === "ts"
                    ? eventTs(ev)
                    : api.sort.key === "event"
                      ? eventName(ev)
                      : api.sort.key === "state"
                        ? eventState(ev)
                        : eventLatency(ev),
                api.sort.dir,
                api.sort.key === "latency" ? "number" : "string",
              );
        return (
          <div className="dbg-sec">
            <div className="l3-toolbar">
              <MetricCard label={t("debug.ipc.events", "events")} value={data.event_count} />
              <MetricCard label={t("debug.ipc.avg_latency", "avg latency")} value={`${data.avg_latency_ms} ms`} />
              <MetricCard label={t("debug.ipc.positions", "positions / pendings")} value={`${data.exposure.positions} / ${data.exposure.pendings}`} sub={t("debug.ipc.cap", "cap {n}", { n: data.max_total_exposure })} />
              <span className="timestamp-note">{api.sort.key === null ? t("debug.ipc.backend_order", "backend order — click a header to sort") : t("debug.ipc.sorted_by", "sorted by {key} {dir}", { key: api.sort.key, dir: api.sort.dir })}</span>
            </div>
            <div tabIndex={0} className="l3-scroll dbg-table-wrap">
              <table className="data-table dbg-table">
                <thead>
                  <tr>
                    <SortTh<SortKey> label={t("debug.ipc.th_timestamp", "timestamp")} col="ts" api={api as SortApi<SortKey>} title={t("debug.ipc.ts_title", "event timestamp")} />
                    <SortTh<SortKey> label={t("debug.ipc.th_event", "event")} col="event" api={api as SortApi<SortKey>} />
                    <SortTh<SortKey> label={t("debug.ipc.th_state", "state")} col="state" api={api as SortApi<SortKey>} title={t("debug.ipc.state_title", "state / status, falling back to execution_mode")} />
                    <th className="plain">{t("debug.ipc.th_reason", "reason / retcode")}</th>
                    <SortTh<SortKey> label={t("debug.ipc.th_latency", "latency")} col="latency" api={api as SortApi<SortKey>} num />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((ev, i) => {
                    const reason = cellText(ev, /reason|retcode|code/i);
                    const lat = eventLatency(ev);
                    return (
                      <tr key={String(ev.id ?? i)}>
                        <td>{eventTs(ev) ?? "—"}</td>
                        <td>{eventName(ev) ?? "—"}</td>
                        <td>{eventState(ev) ? <span className="badge neutral">{eventState(ev)}</span> : "—"}</td>
                        <td className="l3-cell" title={reason ?? undefined}>
                          {reason ?? "—"}
                        </td>
                        <td className="num">{lat === null ? "—" : `${lat}`}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {rows.length === 0 && <EmptyState message={t("debug.ipc.no_events", "Event log empty for this window.")} />}
            </div>
          </div>
        );
      }}
    </QuerySection>
  );
}
