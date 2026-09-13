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
import { useNewsAiStatus } from "../hooks";
import { ArticleAiStatusLine } from "./AiStatusLine";
import { NewsFeedSection } from "./NewsFeedSection";
import { NewsKeywordsPanel } from "./NewsKeywordsPanel";
import { NewsSourcesPanel } from "./NewsSourcesPanel";
import { NewsStatePanel } from "./NewsStatePanel";
import { NewsTimelinePanel } from "./NewsTimelinePanel";
import { NewsTradesPanel } from "./NewsTradesPanel";
import { FreshnessNote } from "./shared";
import "./news.css";

export default function NewsPage() {
  const aiStatus = useNewsAiStatus();
  return (
    <div>
      <div className="page-head">
        <h1>News Intelligence</h1>
        <span className="crumb">legacy tab-news</span>
        <span className="desc">isolated news subsystem · bounded gate — news informs, never forces a trade</span>
        <span style={{ marginLeft: "auto" }}>
          <FreshnessNote updatedAtMs={aiStatus.dataUpdatedAt ?? null} label="ai-status" />
        </span>
      </div>

      <NewsStatePanel />
      <NewsFeedSection />
      <NewsTimelinePanel />
      <NewsSourcesPanel />
      <NewsKeywordsPanel />
      <NewsTradesPanel />

      <Panel title="AI readiness (secret-free)" right={<span className="timestamp-note">GET /api/news/ai-status</span>}>
        {aiStatus.isPending ? (
          <Skeleton count={1} height={28} />
        ) : (
          <ArticleAiStatusLine status={aiStatus.data?.ai_status ?? null} />
        )}
      </Panel>
    </div>
  );
}
