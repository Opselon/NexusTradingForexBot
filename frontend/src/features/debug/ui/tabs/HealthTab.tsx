/**
 * Health tab — /api/debug/health subsystem tiles.
 *
 * The operator can sort the tile wall by subsystem name or by backend status
 * word (alphabetical — no severity ladder is invented; the status word and
 * its semantic badge are the backend's own).
 */

import { useMemo } from "react";
import { Dot, MonoValue, PollControl, QuerySection, usePolling, FreshnessCaption } from "@/features/config/ui/kit";
import { StatusBadge } from "@/components/primitives";
import { useI18n } from "@/stores/i18nStore";
import type { DebugHealth } from "../../api";
import { useDebugHealthQuery } from "../../hooks";
import { healthLevel } from "../../model";
import { sortRows, useSortState, type SortState } from "../sorting";

type SortKey = "name" | "status";

export function HealthTab() {
  const t = useI18n((s) => s.t);
  const poll = usePolling(15_000);
  const query = useDebugHealthQuery(poll.paused);
  const api = useSortState<SortKey>({ key: "status", dir: "asc" });

  return (
    <QuerySection<DebugHealth>
      title={t("debug.health.title", "Debug subsystem health (/api/debug/health)")}
      accent
      query={query}
      skeletonRows={4}
      emptyMessage={t("debug.health.empty", "No subsystem data returned.")}
      right={
        <>
          <FreshnessCaption fetchedAtMs={query.dataUpdatedAt || null} intervalMs={15_000} stale={poll.paused} />
          <PollControl paused={poll.paused} onToggle={poll.togglePaused} intervalMs={15_000} busy={query.isFetching} />
        </>
      }
    >
      {(data) => (
        <div className="dbg-sec">
          <div className="l3-toolbar">
            <span className="dbg-overall">
              {t("debug.health.overall_label", "overall")} <StatusBadge status={data.overall_status} />
            </span>
            <span className="timestamp-note">{t("debug.health.checked", "checked {at}", { at: data.checked_at })}</span>
            <span className="dbg-sortctl" role="group" aria-label={t("debug.health.sort_aria", "sort subsystems")}>
              <span className="timestamp-note">{t("debug.features.sort", "sort")}</span>
              {(["status", "name"] as const).map((k) => (
                <button key={k} className={`dbg-chip ${api.sort.key === k ? "active" : ""}`} aria-pressed={api.sort.key === k} onClick={() => api.toggle(k)}>
                  {k}
                  {api.sort.key === k && <span className="dbg-chip-flag">{api.sort.dir === "asc" ? "↑" : "↓"}</span>}
                </button>
              ))}
            </span>
          </div>
          <SortedHealthGrid subsystems={data.subsystems} sort={api.sort} />
        </div>
      )}
    </QuerySection>
  );
}

/**
 * Sorted subsystem tiles + their per-tile metric rows.
 *
 * The sort chains are memoized so a re-render of the tab (polling tick,
 * AppShell clock re-render, ...) never re-sorts the backend's rows; deps are
 * exactly the input the sortRows read. Sorting semantics are unchanged: null
 * accessors still sink and the backend's own order is kept for equal keys.
 */
function SortedHealthGrid({ subsystems, sort }: { subsystems: DebugHealth["subsystems"]; sort: SortState<SortKey> }) {
  const sorted = useMemo(
    () => sortRows(subsystems, (s) => (sort.key === "name" ? s.name : s.status), sort.dir),
    [subsystems, sort.key, sort.dir],
  );
  return (
    <div className="l3-health-grid dbg-health">
      {sorted.map((sub) => (
        <div key={sub.name} className={`l3-health-cell dbg-tile ${healthLevel(sub.status)}`}>
          <div className="name">
            <span>{sub.name}</span>
            <Dot status={sub.status} />
          </div>
          <StatusBadge status={sub.status} />
          <div className="detail">{sub.detail}</div>
          {Object.keys(sub.metrics ?? {}).length > 0 && (
            <SortedMetrics metrics={sub.metrics ?? {}} />
          )}
        </div>
      ))}
    </div>
  );
}

/** Metrics of ONE tile, sorted by key — memoized per tile so a re-sort of the
 *  outer wall does not re-sort the other tiles' metrics. */
function SortedMetrics({ metrics }: { metrics: Record<string, unknown> | undefined }) {
  const sorted = useMemo(() => sortRows(Object.entries(metrics ?? {}), ([k]) => k, "asc"), [metrics]);
  return (
    <div className="metrics">
      {sorted.map(([k, v]) => (
        <div className="mrow" key={k}>
          <span className="mk">{k}</span>
          <span>
            <MonoValue value={v} />
          </span>
        </div>
      ))}
    </div>
  );
}
