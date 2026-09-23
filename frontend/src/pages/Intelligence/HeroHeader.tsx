/**
 * PURPOSE:  Hero header for /alt/intelligence — kicker with hairline rule,
 *           gradient title, endpoint provenance chips (title tooltips),
 *           refined live news-state pill with a gentle availability pulse,
 *           and a modern refresh-all button with a loading spinner state.
 * OWNER:    ui/w2-lane-a  (future edits to this file belong to lane A)
 * CONSUMES: NewsState payload fields only (state, freshness, stale,
 *           available), refresh callbacks computed by IntelligencePage.
 * PROVIDES: default HeroHeader — rendered at the top of IntelligencePage.
 * INVARIANTS: every hero figure is a verbatim backend field; the pill tone
 *           only restates availability/staleness/state words (never
 *           inferred); the dot pulses only while the backend reports
 *           available; refresh-all only triggers refetch of the existing
 *           queries — the backend stays authoritative for every value.
 * EXTEND:   new hero figures = new backend fields rendered verbatim; never
 *           compute a verdict here.
 */

import type { NewsState } from "@/types/domain";
import { useI18n } from "@/stores/i18nStore";

/** Endpoints this page reads — provenance shown in the hero (never decoration). */
const ENDPOINTS = [
  "/api/news/state",
  "/api/news/health",
  "/api/news/latest",
  "/api/intelligence/summary",
  "/api/intelligence/autopsies",
  "/api/liquidity/state",
  "/api/mslie/status",
  "/api/v1/market/regime",
] as const;

interface HeroHeaderProps {
  ns: NewsState | undefined;
  anyFetching: boolean;
  refreshAll: () => void;
}

export default function HeroHeader({ ns, anyFetching, refreshAll }: HeroHeaderProps) {
  const t = useI18n((s) => s.t);
  /** Pill tone restates the backend's own availability/staleness/state words. */
  const liveTone = !ns?.available ? "dim" : ns.stale ? "warn" : ns.state === "BREAKING" || ns.state === "HIGH_IMPACT" ? "bad" : "good";
  return (
<header className="ix-hero">
  <span className="ix-hero-mesh" aria-hidden="true" />
  <div className="ix-hero-main">
    <div className="ix-kicker">
      <span className="dot" aria-hidden="true" />
      {t("intelligence.hero.kicker", "NEWS · STRUCTURE · REGIME")}
      <span className="ix-kicker-rule" aria-hidden="true" />
    </div>
    <h1 className="ix-title">
      <span className="glyph" aria-hidden="true">≈</span>
      <span className="word">{t("nav.page.intelligence", "Intelligence")}</span>
    </h1>
    <p className="ix-desc">
      {t("intelligence.hero.desc", "News flow, market structure and engine regime in one read — raw backend signals only. Stale or unavailable context is labeled as such, never inferred or zero-filled in the frontend.")}
    </p>
    <div className="ix-endpoints" aria-label={t("intelligence.hero.endpoints_aria", "endpoints surfaced by this page")}>
      {ENDPOINTS.map((ep) => (
        <span
          className="ix-ep"
          key={ep}
          title={t("intelligence.hero.endpoint_title", "Provenance — backend endpoint {ep} (every figure on this page is read from it, never inferred here)", { ep })}
        >
          <span className="ix-ep-dot" aria-hidden="true" />
          {ep}
        </span>
      ))}
    </div>
  </div>
  <div className="ix-hero-side">
    <span
      className={`ix-livechip ${liveTone}`}
      title={t("intelligence.hero.chip_title", "Backend news state + freshness from /api/news/state — displayed verbatim, never inferred.")}
    >
      <span className="d" aria-hidden="true" />
      <span className="state">{ns?.available ? (ns.state ?? "—") : t("intelligence.hero.unavailable", "NEWS UNAVAILABLE")}</span>
      <span className="fresh">
        {ns?.available
          ? ns.stale
            ? `· ${t("intelligence.kpi.stale_flag", "STALE")}`
            : `· ${t("intelligence.kpi.freshness", "freshness {v}", { v: ns.freshness ?? "—" })}`
          : t("intelligence.hero.offline", "· subsystem offline")}
      </span>
    </span>
    <button
      type="button"
      className="ix-hero-refresh"
      onClick={refreshAll}
      disabled={anyFetching}
      aria-label={t("intelligence.hero.refresh_aria", "Refresh every intelligence section")}
      title={t("intelligence.hero.refresh_title", "Refetch every intelligence query — the backend stays authoritative for every value.")}
    >
      {anyFetching ? (
        <span className="ix-hero-spin" aria-hidden="true" />
      ) : (
        <span className="ix-hero-refresh-ico" aria-hidden="true">⟳</span>
      )}
      <span>{anyFetching ? t("intelligence.hero.refreshing", "Refreshing…") : t("intelligence.hero.refresh_all", "Refresh all")}</span>
    </button>
  </div>
</header>
  );
}
