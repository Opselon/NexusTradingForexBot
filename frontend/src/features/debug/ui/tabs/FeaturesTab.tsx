/**
 * Features tab — /api/debug/features: the live feature-contract grid.
 *
 * Cards are sorted client-side (index / name / numeric value) over the RAW
 * backend rows before formatting, so a sort can never reorder what a value
 * IS — only how the wall is listed. Null values sink, anomalies stay
 * highlightable via the existing filter.
 */

import { useMemo, useState } from "react";
import { MetricCard } from "@/components/primitives";
import { FreshnessCaption, PollControl, QuerySection, usePolling } from "@/features/config/ui/kit";
import type { DebugFeatures } from "../../api";
import { useDebugFeaturesQuery } from "../../hooks";
import { featureCells } from "../../model";
import { sortRows, useSortState } from "../sorting";

type SortKey = "index" | "name" | "value";

export function FeaturesTab() {
  const poll = usePolling(15_000);
  const query = useDebugFeaturesQuery(poll.paused);
  const [onlyAnomalies, setOnlyAnomalies] = useState(false);
  const api = useSortState<SortKey>({ key: "index", dir: "asc" });

  const cells = useMemo(() => {
    const rows = query.data?.features ?? [];
    const sorted = sortRows(
      rows,
      (r) => (api.sort.key === "name" ? r.name : api.sort.key === "value" ? r.value : r.index),
      api.sort.dir,
      api.sort.key === "value" ? "number" : "string",
    );
    return featureCells(sorted).filter((c) => (onlyAnomalies ? c.status !== "VALID" : true));
  }, [query.data, api.sort, onlyAnomalies]);

  return (
    <QuerySection<DebugFeatures>
      title="Feature contract (/api/debug/features)"
      accent
      query={query}
      skeletonRows={6}
      emptyMessage="Backend returned no feature rows."
      right={
        <>
          <FreshnessCaption fetchedAtMs={query.dataUpdatedAt || null} intervalMs={15_000} note={query.data?.timestamp_utc ? `vector @ ${query.data.timestamp_utc}` : undefined} stale={poll.paused} />
          <PollControl paused={poll.paused} onToggle={poll.togglePaused} intervalMs={15_000} busy={query.isFetching} />
          <button className="btn small ghost" onClick={() => setOnlyAnomalies((v) => !v)}>{onlyAnomalies ? "show all" : "anomalies only"}</button>
        </>
      }
    >
      {(data) => (
        <div className="dbg-sec">
          <div className="l3-toolbar">
            <MetricCard label="engine" value={data.engine_online ? "ONLINE" : "OFFLINE"} tone={data.engine_online ? "pos" : "neg"} />
            <MetricCard label="valid" value={`${data.features.length - data.anomaly_count}/${data.feature_count}`} tone={data.all_valid ? "pos" : "neg"} />
            <MetricCard label="NaN / Inf" value={`${data.nan_count} / ${data.inf_count}`} tone={data.anomaly_count ? "neg" : "dim"} />
            <MetricCard
              label="vector age"
              value={data.age_seconds === null ? "—" : `${data.age_seconds.toFixed(1)}s`}
              tone={data.is_stale ? "neg" : "pos"}
              sub={data.is_stale ? `STALE (>${data.stale_threshold_seconds}s)` : "fresh"}
            />
            <span className="dbg-sortctl" role="group" aria-label="sort feature grid">
              <span className="timestamp-note">sort</span>
              {(["index", "name", "value"] as const).map((k) => (
                <button key={k} className={`dbg-chip ${api.sort.key === k ? "active" : ""}`} aria-pressed={api.sort.key === k} onClick={() => api.toggle(k)}>
                  {k}
                  {api.sort.key === k && <span className="dbg-chip-flag">{api.sort.dir === "asc" ? "↑" : "↓"}</span>}
                </button>
              ))}
            </span>
          </div>
          {data.is_stale && (
            <div className="l3-note warn" style={{ marginBottom: 8 }}>
              The feature snapshot is older than {data.stale_threshold_seconds}s — the tick pipeline is not feeding the model right now.
            </div>
          )}
          <div className="l3-grid-features dbg-feats">
            {cells.map((c) => (
              <div key={`${c.index}-${c.key}`} className={`l3-feat dbg-feat ${c.status === "VALID" ? "" : c.level === "warn" ? "warn" : "bad"}`} title={`${c.key} · ${c.name} · ${c.status}`}>
                <span className="n">
                  <span className="dbg-feat-ix">{String(c.index).padStart(2, "0")}</span>
                  {c.name}
                </span>
                <span className="v">{c.value}</span>
              </div>
            ))}
          </div>
          {cells.length === 0 && <div className="l3-note">No rows match the current filter.</div>}
        </div>
      )}
    </QuerySection>
  );
}
