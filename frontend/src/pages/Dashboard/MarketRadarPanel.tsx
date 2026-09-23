/**
 * MarketRadarPanel — React port of legacy renderMarketRadar (app.js ~L9995).
 *
 * Render binding ONLY: every value comes verbatim from the canonical
 * `snapshot.radar` object (LiveEngine._last_market_radar passthrough —
 * state, regime, candidate_count, best_setup{setup_type, quality,
 * factors.direction, compatible_strategies}, news_state, decision_reason,
 * updated_at). We NEVER recompute or derive trading intelligence here; a
 * radar candidate is never shown as an approved trade (SETUP_READY +
 * blocked decision_reason stays visibly distinct from an approval).
 *
 * null / non-object radar → explicit "NO RADAR DATA" waiting state with
 * dashes — never fake numbers (closes the parity gap from
 * docs/audit/wave_20260914/09_indicators_ui.md §5).
 */

import { useMemo } from "react";
import type { ReactNode } from "react";
import { Panel } from "@/components/primitives";
import { formatTime } from "@/lib/format";
import { useI18n } from "@/stores/i18nStore";
import "./market-console.css";

/** Shape of the backend radar contract (bar_handler.py + SetupDetection.to_contract). */
interface RadarSetup {
  setup_id?: string | null;
  setup_type?: string | null;
  quality?: number | null;
  factors?: Record<string, number> | null;
  compatible_strategies?: string[] | null;
}

interface RadarPayload {
  state?: string | null;
  regime?: string | null;
  candidate_count?: number | null;
  best_setup?: RadarSetup | null;
  setups?: RadarSetup[] | null;
  news_state?: string | null;
  decision_reason?: string | null;
  updated_at?: string | null;
  symbol?: string | null;
}

function asRecord(v: unknown): Record<string, unknown> | null {
  return v !== null && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}

/** Verbatim passthrough — no coercion beyond shape checks. */
function parseRadar(radar: unknown): RadarPayload | null {
  const r = asRecord(radar);
  if (!r) return null;
  return {
    state: typeof r.state === "string" ? r.state : null,
    regime: typeof r.regime === "string" ? r.regime : null,
    candidate_count: typeof r.candidate_count === "number" ? r.candidate_count : null,
    best_setup: parseSetup(r.best_setup),
    setups: Array.isArray(r.setups) ? r.setups.map(parseSetup).filter((s): s is RadarSetup => s !== null) : null,
    news_state: typeof r.news_state === "string" ? r.news_state : null,
    decision_reason: typeof r.decision_reason === "string" ? r.decision_reason : null,
    updated_at: typeof r.updated_at === "string" ? r.updated_at : null,
    symbol: typeof r.symbol === "string" ? r.symbol : null,
  };
}

function parseSetup(v: unknown): RadarSetup | null {
  const s = asRecord(v);
  if (!s) return null;
  const factors = asRecord(s.factors);
  return {
    setup_id: typeof s.setup_id === "string" ? s.setup_id : null,
    setup_type: typeof s.setup_type === "string" ? s.setup_type : null,
    quality: typeof s.quality === "number" ? s.quality : null,
    factors: factors ? (Object.fromEntries(Object.entries(factors).filter((e): e is [string, number] => typeof e[1] === "number")) as Record<string, number>) : null,
    compatible_strategies: Array.isArray(s.compatible_strategies) ? s.compatible_strategies.filter((c): c is string => typeof c === "string") : null,
  };
}

/** Direction from factors.direction: +1 = BUY, -1 = SELL (only if present). */
function directionOf(best: RadarSetup | null | undefined): "BUY" | "SELL" | null {
  const d = best?.factors?.direction;
  if (d === 1) return "BUY";
  if (d === -1) return "SELL";
  return null;
}

const dash = (v: ReactNode | null | undefined): ReactNode => (v === null || v === undefined || v === "" ? "—" : v);

