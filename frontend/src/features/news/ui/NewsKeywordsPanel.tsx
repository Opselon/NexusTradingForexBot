/**
 * Keywords — the deterministic keyword backbone + corpus coverage
 * (/api/news/keywords). Category + search narrow the server-side listing; the
 * coverage table always comes from the backend's own scan.
 */

import { useMemo, useState } from "react";
import { DataTable, EmptyState, ErrorState, MetricCard, Panel } from "@/components/primitives";
import { HeatBar } from "@/components/viz";
import { formatNumber } from "@/lib/format";
import { useI18n } from "@/stores/i18nStore";
import { useNewsKeywords } from "../hooks";
import { dirWord } from "../model";
import { FreshnessNote, asErrorText } from "./shared";

export function NewsKeywordsPanel() {
  const t = useI18n((s) => s.t);
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

  // perf: sorted category keys once per dataset, not on every render.
  const catKeys = useMemo(() => Object.keys(cats).sort(), [cats]);

  const submitSearch = (value: string): void => {
    setQ(value);
    // debounce by one tick — the query key only changes on the applied value
    window.setTimeout(() => setDebounced(value.trim()), 250);
  };

  return (
    <Panel
      title={t("news.keywords.title", "Keyword intelligence")}
      right={
        <>
          <select className="select" value={category} onChange={(e) => setCategory(e.target.value)} aria-label={t("news.keywords.category_label", "category")}>
            <option value="">{t("news.keywords.all_categories", "all categories")}</option>
            {catKeys.map((c) => (
              <option key={c} value={c}>
                {c} ({cats[c]})
              </option>
            ))}
          </select>
          <input
            className="input"
            style={{ width: 140 }}
            placeholder={t("news.keywords.filter_placeholder", "filter keyword…")}
            value={q}
            onChange={(e) => submitSearch(e.target.value)}
            aria-label={t("news.keywords.search_label", "keyword search")}
          />
        </>
      }
    >
      <div className="grid cols-4">
        <MetricCard label={t("news.keywords.metric_dataset", "dataset keywords")} value={data?.dataset?.total_keywords ?? "—"} tone="dim" sub={`v${data?.dataset?.version ?? "—"}`} />
        <MetricCard label={t("news.keywords.metric_scanned", "articles scanned")} value={cov?.articles_scanned ?? "—"} tone="dim" sub={t("news.keywords.metric_scanned_sub", "last 500 canonical")} />
        <MetricCard
          label={t("news.keywords.metric_mentions", "total mentions")}
          value={cov?.total_mentions ?? "—"}
          tone="dim"
          sub={t("news.keywords.metric_mentions_sub", "active keywords {n}", { n: cov?.active_keywords ?? "—" })}
        />
        <MetricCard
          label={t("news.keywords.metric_mix", "direction mix")}
          value={`${dir.BULLISH ?? 0} / ${dir.BEARISH ?? 0}`}
          tone="dim"
          sub={t("news.keywords.metric_mix_sub", "neutral {n} · bullish / bearish keywords", { n: dir.NEUTRAL ?? 0 })}
        />
      </div>

      <div className="statline" style={{ margin: "10px 0 4px" }}>
        <span>{t("news.keywords.top_coverage", "top coverage (backend scan)")}</span>
        <FreshnessNote updatedAtMs={keywords.dataUpdatedAt ?? null} label={t("news.fresh.keywords", "keywords")} staleAfterMs={120_000} />
      </div>
      {keywords.isPending ? (
        <div className="viz-empty">{t("news.keywords.loading", "loading keyword dataset…")}</div>
      ) : keywords.isError ? (
        <ErrorState message={asErrorText(keywords.error, t)} onRetry={() => keywords.refetch()} />
      ) : tops.length === 0 ? (
        <EmptyState
          message={t("news.keywords.empty", "No keyword hits in the scanned corpus.")}
          hint={t("news.keywords.empty_hint", "Fetch news to populate — the scan covers the last 500 canonical articles.")}
        />
      ) : (
        <>
          <DataTable
            headers={[
              { label: t("news.keywords.h_keyword", "keyword") },
              { label: t("news.keywords.h_category", "category") },
              { label: t("news.keywords.h_bias", "bias") },
              { label: t("news.keywords.h_hits", "hits"), num: true },
              { label: t("news.keywords.h_mentions", "mentions"), num: true },
              { label: t("news.keywords.h_share", "share"), num: true },
            ]}
          >
            {tops.map((k) => (
              <tr key={k.keyword}>
                <td>{k.keyword}</td>
                <td>{k.category ?? "—"}</td>
                <td>
                  <span className={`news-dir ${(k.direction_bias ?? "NEUTRAL").toUpperCase()}`}>{dirWord(t, k.direction_bias ?? "NEUTRAL")}</span>
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
                title: t("news.keywords.weight_title", "{category} · weight {w}", { category: k.category ?? "", w: k.weight ?? "—" }),
              }))}
              scaleCaptions={["0%", t("news.keywords.scale_share", "share 100%")]}
            />
          </div>
          {listing.length > 0 && (
            <div className="tiny faint" style={{ marginTop: 8 }}>
              {t("news.keywords.matching", "{n} dataset keywords match the current filter (top {top} shown above).", { n: listing.length, top: tops.length })}
            </div>
          )}
        </>
      )}
    </Panel>
  );
}
