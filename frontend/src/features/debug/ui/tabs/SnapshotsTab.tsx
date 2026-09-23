/**
 * Snapshots tab — rolling in-memory ring (64 max): capture, sortable list,
 * full-detail viewer, and A=/B= handoff into the Compare tab.
 *
 * Capture note: the canonical way to fill the ring is GET /api/debug/state —
 * the server pushes the payload into the store as a side effect.
 */

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { EmptyState, ErrorState, Panel, Skeleton } from "@/components/primitives";
import { useI18n } from "@/stores/i18nStore";
import { FreshnessCaption, JsonView, PollControl, QuerySection, usePolling } from "@/features/config/ui/kit";
import type { SnapshotList, SnapshotMeta } from "../../api";
import { debugApi } from "../../api";
import { useSnapshotDetail, useSnapshotsQuery } from "../../hooks";
import { SortTh, sortRows, useSortState, type SortApi } from "../sorting";

type SortKey = "ts" | "id";

export function SnapshotsTab({ onSendToCompare }: { onSendToCompare: (id: string, slot: "a" | "b") => void }) {
  const t = useI18n((s) => s.t);
  const poll = usePolling(30_000);
  const query = useSnapshotsQuery(poll.paused);
  const [detailId, setDetailId] = useState<string | null>(null);
  const detail = useSnapshotDetail(detailId);
  const queryClient = useQueryClient();
  const api = useSortState<SortKey>({ key: "ts", dir: "desc" });

  const capture = async () => {
    try {
      await debugApi.state();
      await queryClient.invalidateQueries({ queryKey: ["debug", "snapshots"] });
    } catch {
      /* the list poll will surface the failure state anyway */
    }
  };

  return (
    <>
      <QuerySection<SnapshotList>
        title={t("debug.snap.title", "Snapshot ring (64 max, in-memory) (/api/debug/snapshots)")}
        accent
        query={query}
        skeletonRows={4}
        emptyMessage={t("debug.snap.empty", "No snapshots stored yet — capture one (state poll also fills the ring).")}
        right={
          <>
            <FreshnessCaption fetchedAtMs={query.dataUpdatedAt || null} intervalMs={30_000} stale={poll.paused} />
            <PollControl paused={poll.paused} onToggle={poll.togglePaused} intervalMs={30_000} busy={query.isFetching} />
            <button className="btn small primary" onClick={() => void capture()}>
                {t("debug.snap.capture", "Capture now")}
              </button>
          </>
        }
      >
        {(data) => {
          const rows = sortRows(
            data.snapshots ?? [],
            (s) => (api.sort.key === "id" ? String(s.snapshot_id ?? "") : String(s.timestamp ?? "")),
            api.sort.dir,
          );
          return (
            <div className="dbg-sec">
              {!data.available && <div className="l3-note warn">{t("debug.snap.not_attached", "snapshot store not attached on this server process")}</div>}
              <div className="l3-toolbar">
                <span className="timestamp-note">
                  {t("debug.snap.stored", "{n} stored", { n: (data.snapshots ?? []).length })} · {api.sort.key === null ? t("debug.snap.backend_order", "backend order") : t("debug.snap.sorted_by", "sorted by {key} {dir}", { key: api.sort.key, dir: api.sort.dir })}
                </span>
              </div>
              <div className="l3-scroll sm dbg-table-wrap">
                <table className="data-table dbg-table">
                  <thead>
                    <tr>
                      <SortTh<SortKey> label={t("debug.snap.th_id", "snapshot id")} col="id" api={api as SortApi<SortKey>} />
                      <SortTh<SortKey> label={t("debug.snap.th_ts", "timestamp")} col="ts" api={api as SortApi<SortKey>} />
                      <th className="plain">{t("debug.snap.th_actions", "actions")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((s: SnapshotMeta) => (
                      <tr key={String(s.snapshot_id)}>
                        <td className="inline-mono">{String(s.snapshot_id ?? "—")}</td>
                        <td>{String(s.timestamp ?? "—")}</td>
                        <td>
                          <div className="dbg-row dbg-actions">
                              <button className="btn small" onClick={() => setDetailId(String(s.snapshot_id))}>
                              {t("debug.snap.detail", "Detail")}
                            </button>
                            <button className="btn small ghost" title={t("debug.snap.to_a", "send to compare slot A")} onClick={() => onSendToCompare(String(s.snapshot_id), "a")}>
                              A=
                            </button>
                            <button className="btn small ghost" title={t("debug.snap.to_b", "send to compare slot B")} onClick={() => onSendToCompare(String(s.snapshot_id), "b")}>
                              B=
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {rows.length === 0 && <EmptyState message={t("debug.snap.no_rows", "No snapshot rows to display.")} />}
              </div>
            </div>
          );
        }}
      </QuerySection>
      {detailId && (
        <Panel
          title={t("debug.snap.detail_title", "Snapshot {id}", { id: detailId })}
          right={
            <button className="btn small ghost" onClick={() => setDetailId(null)}>
              {t("common.close", "close")}
            </button>
          }
        >
          {detail.isPending ? (
            <Skeleton count={3} />
          ) : detail.isError ? (
            <ErrorState message={detail.error instanceof Error ? detail.error.message : t("debug.snap.read_failed", "snapshot read failed")} onRetry={() => void detail.refetch()} />
          ) : (
            <div tabIndex={0} className="l3-scroll dbg-json">
              <JsonView value={detail.data} name={detailId} />
            </div>
          )}
        </Panel>
      )}
    </>
  );
}
