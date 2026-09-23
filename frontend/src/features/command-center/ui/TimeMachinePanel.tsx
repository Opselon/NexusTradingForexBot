/**
 * Time machine panel — bounds + debounced lazy frame slider (extracted from
 * CommandCenterPage during the BUG-312 analysis wave; behavior unchanged).
 *
 * Frames are fetched LAZILY per settled slider instant (350 ms debounce,
 * placeholderData keeps the previous frame on screen while swapping).
 */

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { DataTable, EmptyState, Panel, Skeleton } from "@/components/primitives";
import { formatDateTime, formatTime } from "@/lib/format";
import { DistBars, useDebounced } from "../../research/ui/lane5Kit";
import { arr, str } from "../model";
import { commandCenterQueries, useTimeMachineFrame } from "../useCases";
import { ccRetry, ccRetryDelay } from "../useCases";
import { useI18n } from "@/stores/i18nStore";

export function TimeMachine() {
  const t = useI18n((s) => s.t);
  const boundsQ = useQuery({
    queryKey: ["command-center", "tm-bounds"],
    queryFn: ({ signal }) => commandCenterQueries.tmBounds(signal),
    retry: ccRetry,
    retryDelay: ccRetryDelay,
  });
  const bounds = boundsQ.data?.available === true ? boundsQ.data : null;
  const range = useMemo(() => {
    const lo = bounds?.earliest ? new Date(bounds.earliest).getTime() : NaN;
    const hi = bounds?.latest ? new Date(bounds.latest).getTime() : NaN;
    return Number.isNaN(lo) || Number.isNaN(hi) || hi <= lo ? null : { lo, hi };
  }, [bounds]);
  const [sliderMs, setSliderMs] = useState<number | null>(null);
  const effectiveMs = sliderMs ?? range?.hi ?? null;
  const debouncedIso = useDebounced(effectiveMs === null ? null : new Date(effectiveMs).toISOString(), 350);
  const frameQ = useTimeMachineFrame(debouncedIso);
  // perf: zone census built once per frame instead of every render
  // (deps: frame — the only reactive value read; same insertion order/values).
  const frame = frameQ.data;
  const zoneRows = useMemo(() => {
    const byZone = new Map<string, number>();
    for (const n of arr(frame?.nodes)) {
      const z = str(n.zone) ?? "UNKNOWN";
      byZone.set(z, (byZone.get(z) ?? 0) + 1);
    }
    return [...byZone.entries()].map(([label, count]) => ({ label, count }));
  }, [frame]);

  if (boundsQ.isPending) return <Panel title={t("command-center.panel.time_machine", "Time machine")}><Skeleton count={3} /></Panel>;
  if (!bounds || !range) {
    return (
      <Panel title={t("command-center.panel.time_machine", "Time machine")} tight>
        <EmptyState message={t("command-center.empty.no_history", "No historical events yet")} hint={boundsQ.data?.reason ?? t("command-center.empty.no_history_hint", "timemachine/bounds answered available:false — nothing to scrub")} />
      </Panel>
    );
  }

  const transitions = arr(frame?.transitions);

  return (
    <Panel
      title={t("command-center.panel.time_machine_title", "Time machine — fleet state at an instant")}
      right={<span className="tiny muted">{formatDateTime(debouncedIso)} · {t("command-center.tm.events_in_range", "{n} events in range", { n: String(bounds.total_events ?? 0) })}</span>}
      tight
    >
      <div style={{ display: "grid", gap: 8 }}>
        <input
          type="range"
          min={range.lo}
          max={range.hi}
          step={60_000}
          value={effectiveMs ?? range.hi}
          onChange={(e) => setSliderMs(Number(e.target.value))}
          style={{ width: "100%", accentColor: "var(--accent)" }}
          aria-label={t("command-center.a11y.timeline_scrubber", "timeline scrubber")}
        />
        <div style={{ display: "flex", justifyContent: "space-between" }} className="tiny faint">
          <span>{formatDateTime(bounds.earliest)}</span>
          <span>{formatTime(debouncedIso ?? bounds.latest)}</span>
          <span>{formatDateTime(bounds.latest)}</span>
        </div>
        {frameQ.isFetching && <div className="tiny muted">{t("command-center.tm.frame_loading", "frame loading (lazy, debounced 350 ms)…")}</div>}
        {frame?.available === false ? (
          <EmptyState message={frame.reason ?? t("command-center.tm.frame_unavailable", "frame not available")} />
        ) : (
          <div className="grid cols-2" style={{ marginTop: 6 }}>
            <div>
              <div className="section-title">{t("command-center.tm.zone_census", "zone census at instant")}</div>
              <DistBars rows={zoneRows} />
            </div>
            <div>
              <div className="section-title">{t("command-center.tm.transitions", "transitions in this frame (±60 s)")}</div>
              {transitions.length === 0 ? (
                <EmptyState message={t("command-center.empty.no_transitions", "No lifecycle transition happened at this instant.")} />
              ) : (
                <DataTable headers={[{ label: t("command-center.th.strategy", "strategy") }, { label: "→" }, { label: t("command-center.th.actor", "actor") }, { label: t("command-center.th.reason", "reason") }]}>
                  {transitions.slice(0, 12).map((t, i) => (
                    <tr key={i}>
                      <td className="inline-mono tiny">{str(t.strategy_id)?.slice(0, 12) ?? "—"}</td>
                      <td>
                        <span className="badge">{str(t.to_state) ?? "—"}</span>
                      </td>
                      <td className="tiny">{str(t.actor) ?? "—"}</td>
                      <td className="tiny muted">{str(t.reason) ?? ""}</td>
                    </tr>
                  ))}
                </DataTable>
              )}
            </div>
          </div>
        )}
      </div>
    </Panel>
  );
}
