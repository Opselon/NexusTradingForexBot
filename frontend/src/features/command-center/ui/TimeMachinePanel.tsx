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

export function TimeMachine() {
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

  if (boundsQ.isPending) return <Panel title="Time machine"><Skeleton count={3} /></Panel>;
  if (!bounds || !range) {
    return (
      <Panel title="Time machine" tight>
        <EmptyState message="No historical events yet" hint={boundsQ.data?.reason ?? "timemachine/bounds answered available:false — nothing to scrub"} />
      </Panel>
    );
  }

  const frame = frameQ.data;
  const nodes = arr(frame?.nodes);
  const byZone = new Map<string, number>();
  for (const n of nodes) byZone.set(str(n.zone) ?? "UNKNOWN", (byZone.get(str(n.zone) ?? "UNKNOWN") ?? 0) + 1);
  const transitions = arr(frame?.transitions);

  return (
    <Panel
      title="Time machine — fleet state at an instant"
      right={<span className="tiny muted">{formatDateTime(debouncedIso)} · {String(bounds.total_events ?? 0)} events in range</span>}
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
          aria-label="timeline scrubber"
        />
        <div style={{ display: "flex", justifyContent: "space-between" }} className="tiny faint">
          <span>{formatDateTime(bounds.earliest)}</span>
          <span>{formatTime(debouncedIso ?? bounds.latest)}</span>
          <span>{formatDateTime(bounds.latest)}</span>
        </div>
        {frameQ.isFetching && <div className="tiny muted">frame loading (lazy, debounced 350 ms)…</div>}
        {frame?.available === false ? (
          <EmptyState message={frame.reason ?? "frame not available"} />
        ) : (
          <div className="grid cols-2" style={{ marginTop: 6 }}>
            <div>
              <div className="section-title">zone census at instant</div>
              <DistBars rows={[...byZone.entries()].map(([label, count]) => ({ label, count }))} />
            </div>
            <div>
              <div className="section-title">transitions in this frame (±60 s)</div>
              {transitions.length === 0 ? (
                <EmptyState message="No lifecycle transition happened at this instant." />
              ) : (
                <DataTable headers={[{ label: "strategy" }, { label: "→" }, { label: "actor" }, { label: "reason" }]}>
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
