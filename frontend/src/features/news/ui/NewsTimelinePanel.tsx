/**
 * Impact + timeline — bucketed signed impact sums (/api/news/timeline) and the
 * recent impact rows (/api/news/impact). The asset and bucket window are
 * controls; both narrow what the backend returns (no client-side filtering of a
 * larger set, no client-side aggregation).
 */

import { useMemo, useState } from "react";
import { DataTable, EmptyState, ErrorState, Panel, Segmented } from "@/components/primitives";
import { formatDateTime, formatNumber, formatPct } from "@/lib/format";
import { useNewsImpact, useNewsTimeline } from "../hooks";
import { timelineWindow } from "../model";
import { NewsImpactCanvas } from "./NewsImpactCanvas";
import { FreshnessNote, asErrorText } from "./shared";

type TfId = "15m" | "1h" | "4h" | "1d";

const TF: Record<TfId, { bucket: number; hours: number; label: string }> = {
  "15m": { bucket: 900, hours: 48, label: "15m" },
  "1h": { bucket: 3_600, hours: 48, label: "1h" },
  "4h": { bucket: 14_400, hours: 72, label: "4h" },
  "1d": { bucket: 86_400, hours: 168, label: "1d" },
};

export function NewsTimelinePanel() {
  const [tf, setTf] = useState<TfId>("15m");
  const [asset, setAsset] = useState("XAUUSD");
  const [assetDraft, setAssetDraft] = useState("XAUUSD");
  const bucket = TF[tf].bucket;
  const hours = TF[tf].hours;

  const timeline = useNewsTimeline(bucket, hours);
  const impact = useNewsImpact(asset, 40);

  const buckets = timeline.data ?? [];
  // perf: window derivation (first/last/sum over the bucket array) once per
  // payload identity — the timeline poll refetches every 120s.
  const win = useMemo(() => timelineWindow(buckets), [buckets]);
  const assetInvalid = !/^[A-Z0-9._-]{1,16}$/.test(assetDraft.trim().toUpperCase());

  return (
    <Panel
      title="Impact timeline"
      right={
        <>
          <Segmented options={(Object.keys(TF) as TfId[]).map((k) => ({ id: k, label: TF[k].label }))} value={tf} onChange={setTf} />
          <form
            className="news-inline-input"
            onSubmit={(e) => {
              e.preventDefault();
              if (!assetInvalid) setAsset(assetDraft.trim().toUpperCase());
            }}
          >
            <input
              className={`input ${assetInvalid ? "invalid" : ""}`}
              style={{ width: 96 }}
              value={assetDraft}
              onChange={(e) => setAssetDraft(e.target.value)}
              aria-label="asset"
              aria-invalid={assetInvalid}
              placeholder="XAUUSD"
            />
            <button className="btn small" type="submit" disabled={assetInvalid}>
              Apply
            </button>
          </form>
        </>
      }
    >
      {assetInvalid && assetDraft.trim() !== "" && <div className="news-field-error">asset: 1–16 chars, A–Z 0–9 . _ - only</div>}
      <div className="statline" style={{ marginBottom: 8 }}>
        <span>
          {buckets.length} buckets · {win.articles} impact records in view
        </span>
        <span>{win.from ? `${formatDateTime(win.from)} → ${win.to ? formatDateTime(win.to) : ""}` : "window —"}</span>
        <span>bucket {TF[tf].label} · lookback {hours}h · asset {asset}</span>
        <FreshnessNote updatedAtMs={timeline.dataUpdatedAt ?? null} label="timeline" staleAfterMs={300_000} />
      </div>
      {timeline.isPending ? (
        <div className="viz-empty">loading buckets…</div>
      ) : timeline.isError ? (
        <ErrorState message={asErrorText(timeline.error)} onRetry={() => timeline.refetch()} />
      ) : (
        <NewsImpactCanvas buckets={buckets} bucketSec={bucket} hoursBack={hours} emptyHint={`No ${asset} impact buckets in the last ${hours}h — try a wider timeframe.`} />
      )}

      <div className="section-title" style={{ marginTop: 14 }}>
        Recent impact records · {asset}
      </div>
      {impact.isPending ? (
        <div className="viz-empty">loading impacts…</div>
      ) : impact.isError ? (
        <ErrorState message={asErrorText(impact.error)} onRetry={() => impact.refetch()} />
      ) : (impact.data ?? []).length === 0 ? (
        <EmptyState message={`No stored impact rows for ${asset}.`} hint="Impacts are written by the analysis pass — analyze an article or enable auto-analysis." />
      ) : (
        <DataTable
          headers={[
            { label: "evaluated" },
            { label: "direction" },
            { label: "strength", num: true },
            { label: "relevance", num: true },
            { label: "confidence", num: true },
            { label: "horizon" },
            { label: "article" },
          ]}
        >
          {(impact.data ?? []).map((im, i) => (
            <tr key={`${im.id ?? i}`}>
              <td>{im.evaluated_at ? formatDateTime(im.evaluated_at) : "—"}</td>
              <td>
                <span className={`news-dir ${(im.direction ?? "NEUTRAL").toUpperCase()}`}>{im.direction ?? "—"}</span>
              </td>
              <td className="num">{formatNumber(im.strength, 3)}</td>
              <td className="num">{im.relevance != null ? formatPct(im.relevance * 100, 0) : "—"}</td>
              <td className="num">{formatNumber(im.confidence, 3)}</td>
              <td>{im.horizon ?? "—"}</td>
              <td className="inline-mono">{String(im.article_id ?? "—").slice(0, 10)}</td>
            </tr>
          ))}
        </DataTable>
      )}
    </Panel>
  );
}
