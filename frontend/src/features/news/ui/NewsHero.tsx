/**
 * PURPOSE:  Page hero for the News console — kicker, glyph + gradient title,
 *           description, endpoint provenance chips and a status rail that
 *           mirrors the page's OWN queries (state, ai-status).
 * OWNER:    uiux-modern-20260926 lane 8 (news) — future edits go here.
 * CONSUMES: useNewsState() / useNewsAiStatus() query STATE only (pending /
 *           error / backend word), i18n keys news.page.*, news.hero.*,
 *           news.fresh.*; theme tokens through news-hero.css.
 * PROVIDES: NewsHero — rendered as the first child of NewsPage.
 * INVARIANTS: presentation only (no new fetch/route/dependency, no changed
 *           value or backend wording); the rail mounts only query keys that
 *           are ALREADY mounted elsewhere on this page (state by
 *           NewsStatePanel, ai-status by NewsPage), so it adds no fetch and
 *           no poller of its own; a failed source renders UNAVAILABLE and a
 *           pending source a shimmer bar — never a fabricated value; the
 *           visible title is a <div> because AppShell renders the route's
 *           single sr-only <h1> (a second one would break that invariant);
 *           class prefix `news-` (contract §3).
 * EXTEND:   a new rail cell = a query already used by a section below, plus
 *           an existing i18n key; never mount a query solely for this hero.
 */

import type { ReactNode } from "react";
import { useI18n } from "@/stores/i18nStore";
import { useNewsAiStatus, useNewsState } from "../hooks";
import { FreshnessNote } from "./shared";
import "./news-hero.css";

/** Endpoints this page reads — provenance shown as chips, verbatim (api.ts doc blocks). */
const ENDPOINTS = [
  "/api/news",
  "/api/news/state",
  "/api/news/health",
  "/api/news/timeline",
  "/api/news/keywords",
  "/api/news/ai-status",
] as const;

type Tone = "ok" | "bad" | "busy";

interface QueryState {
  isPending: boolean;
  isError: boolean;
}

/** Pending → shimmer, failed → red, settled → green (query state, not a verdict). */
function toneOf(q: QueryState): Tone {
  if (q.isError) return "bad";
  if (q.isPending) return "busy";
  return "ok";
}

/** One source cell: tone dot + label, then the raw backend word below it. */
function RailCell({ label, tone, value }: { label: string; tone: Tone; value: ReactNode }) {
  const t = useI18n((s) => s.t);
  return (
    <div className="news-rail-cell">
      <span className="news-rail-top">
        <span className={`news-rail-dot news-rail-${tone}`} aria-hidden="true" />
        <span className="news-rail-k">{label}</span>
      </span>
      <span className="news-rail-v">
        {tone === "busy" ? (
          <span className="skeleton news-rail-busy" aria-hidden="true" />
        ) : tone === "bad" ? (
          t("news.hero.unavailable", "UNAVAILABLE")
        ) : (
          value
        )}
      </span>
    </div>
  );
}

export function NewsHero() {
  const t = useI18n((s) => s.t);
  const stateQuery = useNewsState();
  const aiQuery = useNewsAiStatus();

  const stateWord = stateQuery.data?.state ?? "—";
  const aiWord = aiQuery.data?.ai_status?.state ?? "—";

  return (
    <section className="news-hero" aria-labelledby="news-hero-title">
      <span className="news-hero-mesh" aria-hidden="true" />
      <div className="news-hero-main">
        <div className="news-kicker">
          <span className="news-kicker-dot" aria-hidden="true" />
          {t("news.hero.kicker", "MARKET & RESEARCH · ISOLATED NEWS SUBSYSTEM")}
          <span className="news-kicker-rule" aria-hidden="true" />
        </div>
        <div className="news-title" id="news-hero-title">
          <span className="glyph" aria-hidden="true">
            ◉
          </span>
          <span className="word">{t("news.page.title", "News Intelligence")}</span>
        </div>
        <p className="news-desc">
          {t(
            "news.page.desc",
            "isolated news subsystem · bounded gate — news informs, never forces a trade",
          )}
        </p>
        <div
          className="news-endpoints"
          aria-label={t("news.hero.endpoints_aria", "backend endpoints this page reads")}
        >
          {ENDPOINTS.map((ep) => (
            <span
              className="news-ep"
              key={ep}
              title={t(
                "news.hero.endpoint_title",
                "Provenance — backend endpoint {ep}: every figure on this page is read from it, never inferred here",
                { ep },
              )}
            >
              <span className="news-ep-dot" aria-hidden="true" />
              {ep}
            </span>
          ))}
        </div>
      </div>

      <div
        className="news-hero-side"
        role="group"
        aria-label={t("news.hero.rail_aria", "news data sources on this page")}
      >
        <RailCell
          label={t("news.fresh.state", "state")}
          tone={toneOf(stateQuery)}
          value={stateWord}
        />
        <RailCell
          label={t("news.fresh.ai_status", "ai-status")}
          tone={toneOf(aiQuery)}
          value={aiWord}
        />
        <span className="news-hero-fresh">
          <FreshnessNote
            updatedAtMs={aiQuery.dataUpdatedAt ?? null}
            label={t("news.fresh.ai_status", "ai-status")}
          />
        </span>
      </div>
    </section>
  );
}
