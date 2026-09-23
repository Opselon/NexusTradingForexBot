/**
 * State tab — canonical /api/debug/state viewer (raw JSON or per-section
 * scalars + collapsed depth-1 JSON). Backend truth only: an UNAVAILABLE
 * section renders its own reason + correlation id, never a blank panel.
 */

import { useMemo, useState } from "react";
import { EmptyState } from "@/components/primitives";
import { FreshnessCaption, JsonView, KeyValueList, PollControl, QuerySection, scalarRows, usePolling } from "@/features/config/ui/kit";
import type { DebugState } from "../../api";
import { useDebugStateQuery } from "../../hooks";
import { DEBUG_SECTIONS } from "../../model";

/** One tab-level derivation: which section chips exist + the selected
 *  section's rows (scalarRows + _section filter). Memoized per
 *  (snapshot, section) identity so a re-render of the tab (freshness ticks,
 *  polling state flips) never re-walks the payload; the branch logic and the
 *  rendered values are byte-identical to the inline code it replaces. */
function useSectionView(snap: DebugState | undefined, section: string) {
  return useMemo(() => {
    const chips = snap ? DEBUG_SECTIONS.filter((s) => s in snap) : [];
    if (!snap) return { chips, kind: "absent" as const, obj: null, rows: null };
    const sec = (snap as Record<string, unknown>)[section];
    if (!sec) return { chips, kind: "absent" as const, obj: null, rows: null };
    const obj = sec as Record<string, unknown>;
    if (obj.available === false) return { chips, kind: "unavailable" as const, obj, rows: null };
    return {
      chips,
      kind: "rows" as const,
      obj,
      rows: scalarRows(obj).filter(([k]) => k !== "_section"),
    };
  }, [snap, section]);
}

export function StateTab() {
  const poll = usePolling(20_000);
  const query = useDebugStateQuery(poll.paused);
  const [section, setSection] = useState<string>("runtime");
  const [raw, setRaw] = useState(false);
  const view = useSectionView(query.data, section);

  return (
    <QuerySection<DebugState>
      title="Canonical debug snapshot (/api/debug/state)"
      accent
      query={query}
      skeletonRows={6}
      emptyMessage="Snapshot unavailable."
      right={
        <>
          <FreshnessCaption fetchedAtMs={query.dataUpdatedAt || null} intervalMs={20_000} note={query.data?.snapshot_id ? `id ${query.data.snapshot_id}` : undefined} stale={poll.paused} />
          <PollControl paused={poll.paused} onToggle={poll.togglePaused} intervalMs={20_000} busy={query.isFetching} />
          <button className="btn small ghost" onClick={() => setRaw((r) => !r)}>{raw ? "typed view" : "raw JSON"}</button>
        </>
      }
    >
      {(snap) => (
        <div className="dbg-sec">
          {snap.available === false && (
            <div className="l3-note bad">Snapshot flagged unavailable: {String(snap.reason ?? "UNKNOWN")} — the sections below are what the backend could still provide.</div>
          )}
          <div className="dbg-chips" aria-label="state sections">
            <button className={`dbg-chip ${section === "__all" ? "active" : ""}`} onClick={() => setSection("__all")}>
              all
            </button>
            {view.chips.map((s) => (
              <button key={s} className={`dbg-chip ${section === s ? "active" : ""}`} aria-pressed={section === s} onClick={() => setSection(s)}>
                {s}
                {((snap as Record<string, unknown>)[s] as { available?: boolean } | undefined)?.available === false && <span className="dbg-chip-flag" title="section reports available=false">!</span>}
              </button>
            ))}
          </div>
          {raw || section === "__all" ? (
            <div tabIndex={0} className="l3-scroll dbg-json">
              <JsonView value={snap} name="state" />
            </div>
          ) : (
            (() => {
              if (view.kind === "absent") return <EmptyState message={`Section "${section}" absent from this snapshot.`} />;
              if (view.kind === "unavailable") {
                const obj = view.obj;
                return (
                  <div className="l3-note warn">
                    {section}: UNAVAILABLE — {String(obj.reason ?? "no reason given")}
                    {obj.correlation_id ? ` (correlation ${String(obj.correlation_id)})` : ""}
                  </div>
                );
              }
              return (
                <>
                  <KeyValueList rows={view.rows} />
                  <div tabIndex={0} className="l3-scroll sm dbg-json" style={{ marginTop: 8 }}>
                    <JsonView value={view.obj} name={section} depth={1} />
                  </div>
                </>
              );
            })()
          )}
        </div>
      )}
    </QuerySection>
  );
}
