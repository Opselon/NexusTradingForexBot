/**
 * Rankings — GET /api/v1/marketplace/rankings?dimension=…
 *
 * The dimension selector re-queries the backend (the server owns ranking
 * derivation from the latest 14-factor snapshots); an unscored seed renders
 * NOT_AVAILABLE, never 0. Dimension labels are display labels (t()d inside
 * the render); dimension ids sent to the backend stay verbatim.
 */

import { useState } from "react";
import { useI18n } from "@/stores/i18nStore";
import { DataTable, EmptyState, ErrorState, Panel, Segmented, Skeleton } from "@/components/primitives";
import { formatDateTime, formatNumber } from "@/lib/format";
import { useMktRankings } from "../hooks";
import { lifecycleLevel } from "../model";
import { FreshnessNote, asErrorText } from "./shared";

const DIMENSION_IDS = ["OVERALL", "PROFITABILITY", "ROBUSTNESS", "OOS", "LIVE_READINESS"] as const;

export function RankingsSection() {
  const t = useI18n((s) => s.t);
  const [dim, setDim] = useState("OVERALL");
  const rankings = useMktRankings(dim);

  const rows = rankings.data?.rows ?? [];

  /** Static ids, labels rebuilt per render so they follow the language. */
  const dimensions = [
    { id: "OVERALL", label: t("marketplace.dim.overall", "Overall") },
    { id: "PROFITABILITY", label: t("marketplace.dim.profitability", "Profitability") },
    { id: "ROBUSTNESS", label: t("marketplace.dim.robustness", "Robustness") },
    { id: "OOS", label: t("marketplace.dim.oos", "OOS") },
    { id: "LIVE_READINESS", label: t("marketplace.dim.live", "Live") },
  ] satisfies Array<{ id: (typeof DIMENSION_IDS)[number]; label: string }>;

  const profileLabel = rankings.data?.profileId ?? t("marketplace.val.default", "default");

  return (
    <Panel
      title={`${t("marketplace.nav.rankings", "Rankings")} · ${rankings.data?.dimension ?? dim.toUpperCase()}`}
      right={
        <>
          <Segmented options={dimensions} value={dim} onChange={setDim} />
          <FreshnessNote updatedAtMs={rankings.dataUpdatedAt ?? null} label={t("marketplace.fresh.rankings", "rankings")} />
        </>
      }
    >
      <div className="tiny faint" style={{ marginBottom: 8 }}>
        {t(
          "marketplace.rankings.note",
          "profile {p} · ranking derives from the latest stored 14-factor snapshot per seed (backend SQL, deduped)",
          { p: profileLabel },
        )}
      </div>
      {rankings.isPending ? (
        <Skeleton count={5} height={20} />
      ) : rankings.isError ? (
        <ErrorState message={asErrorText(rankings.error, t)} onRetry={() => rankings.refetch()} />
      ) : rows.length === 0 ? (
        <EmptyState
          message={t("marketplace.rankings.empty", "No rankings computed yet.")}
          hint={t("marketplace.rankings.empty_hint", "Install seeds, then queue research runs — score snapshots populate this table.")}
        />
      ) : (
        <DataTable
          headers={[
            { label: "#" },
            { label: t("marketplace.th.seed", "seed") },
            { label: t("marketplace.th.family", "family") },
            { label: t("marketplace.th.lifecycle", "lifecycle") },
            { label: t("marketplace.th.total", "total"), num: true },
            { label: t("marketplace.th.verdict", "verdict") },
            { label: t("marketplace.th.scored_at", "scored at") },
          ]}
        >
          {rows.map((r, i) => {
            const na = r.total === null || r.total === undefined;
            return (
              <tr key={r.seed_id}>
                <td>
                  <span className={`mkt-rank ${i < 3 ? `r${i + 1}` : ""}`} dir="ltr">
                    {i + 1}
                  </span>
                </td>
                <td>{r.seed_id}</td>
                <td>{r.family || "—"}</td>
                <td>
                  <span className={`badge ${lifecycleLevel(r.lifecycle)}`}>{String(r.lifecycle || t("marketplace.status.unknown", "UNKNOWN"))}</span>
                </td>
                <td className="num mkt-score-cell" dir="ltr" style={na ? { color: "var(--text-faint)" } : undefined}>
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
