/**
 * PURPOSE:  Hero header for /alt/intelligence — kicker, gradient title,
 *           endpoint provenance chips, live news-state pill, refresh-all.
 * OWNER:    ui/w2-lane-a  (future edits to this file belong to lane A)
 * CONSUMES: NewsState payload fields only (state, freshness, stale,
 *           available), refresh callbacks computed by IntelligencePage.
 * PROVIDES: default HeroHeader — rendered at the top of IntelligencePage.
 * INVARIANTS: every hero figure is a verbatim backend field; the pill tone
 *           restates availability/staleness/state words (never inferred);
 *           refresh-all only triggers refetch of the existing queries —
 *           the backend stays authoritative for every value.
 * EXTEND:   new hero figures = new backend fields rendered verbatim; never
 *           compute a verdict here.
 */

import type { NewsState } from "@/types/domain";

/** Endpoints this page reads — provenance shown in the hero (never decoration). */
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
  /** Pill tone restates the backend's own availability/staleness/state. */
  const liveTone = !ns?.available ? "dim" : ns.stale ? "warn" : ns.state === "BREAKING" || ns.state === "HIGH_IMPACT" ? "bad" : "good";
  return (
<header className="ix-hero">
  <div className="ix-hero-main">
    <div className="ix-kicker">
      <span className="dot" aria-hidden="true" />
      NEWS · STRUCTURE · REGIME
    </div>
    <h1 className="ix-title">
      <span className="glyph" aria-hidden="true">≈</span>
      <span className="word">Intelligence</span>
    </h1>
    <p className="ix-desc">
      News flow, market structure and engine regime in one read — raw backend signals only. Stale or unavailable context is labeled as such,
      never inferred or zero-filled in the frontend.
    </p>
    <div className="ix-endpoints" aria-label="endpoints surfaced by this page">
      {ENDPOINTS.map((ep) => (
        <span className="ix-ep" key={ep}>
          {ep}
        </span>
      ))}
    </div>
  </div>
  <div className="ix-hero-side">
    <span
      className={`ix-livechip ${liveTone}`}
      title="Backend news state + freshness from /api/news/state — displayed verbatim, never inferred."
    >
      <span className="d" aria-hidden="true" />
      {ns?.available ? (ns.state ?? "—") : "NEWS UNAVAILABLE"}
      <span className="fresh">{ns?.available ? (ns.stale ? "· STALE" : `· freshness ${ns.freshness ?? "—"}`) : "· subsystem offline"}</span>
    </span>
    <button className="btn small" onClick={refreshAll} disabled={anyFetching} aria-label="Refresh every intelligence section">
      {anyFetching ? "⟳ Refreshing…" : "⟳ Refresh all"}
    </button>
  </div>
</header>
  );
}
