/**
 * PURPOSE:  Shared hero header (kicker → glyph + gradient title → description
 *           + endpoint provenance chips | status side) for the legacy pages
 *           that had no page chrome at all.
 * OWNER:    uiux-modern-20260926 lane 3 (pages) — future edits go here.
 * CONSUMES: props only — every string and endpoint path is supplied by the
 *           calling page. No query, no payload, no store, no fetch.
 * PROVIDES: default PageHero(props) — <header class="pg-hero"> with kicker,
 *           glyph + gradient title, optional description, endpoint
 *           provenance chips and an optional status side slot.
 * INVARIANTS: presentation only (no new fetch/route/dependency, no changed
 *           value or wording); endpoint paths are provenance FACTS listed by
 *           the caller — never inferred or decorated here; class prefix `pg`
 *           (contract §3); the page's document heading stays the shell's
 *           single sr-only <h1>, so the visible title is a <div> (adding a
 *           second <h1> would break AppShell's exactly-one-h1 invariant).
 * EXTEND:   pass `side` for a status/refresh slot; never read a query here.
 */
import type { ReactNode } from "react";
import { useI18n } from "@/stores/i18nStore";
import "./pageHero.css";

export interface PageHeroProps {
  /** Uppercase micro-label above the title (already localized by the caller). */
  kicker: string;
  /** Single decorative glyph rendered before the title. */
  glyph: string;
  /** Visible page title — same text as the shell's sr-only route <h1>. */
  title: string;
  /** One or two sentences describing what this page reads (already localized). */
  description?: string;
  /** Endpoint paths this page reads — provenance shown as chips, verbatim. */
  endpoints: readonly string[];
  /** Status/refresh slot on the hero's trailing side. */
  side?: ReactNode;
}

/** Hero header — structure copied from the merged heroes (Intelligence
 *  `HeroHeader.tsx`, `TradingHero.tsx`); surface lives in `_shared/pages.css`. */
export default function PageHero({ kicker, glyph, title, description, endpoints, side }: PageHeroProps) {
  const t = useI18n((s) => s.t);
  return (
    <header className="pg-hero">
      <span className="pg-hero-mesh" aria-hidden="true" />
      <div className="pg-hero-main">
        <div className="pg-kicker">
          <span className="dot" aria-hidden="true" />
          {kicker}
          <span className="pg-kicker-rule" aria-hidden="true" />
        </div>
        <div className="pg-title">
          <span className="glyph" aria-hidden="true">{glyph}</span>
          <span className="word">{title}</span>
        </div>
        {description ? <p className="pg-desc">{description}</p> : null}
        <div
          className="pg-endpoints"
          aria-label={t("_shared.hero.endpoints_aria", "backend endpoints this page reads")}
        >
          {endpoints.map((ep) => (
            <span
              className="pg-ep"
              key={ep}
              title={t(
                "_shared.hero.endpoint_title",
                "Provenance — backend endpoint {ep}: every figure on this page is read from it, never inferred here",
                { ep },
              )}
            >
              <span className="pg-ep-dot" aria-hidden="true" />
              {ep}
            </span>
          ))}
        </div>
      </div>
      {side ? <div className="pg-hero-side">{side}</div> : null}
    </header>
  );
}
