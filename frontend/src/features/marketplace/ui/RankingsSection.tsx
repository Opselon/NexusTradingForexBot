/**
 * Rankings — GET /api/v1/marketplace/rankings?dimension=…
 *
 * The dimension selector re-queries the backend (the server owns ranking
 * derivation from the latest 14-factor snapshots); an unscored seed renders
 * NOT_AVAILABLE, never 0.
 */

import { useState } from "react";
import { DataTable, EmptyState, ErrorState, Panel, Segmented, Skeleton } from "@/components/primitives";
import { formatDateTime, formatNumber } from "@/lib/format";
import { useMktRankings } from "../hooks";
import { lifecycleLevel } from "../model";
import { FreshnessNote, asErrorText } from "./shared";

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
      <div className="tiny faint" style={{ marginBottom: 8 }}>
        profile {rankings.data?.profileId ?? "default"} · ranking derives from the latest stored 14-factor snapshot per seed (backend SQL, deduped)
      </div>
      {rankings.isPending ? (
        <Skeleton count={5} height={20} />
      ) : rankings.isError ? (
        <ErrorState message={asErrorText(rankings.error)} onRetry={() => rankings.refetch()} />
      ) : rows.length === 0 ? (
        <EmptyState message="No rankings computed yet." hint="Install seeds, then queue research runs — score snapshots populate this table." />
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
          {rows.map((r, i) => {
            const na = r.total === null || r.total === undefined;
            return (
              <tr key={r.seed_id}>
                <td>
                  <span className={`mkt-rank ${i < 3 ? `r${i + 1}` : ""}`}>{i + 1}</span>
                </td>
                <td>{r.seed_id}</td>
                <td>{r.family || "—"}</td>
                <td>
                  <span className={`badge ${lifecycleLevel(r.lifecycle)}`}>{String(r.lifecycle || "UNKNOWN")}</span>
                </td>
                <td className="num mkt-score-cell" style={na ? { color: "var(--text-faint)" } : undefined}>
                  {na ? "NOT_AVAILABLE" : formatNumber(r.total, 3)}
                </td>
                <td>{r.verdict ?? "—"}</td>
                <td>{r.scored_at ? formatDateTime(r.scored_at) : "—"}</td>
              </tr>
            );
          })}
        </DataTable>
      )}
    </Panel>
  );
}
