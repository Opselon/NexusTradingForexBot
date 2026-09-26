/**
 * PURPOSE:  Page hero for the AI Analysis console — kicker, glyph + gradient
 *           title, description, endpoint provenance chips and a status side
 *           that mirrors the page's own latest-signal query state.
 * OWNER:    uiux-modern-20260926 lane 9 (ai-analysis) — future edits go here.
 * CONSUMES: props only (timestamp/source/isFetching/error/pending passed in by
 *           AiAnalysisPage — the latest-signal query it already owns); i18n
 *           keys ai-analysis.hero.* + ai-analysis.page.*; theme tokens through
 *           aiAnalysis-hero.css.
 * PROVIDES: AaHero — rendered as the first child of AiAnalysisPage.
 * INVARIANTS: presentation only (no new fetch, route, dependency, poll
 *           interval, value or backend wording); this component mounts NO
 *           query, so it can never add a poller of its own; endpoint chips are
 *           provenance FACTS copied from features/ai-analysis/api.ts and
 *           intelligenceApi.ts/shadow70Api.ts — never inferred or decorated;
 *           the visible title is a <div> because AppShell renders the route's
 *           single sr-only <h1> (a second one breaks that invariant);
 *           class prefix `aa` (contract §3).
 * EXTEND:   a new chip = an endpoint this page already reads; style new hero
 *           parts through .aa-hero-* rules in aiAnalysis-hero.css only.
 */
import type { ReactNode } from "react";
import { useI18n } from "@/stores/i18nStore";
import { FreshnessCaption } from "../../research/ui/lane5Kit";
import "./aiAnalysis-hero.css";

/** Endpoints this page reads — provenance shown as chips, verbatim (api.ts,
 *  intelligenceApi.ts and shadow70Api.ts path literals). */
const ENDPOINTS = [
  "/api/v1/signals/latest",
  "/api/v1/signals/history",
  "/api/v1/decisions/stats",
  "/api/v1/decisions/no-trade/reasons",
  "/api/v1/decisions/{decision_id}",
  "/api/v1/indicators",
  "/api/v1/shadow/70d",
  "/api/models/shadow70/health",
  "/api/models/shadow70/disagreements",
  "/api/intelligence/behavior",
  "/api/intelligence/anomalies",
  "/api/intelligence/evolution",
  "/api/intelligence/positions/{ticket}/timeline",
] as const;

/** Query-state tone of the status dot — pending/error/settled, never a verdict. */
type Tone = "busy" | "bad" | "ok";

export interface AaHeroProps {
  /** Timestamp of the latest signal payload (null/absent → the caption's own
   *  "no timestamp returned" wording — never faked). */
  timestamp?: string | number | null;
  /** Source label already localized by the caller (ledger name). */
  source?: string;
  isFetching?: boolean;
  /** True only for a real backend error (the caller filters RESOURCE_NOT_FOUND). */
  error?: boolean;
  /** Latest-signal query is still pending. */
  pending?: boolean;
  /** Optional extra status slot (kept for future rails; rendered beside the dot). */
  side?: ReactNode;
}

/** Hero header — structure follows the merged heroes (pages/_shared/PageHero,
 *  features/news/ui/NewsHero); surface lives in aiAnalysis-hero.css. */
export default function AaHero({ timestamp, source, isFetching, error, pending, side }: AaHeroProps) {
  const t = useI18n((s) => s.t);
  const tone: Tone = error ? "bad" : pending ? "busy" : "ok";
  return (
    <header className="aa-page-hero">
      <span className="aa-hero-mesh" aria-hidden="true" />
      <div className="aa-hero-main">
        <div className="aa-hero-kicker">
          <span className="dot" aria-hidden="true" />
          {t("ai-analysis.hero.kicker", "MARKET & RESEARCH · AI ANALYSIS")}
          <span className="aa-hero-kicker-rule" aria-hidden="true" />
        </div>
        <div className="aa-hero-title">
          <span className="glyph" aria-hidden="true">
            ◈
          </span>
          <span className="word">{t("ai-analysis.page.title", "AI Analysis")}</span>
        </div>
        <p className="aa-hero-desc">
          {t(
            "ai-analysis.page.subtitle_intel",
            "model signals · decision gates · indicators · shadow 70D · intel",
          )}
        </p>
        <div
          className="aa-hero-endpoints"
          aria-label={t("ai-analysis.hero.endpoints_aria", "backend endpoints this page reads")}
        >
          {ENDPOINTS.map((ep) => (
            <span
              className="aa-hero-ep"
              key={ep}
              title={t(
                "ai-analysis.hero.endpoint_title",
                "Provenance — backend endpoint {ep}: every figure on this page is read from it, never inferred here",
                { ep },
              )}
            >
              <span className="aa-hero-ep-dot" aria-hidden="true" />
              {ep}
            </span>
          ))}
        </div>
      </div>

      <div
        className="aa-hero-side"
        role="group"
        aria-label={t("ai-analysis.hero.side_aria", "latest signal source on this page")}
      >
        <span className={`aa-hero-live aa-hero-live-${tone}`}>
          <i className="aa-hero-live-dot" aria-hidden="true" />
          <span className="aa-hero-src">{source}</span>
        </span>
        <FreshnessCaption
          timestamp={timestamp ?? null}
          source={source}
          isFetching={isFetching}
          error={error}
        />
        {side}
      </div>
    </header>
  );
}
