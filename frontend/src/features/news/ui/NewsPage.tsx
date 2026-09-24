/**
 * News tab — full parity with the legacy tab-news section.
 *
 * Sections (each independently loading/errored, all backend-authoritative):
 *   state panel (engine + auto-analysis toggles, fetch, guarded self-heal),
 *   headlines feed with impact/sentiment badges + AI actions + article drawer,
 *   impact timeline + recent impact records, source registry + subsystem health,
 *   keyword intelligence, and the news↔trade linkage probe.
 *
 * Presentation only: every read goes through ../useCases via ../hooks.
 */

import { Panel, Skeleton } from "@/components/primitives";
import { useI18n } from "@/stores/i18nStore";
import { useNewsAiStatus } from "../hooks";
import { ArticleAiStatusLine } from "./AiStatusLine";
import { NewsFeedSection } from "./NewsFeedSection";
import { NewsKeywordsPanel } from "./NewsKeywordsPanel";
import { NewsProConsolePanel } from "./NewsProConsolePanel";
import { NewsSourcesPanel } from "./NewsSourcesPanel";
import { NewsStatePanel } from "./NewsStatePanel";
import { NewsTimelinePanel } from "./NewsTimelinePanel";
import { NewsTradesPanel } from "./NewsTradesPanel";
import { FreshnessNote } from "./shared";
import "./news.css";

export default function NewsPage() {
  const t = useI18n((s) => s.t);
  const aiStatus = useNewsAiStatus();
  return (
    <div>
      <div className="page-head">
        <h1>{t("news.page.title", "News Intelligence")}</h1>
        <span className="crumb">{t("news.page.crumb", "legacy tab-news")}</span>
        <span className="desc">
          {t("news.page.desc", "isolated news subsystem · bounded gate — news informs, never forces a trade")}
        </span>
        <span style={{ marginInlineStart: "auto" }}>
          <FreshnessNote updatedAtMs={aiStatus.dataUpdatedAt ?? null} label={t("news.fresh.ai_status", "ai-status")} />
        </span>
      </div>

      <NewsStatePanel />
      <NewsFeedSection />
      <NewsProConsolePanel />
      <NewsTimelinePanel />
      <NewsSourcesPanel />
      <NewsKeywordsPanel />
      <NewsTradesPanel />

      <Panel
        title={t("news.page.ai_ready", "AI readiness (secret-free)")}
        right={<span className="timestamp-note">GET /api/news/ai-status</span>}
      >
        {aiStatus.isPending ? (
          <Skeleton count={1} height={28} />
        ) : (
          <ArticleAiStatusLine status={aiStatus.data?.ai_status ?? null} />
        )}
      </Panel>
    </div>
  );
}
