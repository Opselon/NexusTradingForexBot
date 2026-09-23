/**
 * Rankings — GET /api/v1/marketplace/rankings?dimension=…
 *
 * The dimension selector re-queries the backend (the server owns ranking
 * derivation from the latest 14-factor snapshots); an unscored seed renders
 * NOT_AVAILABLE, never 0.
 */

import { memo, useState } from "react";
import { DataTable, EmptyState, ErrorState, Panel, Segmented, Skeleton } from "@/components/primitives";
import { formatDateTime, formatNumber } from "@/lib/format";
import { useMktRankings } from "../hooks";
import { lifecycleLevel } from "../model";
import type { MktRankingRow } from "../types";
import { FreshnessNote, asErrorText, requestIdOf } from "./shared";

/** Constant style objects hoisted out of the row map (no per-render allocs). */
const PROFILE_NOTE_STYLE = { marginBottom: 8 } as const;

/** One ranking row. Memoized: dimension switches and freshness captions no
 *  longer re-render every row. */
const RankingRow = memo(function RankingRow({ row, rank }: { row: MktRankingRow; rank: number }) {
  const na = row.total === null || row.total === undefined;
  return (
    <tr key={row.seed_id}>
      <td>
        <span className={`mkt-rank ${rank < 3 ? `r${rank + 1}` : ""}`}>{rank + 1}</span>
      </td>
      <td>{row.seed_id}</td>
      <td>{row.family || "—"}</td>
      <td>
        <span className={`badge ${lifecycleLevel(row.lifecycle)}`}>{String(row.lifecycle || "UNKNOWN")}</span>
      </td>
      <td className="num mkt-score-cell" style={na ? NA_CELL_STYLE : undefined}>
        {na ? "NOT_AVAILABLE" : formatNumber(row.total, 3)}
      </td>
      <td>{row.verdict ?? "—"}</td>
      <td>{row.scored_at ? formatDateTime(row.scored_at) : "—"}</td>
    </tr>
  );
});

/** Faint style for an unscored (NOT_AVAILABLE) total cell. */
const NA_CELL_STYLE = { color: "var(--text-faint)" } as const;

const DIMENSIONS = [
  { id: "OVERALL", label: "Overall" },
  { id: "PROFITABILITY", label: "Profitability" },
  { id: "ROBUSTNESS", label: "Robustness" },
  { id: "OOS", label: "OOS" },
  { id: "LIVE_READINESS", label: "Live" },
];

export function RankingsSection() {
  const [dim, setDim] = useState("OVERALL");
  const rankings = useMktRankings(dim);

  const rows = rankings.data?.rows ?? [];

  return (
    <Panel
      title={`Rankings · ${rankings.data?.dimension ?? dim.toUpperCase()}`}
      right={
        <>
          <Segmented options={DIMENSIONS} value={dim} onChange={setDim} />
          <FreshnessNote updatedAtMs={rankings.dataUpdatedAt ?? null} label="rankings" />
        </>
      }
    >
      <div className="tiny faint" style={PROFILE_NOTE_STYLE}>
        profile {rankings.data?.profileId ?? "default"} · GET /api/v1/marketplace/rankings derives from the latest stored 14-factor snapshot per seed (backend SQL, deduped)
      </div>
      {rankings.isPending ? (
        <Skeleton count={5} height={20} />
      ) : rankings.isError ? (
        <ErrorState
          message={asErrorText(rankings.error)}
          requestId={requestIdOf(rankings.error)}
          onRetry={() => rankings.refetch()}
        />
      ) : rows.length === 0 ? (
        <EmptyState
          message="No rankings computed yet."
          hint="GET /api/v1/marketplace/rankings returned an empty set — install seeds, then queue research runs to populate it."
        />
      ) : (
        <DataTable
          headers={[
            { label: "#" },
            { label: "seed" },
            { label: "family" },
            { label: "lifecycle" },
            { label: "total", num: true },
            { label: "verdict" },
            { label: "scored at" },
          ]}
        >
          {rows.map((r, i) => (
            <RankingRow key={r.seed_id} row={r} rank={i} />
          ))}
        </DataTable>
      )}
    </Panel>
  );
}
