/**
 * Impact + timeline — bucketed signed impact sums (/api/news/timeline) and the
 * recent impact rows (/api/news/impact). The asset and bucket window are
 * controls; both narrow what the backend returns (no client-side filtering of a
 * larger set, no client-side aggregation).
 */

import { useMemo, useState } from "react";
import { EmptyState, ErrorState, Panel, Segmented } from "@/components/primitives";
import { formatDateTime, formatNumber, formatPct } from "@/lib/format";
import { useI18n } from "@/stores/i18nStore";
import { useNewsImpact, useNewsTimeline } from "../hooks";
import { dirWord, timelineWindow } from "../model";
import type { NewsImpactRow } from "../types";
import { NewsImpactCanvas } from "./NewsImpactCanvas";
import { NewsSortTable, type SortColumn } from "./NewsSortTable";
import { FreshnessNote, asErrorText } from "./shared";

type TfId = "15m" | "1h" | "4h" | "1d";

const TF: Record<TfId, { bucket: number; hours: number; label: string }> = {
  "15m": { bucket: 900, hours: 48, label: "15m" },
  "1h": { bucket: 3_600, hours: 48, label: "1h" },
  "4h": { bucket: 14_400, hours: 72, label: "4h" },
  "1d": { bucket: 86_400, hours: 168, label: "1d" },
};

export function NewsTimelinePanel() {
  const t = useI18n((s) => s.t);
  const [tf, setTf] = useState<TfId>("15m");
  const [asset, setAsset] = useState("XAUUSD");
  const [assetDraft, setAssetDraft] = useState("XAUUSD");
  const bucket = TF[tf].bucket;
  const hours = TF[tf].hours;

  const timeline = useNewsTimeline(bucket, hours);
  const impact = useNewsImpact(asset, 40);

  // perf: the sortable header (localized labels + sort extractors) is built
  // once per translation identity, not on every render of the 40-row table.
  const impactColumns = useMemo<SortColumn<NewsImpactRow>[]>(
    () => [
      { key: "evaluated", label: t("news.timeline.h_evaluated", "evaluated"), sortValue: (im) => (im.evaluated_at ? Date.parse(im.evaluated_at) : null) },
      { key: "direction", label: t("news.timeline.h_direction", "direction"), sortValue: (im) => (im.direction ?? "NEUTRAL").toUpperCase() },
      { key: "strength", label: t("news.timeline.h_strength", "strength"), num: true, sortValue: (im) => im.strength ?? null },
      { key: "relevance", label: t("news.timeline.h_relevance", "relevance"), num: true, sortValue: (im) => im.relevance ?? null },
      { key: "confidence", label: t("news.timeline.h_confidence", "confidence"), num: true, sortValue: (im) => im.confidence ?? null },
      { key: "horizon", label: t("news.timeline.h_horizon", "horizon"), sortValue: (im) => im.horizon ?? null },
      { key: "article", label: t("news.timeline.h_article", "article"), sortValue: (im) => im.article_id ?? null },
    ],
    [t],
  );

  const buckets = timeline.data ?? [];
  // perf: window derivation (first/last/sum over the bucket array) once per
  // payload identity — the timeline poll refetches every 120s.
  const win = useMemo(() => timelineWindow(buckets), [buckets]);
  const assetInvalid = !/^[A-Z0-9._-]{1,16}$/.test(assetDraft.trim().toUpperCase());

  return (
    <Panel
      title={t("news.timeline.title", "Impact timeline")}
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
              aria-label={t("news.timeline.asset_label", "asset")}
              aria-invalid={assetInvalid}
              placeholder="XAUUSD"
            />
            <button className="btn small" type="submit" disabled={assetInvalid}>
              {t("news.timeline.apply", "Apply")}
            </button>
          </form>
        </>
      }
    >
      {assetInvalid && assetDraft.trim() !== "" && (
        <div className="news-field-error">
          {t("news.timeline.field_error", "asset: 1–16 chars, A–Z 0–9 . _ - only")}
        </div>
      )}
      <div className="statline" style={{ marginBottom: 8 }}>
        <span>
          {t("news.timeline.stats", "{n} buckets · {records} impact records in view", {
            n: buckets.length,
            records: win.articles,
          })}
        </span>
        <span>
          {win.from
            ? t("news.timeline.window", "{from} → {to}", {
                from: formatDateTime(win.from),
                to: win.to ? formatDateTime(win.to) : "",
              })
            : t("news.timeline.window_none", "window —")}
        </span>
        <span>
          {t("news.timeline.bucket_meta", "bucket {tf} · lookback {h}h · asset {asset}", {
            tf: TF[tf].label,
            h: hours,
            asset,
          })}
        </span>
        <FreshnessNote updatedAtMs={timeline.dataUpdatedAt ?? null} label={t("news.fresh.timeline", "timeline")} staleAfterMs={300_000} />
      </div>
      {timeline.isPending ? (
        <div className="viz-empty">{t("news.timeline.loading_buckets", "loading buckets…")}</div>
      ) : timeline.isError ? (
        <ErrorState message={asErrorText(timeline.error, t)} onRetry={() => timeline.refetch()} />
      ) : (
        <NewsImpactCanvas
          buckets={buckets}
          bucketSec={bucket}
          hoursBack={hours}
          emptyHint={t("news.timeline.empty_hint", "No {asset} impact buckets in the last {h}h — try a wider timeframe.", {
            asset,
            h: hours,
          })}
        />
      )}

      <div className="section-title" style={{ marginTop: 14 }}>
        {t("news.timeline.recent_title", "Recent impact records · {asset}", { asset })}
      </div>
      {impact.isPending ? (
        <div className="viz-empty">{t("news.timeline.loading_impacts", "loading impacts…")}</div>
      ) : impact.isError ? (
        <ErrorState message={asErrorText(impact.error, t)} onRetry={() => impact.refetch()} />
      ) : (impact.data ?? []).length === 0 ? (
        <EmptyState
          message={t("news.timeline.empty_rows", "No stored impact rows for {asset}.", { asset })}
          hint={t(
            "news.timeline.empty_rows_hint",
            "Impacts are written by the analysis pass — analyze an article or enable auto-analysis.",
          )}
        />
      ) : (
        <NewsSortTable
          columns={impactColumns}
          rows={impact.data ?? []}
          rowKey={(im, i) => `${im.id ?? i}`}
          label={t("news.timeline.recent_title", "Recent impact records · {asset}", { asset })}
          renderCells={(im) => (
            <>
              <td>{im.evaluated_at ? formatDateTime(im.evaluated_at) : "—"}</td>
              <td>
                <span className={`news-dir ${(im.direction ?? "NEUTRAL").toUpperCase()}`}>
                  {im.direction ? dirWord(t, im.direction) : "—"}
                </span>
              </td>
              <td className="num">{formatNumber(im.strength, 3)}</td>
              <td className="num">{im.relevance != null ? formatPct(im.relevance * 100, 0) : "—"}</td>
              <td className="num">{formatNumber(im.confidence, 3)}</td>
              <td>{im.horizon ?? "—"}</td>
              <td className="inline-mono">{String(im.article_id ?? "—").slice(0, 10)}</td>
            </>
          )}
        />
      )}
    </Panel>
  );
}