export function MarketRadarPanel({ radar, nowMs }: { radar: unknown; nowMs: number }) {
  const t = useI18n((s) => s.t);
  // Parsed once per radar payload: parseRadar allocates fresh objects/arrays,
  // so re-running it on every 1s clock tick would defeat every memo below.
  // Pure function of `radar` — output identical, deps complete over that read.
  const r = useMemo(() => parseRadar(radar), [radar]);
  // legacy default: a present radar object without a state string is NO_SETUP;
  // an absent radar is the explicit waiting badge NO RADAR DATA.
  const state = r ? r.state || "NO_SETUP" : "NO_RADAR_DATA";
  const tone = !r ? "idle" : state === "SETUP_READY" ? "ready" : state === "WATCHING" ? "watch" : "none";

  // radar.updated_at is the authoritative timestamp — age is a display of it.
  let age = "—";
  if (r?.updated_at) {
    const ms = Date.parse(r.updated_at);
    age = Number.isFinite(ms) ? `${Math.max(0, Math.round((nowMs - ms) / 1000))}s` : "—";
  }

  const best = r?.best_setup ?? null;
  const dir = directionOf(best);
  const compat = best?.compatible_strategies ?? [];
  const news = r?.news_state ?? null;
  const newsTone = news === "HIGH_IMPACT" ? "bad" : news === "MEDIUM_IMPACT" ? "warn" : news === "LOW_IMPACT" || news === "CALM" ? "good" : "idle";

  // The quality reads are part of the visible payload text, so the two
  // formatted strings are derived here once per radar payload instead of
  // rebuilt per render (`nowMs` ticks every second; the payload does not).
  // Identical expressions and values, deps complete over the reads above.
  const qualityPct = useMemo(
    () => (typeof best?.quality === "number" ? `${(best.quality * 100).toFixed(1)}%` : "—"),
    [best?.quality],
  );
  const setupPct = useMemo(() => {
    if (!r?.setups) return [] as string[];
    return r.setups.map((s) => (typeof s.quality === "number" ? `${(s.quality * 100).toFixed(1)}%` : "—"));
  }, [r?.setups]);

  return (
    <Panel
      title="Market Radar"
      subtitle="backend setup intelligence — rendered verbatim, never recomputed here"
      accent
      right={
        <span className={`mc-radar__state mc-radar__state--${tone}`} role="status">
          {r ? state : "NO RADAR DATA"}
        </span>
      }
    >
      <div className={`mc-radar mc-radar--${tone}`}>
        {/* Hero decision line (legacy radar-decision) */}
        <div className="mc-radar__decision">
          <span className="mc-radar__decision-k">Decision</span>
          <span className="mc-radar__decision-v">{r ? dash(r.decision_reason) : "Awaiting radar snapshot…"}</span>
        </div>

        {r && (
          <div className="mc-radar__hero">
            <div>
              <div className="mc-radar__k">{t("dash.radar.best_setup", "Best setup")}</div>
              <div className="mc-radar__v">{dash(best?.setup_type)}</div>
            </div>
            <div>
              <div className="mc-radar__k">Direction</div>
              <div className="mc-radar__v">
                {dir ? <span className={`mc-radar__dir mc-radar__dir--${dir.toLowerCase()}`}>{dir}</span> : "—"}
              </div>
            </div>
            <div>
              <div className="mc-radar__k">Quality</div>
              <div className="mc-radar__v mc-radar__v--gold">
                {qualityPct}
              </div>
            </div>
          </div>
        )}

        <div className="mc-radar__facts">
          <div className="mc-radar__fact">
            <span className="mc-radar__k">Regime</span>
            <span className="mc-radar__fv">{r ? dash(r.regime) : "—"}</span>
          </div>
          <div className="mc-radar__fact">
            <span className="mc-radar__k">Candidates</span>
            <span className="mc-radar__fv mc-radar__fv--gold">{r ? dash(r.candidate_count) : "—"}</span>
          </div>
          <div className="mc-radar__fact">
            <span className="mc-radar__k">{t("dash.radar.news_state", "News state")}</span>
            <span className={`mc-radar__news mc-radar__news--${r ? newsTone : "idle"}`}>{r ? dash(news) : "—"}</span>
          </div>
          <div className="mc-radar__fact">
            <span className="mc-radar__k">Updated</span>
            <span className="mc-radar__fv mc-radar__fv--dim" title={r?.updated_at ?? "no radar snapshot"}>
              {r?.updated_at ? `${formatTime(r.updated_at)} · ${age}s ago` : "—"}
            </span>
          </div>
        </div>

        {/* Compatible strategies as chips — verbatim backend strings */}
        <div className="mc-radar__strategies" aria-label="compatible strategies">
          <span className="mc-radar__k">{t("dash.radar.compatible", "Compatible strategies")}</span>
          <span className="mc-radar__chips">
            {r ? (compat.length > 0 ? compat.map((c) => <span key={c} className="l4-chip accent">{c}</span>) : <span className="mc-radar__fv">—</span>) : <span className="mc-radar__fv">—</span>}
          </span>
        </div>

        {/* Ranked setups (radar.setups = backend's ranked top-5 list, verbatim) */}
        {r && r.setups && r.setups.length > 0 && (
          <div tabIndex={0} className="mc-radar__setups">
            <span className="mc-radar__k">Ranked setups (backend order)</span>
            <ul>
              {r.setups.map((s, i) => {
                const sd = directionOf(s);
                return (
                  <li key={s.setup_id ?? `${s.setup_type}-${i}`}>
                    <span className="rank">{i + 1}</span>
                    <span className="type">{dash(s.setup_type)}</span>
                    {sd && <span className={`mc-radar__dir mc-radar__dir--sm mc-radar__dir--${sd.toLowerCase()}`}>{sd}</span>}
                    <span className="q">{setupPct[i] ?? "—"}</span>
                  </li>
                );
              })}
            </ul>
          </div>
        )}
      </div>
    </Panel>
  );
}
