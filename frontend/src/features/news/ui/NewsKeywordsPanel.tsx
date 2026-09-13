/**
 * Keywords — the deterministic keyword backbone + corpus coverage
 * (/api/news/keywords). Category + search narrow the server-side listing; the
 * coverage table always comes from the backend's own scan.
 */

import { useState } from "react";
import { DataTable, EmptyState, ErrorState, MetricCard, Panel } from "@/components/primitives";
import { HeatBar } from "@/components/viz";
import { formatNumber } from "@/lib/format";
import { useNewsKeywords } from "../hooks";
import { FreshnessNote, asErrorText } from "./shared";

export function NewsKeywordsPanel() {
  const [category, setCategory] = useState("");
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const keywords = useNewsKeywords({ category, q: debounced, topN: 25 });

  const data = keywords.data;
  const cov = data?.coverage;
  const tops = cov?.top_keywords ?? [];
  const listing = data?.keywords ?? [];
  const cats = data?.dataset?.categories ?? {};
  const dir = cov?.direction_distribution ?? {};

  const submitSearch = (value: string): void => {
    setQ(value);
    // debounce by one tick — the query key only changes on the applied value
    window.setTimeout(() => setDebounced(value.trim()), 250);
  };

  return (
    <Panel
      title="Keyword intelligence"
      right={
        <>
          <select className="select" value={category} onChange={(e) => setCategory(e.target.value)} aria-label="category">
            <option value="">all categories</option>
            {Object.keys(cats)
              .sort()
              .map((c) => (
                <option key={c} value={c}>
                  {c} ({cats[c]})
                </option>
              ))}
          </select>
          <input
            className="input"
            style={{ width: 140 }}
            placeholder="filter keyword…"
            value={q}
            onChange={(e) => submitSearch(e.target.value)}
            aria-label="keyword search"
          />
        </>
      }
    >
      <div className="grid cols-4">
        <MetricCard label="dataset keywords" value={data?.dataset?.total_keywords ?? "—"} tone="dim" sub={`v${data?.dataset?.version ?? "—"}`} />
        <MetricCard label="articles scanned" value={cov?.articles_scanned ?? "—"} tone="dim" sub="last 500 canonical" />
        <MetricCard label="total mentions" value={cov?.total_mentions ?? "—"} tone="dim" sub={`active keywords ${cov?.active_keywords ?? "—"}`} />
        <MetricCard
          label="direction mix"
          value={`${dir.BULLISH ?? 0} / ${dir.BEARISH ?? 0}`}
          tone="dim"
          sub={`neutral ${dir.NEUTRAL ?? 0} · bullish / bearish keywords`}
        />
      </div>

      <div className="statline" style={{ margin: "10px 0 4px" }}>
        <span>top coverage (backend scan)</span>
        <FreshnessNote updatedAtMs={keywords.dataUpdatedAt ?? null} label="keywords" staleAfterMs={120_000} />
      </div>
      {keywords.isPending ? (
        <div className="viz-empty">loading keyword dataset…</div>
      ) : keywords.isError ? (
        <ErrorState message={asErrorText(keywords.error)} onRetry={() => keywords.refetch()} />
      ) : tops.length === 0 ? (
        <EmptyState message="No keyword hits in the scanned corpus." hint="Fetch news to populate — the scan covers the last 500 canonical articles." />
      ) : (
        <>
          <DataTable
            headers={[
              { label: "keyword" },
              { label: "category" },
              { label: "bias" },
              { label: "hits", num: true },
              { label: "mentions", num: true },
              { label: "share", num: true },
            ]}
          >
            {tops.map((k) => (
              <tr key={k.keyword}>
                <td>{k.keyword}</td>
                <td>{k.category ?? "—"}</td>
                <td>
                  <span className={`news-dir ${(k.direction_bias ?? "NEUTRAL").toUpperCase()}`}>{k.direction_bias ?? "NEUTRAL"}</span>
                </td>
                <td className="num">{k.article_hits ?? 0}</td>
                <td className="num">{k.mention_count ?? 0}</td>
                <td className="num">{formatNumber((k.share ?? 0) * 100, 1)}%</td>
              </tr>
            ))}
          </DataTable>
          <div style={{ marginTop: 10 }}>
            <HeatBar
              items={tops.slice(0, 8).map((k) => ({
                label: k.keyword,
                value: k.share ?? null,
                caption: `${formatNumber((k.share ?? 0) * 100, 1)}%`,
                tone: k.direction_bias === "BULLISH" ? "pos" : k.direction_bias === "BEARISH" ? "bad" : "neu",
                title: `${k.category ?? ""} · weight ${k.weight ?? "—"}`,
              }))}
              scaleCaptions={["0%", "share 100%"]}
            />
          </div>
          {listing.length > 0 && (
            <div className="tiny faint" style={{ marginTop: 8 }}>
              {listing.length} dataset keywords match the current filter (top {tops.length} shown above).
            </div>
          )}
        </>
      )}
    </Panel>
  );
}
