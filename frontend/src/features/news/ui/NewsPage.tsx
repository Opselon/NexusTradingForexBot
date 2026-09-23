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
 *
 * Loading speed (perf lane): the five secondary panels each own their queries
 * and render below the fold, so they are inner code-splits behind LOCAL
 * Suspense boundaries (the shell's shared Suspense would blank the whole
 * route). State + feed stay eager — they are the above-the-fold content.
 */

import { Suspense, lazy } from "react";
import { ErrorState, Panel, Skeleton } from "@/components/primitives";
import { ApiError } from "@/types/api";
import { useNewsAiStatus } from "../hooks";
import { ArticleAiStatusLine } from "./AiStatusLine";
import { NewsFeedSection } from "./NewsFeedSection";
import { NewsStatePanel } from "./NewsStatePanel";
import { FreshnessNote } from "./shared";
import "./news.css";

/* ── inner splits (module-per-panel, fetched in parallel after route entry) ── */
const NewsProConsolePanel = lazy(() => import("./NewsProConsolePanel").then((m) => ({ default: m.NewsProConsolePanel })));
const NewsTimelinePanel = lazy(() => import("./NewsTimelinePanel").then((m) => ({ default: m.NewsTimelinePanel })));
const NewsSourcesPanel = lazy(() => import("./NewsSourcesPanel").then((m) => ({ default: m.NewsSourcesPanel })));
const NewsKeywordsPanel = lazy(() => import("./NewsKeywordsPanel").then((m) => ({ default: m.NewsKeywordsPanel })));
const NewsTradesPanel = lazy(() => import("./NewsTradesPanel").then((m) => ({ default: m.NewsTradesPanel })));

/** Shell-matching skeleton for a lazy panel while its chunk streams in. */
function PanelFallback({ title, count = 3, height = 32 }: { title: string; count?: number; height?: number }) {
  return (
    <Panel title={title} tight>
      <Skeleton count={count} height={height} />
    </Panel>
  );
}

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
      <Suspense fallback={<PanelFallback title="Pro auto console" count={4} height={44} />}>
        <NewsProConsolePanel />
      </Suspense>
      <Suspense fallback={<PanelFallback title="Impact timeline" count={3} height={64} />}>
        <NewsTimelinePanel />
      </Suspense>
      <Suspense
        fallback={
          <div className="grid cols-2">
            <PanelFallback title={`Source registry (…)`} count={4} height={30} />
            <PanelFallback title="Subsystem health" count={4} height={28} />
          </div>
        }
      >
        <NewsSourcesPanel />
      </Suspense>
      <Suspense fallback={<PanelFallback title="Keyword intelligence" count={4} height={30} />}>
        <NewsKeywordsPanel />
      </Suspense>
      <Suspense fallback={<PanelFallback title="News ↔ trade linkage" count={2} height={30} />}>
        <NewsTradesPanel />
      </Suspense>

      <Panel title="AI readiness (secret-free)" right={<span className="timestamp-note">GET /api/news/ai-status</span>}>
        {aiStatus.isPending ? (
          <Skeleton count={1} height={28} />
        ) : aiStatus.isError ? (
          <ErrorState
            message={aiStatus.error instanceof Error ? aiStatus.error.message : "GET /api/news/ai-status failed"}
            requestId={aiStatus.error instanceof ApiError ? aiStatus.error.requestId : null}
            onRetry={() => void aiStatus.refetch()}
          />
        ) : (
          <ArticleAiStatusLine status={aiStatus.data?.ai_status ?? null} />
        )}
      </Panel>
    </div>
  );
}
