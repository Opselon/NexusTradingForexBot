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

import { ErrorState, Panel, Skeleton } from "@/components/primitives";
import { useI18n } from "@/stores/i18nStore";
import { useNewsAiStatus } from "../hooks";
import { ArticleAiStatusLine } from "./AiStatusLine";
import { asErrorText } from "./shared";
import { NewsFeedSection } from "./NewsFeedSection";
import { NewsHero } from "./NewsHero";
import { NewsKeywordsPanel } from "./NewsKeywordsPanel";
import { NewsProConsolePanel } from "./NewsProConsolePanel";
import { NewsSourcesPanel } from "./NewsSourcesPanel";
import { NewsStatePanel } from "./NewsStatePanel";
import { NewsTimelinePanel } from "./NewsTimelinePanel";
import { NewsTradesPanel } from "./NewsTradesPanel";
import "./news.css";

export default function NewsPage() {
  const t = useI18n((s) => s.t);
  const aiStatus = useNewsAiStatus();
  return (
    <div className="news-page">
      {/* hero: kicker → glyph + gradient title → description + endpoint chips |
          status side. AppShell owns the route's single sr-only <h1>, so the
          visible title stays a <div> (NewsHero). */}
      <NewsHero />

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
        {/* §9 states: a failed ai-status fetch renders the backend's own error
            text instead of collapsing into the "not reported" wording below. */}
        {aiStatus.isPending ? (
          <Skeleton count={1} height={28} />
        ) : aiStatus.isError ? (
          <ErrorState message={asErrorText(aiStatus.error, t)} onRetry={() => aiStatus.refetch()} />
        ) : (
          <ArticleAiStatusLine status={aiStatus.data?.ai_status ?? null} />
        )}
      </Panel>
    </div>
  );
}
