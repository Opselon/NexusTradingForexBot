/**
 * PURPOSE:  Rankings storefront — podium treatment for positions 1–3 above
 *           the full backend-ordered table, plus a score bar next to the
 *           raw total.
 * OWNER:    uiux-w6-marketplace
 * CONSUMES: ../hooks (useMktRankings), ../model (lifecycleLevel),
 *           ./storeViewModel (rankWord), ./shared, ./marketplace-store.css
 * PROVIDES: RankingsSection
 * INVARIANTS: the server owns ranking derivation (latest stored 14-factor
 *           snapshot per seed, deduped by backend SQL); an unscored seed
 *           renders NOT_AVAILABLE and gets NO bar (never a fake 0); rank
 *           labels ("rank 1 · highest total") are derived from the array
 *           position, not from an invented metric; the full table is
 *           preserved with all its rows; the dimension Segmented and Reload
 *           path keep working.
 * EXTEND:   a new dimension is a DIMENSIONS entry that the backend already
 *           serves — never add a client-side score.
 */

import { useState } from "react";
import { DataTable, EmptyState, ErrorState, Panel, Segmented, Skeleton } from "@/components/primitives";
import { formatDateTime, formatNumber } from "@/lib/format";
import { useMktRankings } from "../hooks";
import { lifecycleLevel } from "../model";
import type { MktRankingRow } from "../types";
import { rankWord } from "./storeViewModel";
import { FreshnessNote, asErrorText } from "./shared";
import "./marketplace.css";
import "./marketplace-store.css";

const DIMENSIONS = [
  { id: "OVERALL", label: "Overall" },
  { id: "PROFITABILITY", label: "Profitability" },
  { id: "ROBUSTNESS", label: "Robustness" },
  { id: "OOS", label: "OOS" },
  { id: "LIVE_READINESS", label: "Live" },
];

/** Total of a ranking row narrowed to a number (null/undefined -> null). */
function totalOf(r: MktRankingRow): number | null {
  return typeof r.total === "number" && Number.isFinite(r.total) ? r.total : null;
}

/** 14-factor totals observed so far; only used to scale the bar track. */
const SCORE_FLOOR = 0;
const SCORE_CEIL = 1;

export function RankingsSection() {
  const [dim, setDim] = useState("OVERALL");
  const rankings = useMktRankings(dim);

  const rows = rankings.data?.rows ?? [];
  const podium = rows.slice(0, 3);
  const table = rows.slice(3);

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
        <>
          {podium.length > 0 && (
            <div className="mkt-store-podium">
              {podium.map((r, i) => {
                const total = totalOf(r);
                const na = total === null;
                const pct = na ? 0 : Math.max(0, Math.min(100, ((total - SCORE_FLOOR) / (SCORE_CEIL - SCORE_FLOOR)) * 100));
                return (
                  <div className={`mkt-store-pod p${i + 1}`} key={r.seed_id}>
                    <span className="pos">#{i + 1}</span>
                    <span className="seed">{r.seed_id}</span>
                    <span className={`total ${na ? "na" : ""}`} title={na ? "no stored total — NOT_AVAILABLE" : `total ${formatNumber(r.total, 3)}`}>
                      {na ? "NOT_AVAILABLE" : formatNumber(r.total, 3)}
                    </span>
                    {!na && (
                      <span
                        className="mkt-store-scorebar"
                        role="img"
                        aria-label={`score total ${formatNumber(r.total, 3)}, bar 0..1 (derived display)`}
                      >
                        <span className="track"><i style={{ width: `${pct}%` }} /></span>
                      </span>
                    )}
                    <span className="fam">{String(r.family || "unknown family").toUpperCase()}</span>
                    <span>
                      <span className={`badge ${lifecycleLevel(r.lifecycle)}`}>{String(r.lifecycle || "UNKNOWN")}</span>
                    </span>
                    <span className="rankword">{rankWord(i)}</span>
                  </div>
                );
              })}
            </div>
          )}
          {table.length > 0 && (
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
              {table.map((r, i) => {
                const total = totalOf(r);
                const na = total === null;
                const pct = na ? 0 : Math.max(0, Math.min(100, ((total - SCORE_FLOOR) / (SCORE_CEIL - SCORE_FLOOR)) * 100));
                return (
                  <tr key={r.seed_id}>
                    <td>
                      <span className="mkt-rank">{i + 4}</span>
                    </td>
                    <td>{r.seed_id}</td>
                    <td>{r.family || "—"}</td>
                    <td>
                      <span className={`badge ${lifecycleLevel(r.lifecycle)}`}>{String(r.lifecycle || "UNKNOWN")}</span>
                    </td>
                    <td className="num">
                      <span
                        className={`mkt-store-scorebar ${na ? "na" : ""}`}
                        role="img"
                        aria-label={na ? "no stored total — NOT_AVAILABLE" : `score total ${formatNumber(r.total, 3)}, bar 0..1 (derived display)`}
                        title={na ? "no stored total — NOT_AVAILABLE" : `total ${formatNumber(r.total, 3)} · bar 0..1 (derived)`}
                      >
                        <span className="track"><i style={{ width: `${pct}%` }} /></span>
                        <span className="mkt-store-mono">{na ? "NOT_AVAILABLE" : formatNumber(r.total, 3)}</span>
                      </span>
                    </td>
                    <td>{r.verdict ?? "—"}</td>
                    <td>{r.scored_at ? formatDateTime(r.scored_at) : "—"}</td>
                  </tr>
                );
              })}
            </DataTable>
          )}
          {podium.length > 0 && table.length === 0 && (
            <div className="tiny faint" style={{ marginTop: 8 }}>
              fewer than 4 ranked seeds — the podium above is the complete ranking
            </div>
          )}
        </>
      )}
    </Panel>
  );
}
