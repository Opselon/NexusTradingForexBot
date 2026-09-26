/**
 * PURPOSE:  Page hero for the Accounting console — kicker with hairline rule,
 *           glyph + gradient title, description, endpoint provenance chips
 *           (title tooltips), and a four-cell status rail that mirrors the
 *           page's OWN queries.
 * OWNER:    uiux-modern-20260926 lane 5 (account) — future edits go here.
 * CONSUMES: useAccountPerformance / useAccountPeriod("DAY") / useAccountGrowth /
 *           useAccountStrategies (query STATE only — pending/error/data shape),
 *           i18n keys account.hero.* + the feature's existing label keys.
 * PROVIDES: AccountHero — rendered as the first child of AccountPage.
 * INVARIANTS: the rail calls only query keys that are ALREADY mounted
 *           elsewhere on this page (performance + period DAY by
 *           TelemetryHeader, growth by AccountChartsSection, strategies by
 *           StrategiesDashboard) — it adds no fetch, no poller, no interval.
 *           Every figure/word it shows is a backend field or the feature's
 *           existing translated label; a failed source renders UNAVAILABLE,
 *           a pending source renders a shimmer bar — never a fabricated value.
 * EXTEND:   a new rail cell = one query already used by a section below, plus
 *           an existing i18n key; never mount a query solely for this hero.
 */

import type { ReactNode } from "react";
import { useI18n } from "@/stores/i18nStore";
import { useAccountGrowth, useAccountPerformance, useAccountPeriod, useAccountStrategies } from "../hooks";
import { DASH } from "./shared";
import "./account-hero.css";

/** Endpoints this page reads — provenance shown in the hero (never decoration). */
const ENDPOINTS = [
  "/api/account/performance",
  "/api/account/equity-curve",
  "/api/account/growth",
  "/api/account/strategies",
  "/api/account/trades",
  "/api/live/accounting",
] as const;

type Tone = "ok" | "bad" | "busy";

interface QueryState {
  isPending: boolean;
  isError: boolean;
}

/** Pending → shimmer, failed → red, settled → green (state, not a verdict). */
function toneOf(q: QueryState): Tone {
  if (q.isError) return "bad";
  if (q.isPending) return "busy";
  return "ok";
}

/** One source cell: tone dot + label, then the raw backend figure below it. */
function RailCell({ label, tone, value }: { label: string; tone: Tone; value: ReactNode }) {
  return (
    <div className="acc-rail-cell">
      <span className="acc-rail-top">
        <span className={`acc-rail-dot acc-rail-${tone}`} aria-hidden="true" />
        <span className="acc-rail-k">{label}</span>
      </span>
      <span className="acc-rail-v">{tone === "busy" ? <span className="skeleton acc-rail-busy" aria-hidden="true" /> : value}</span>
    </div>
  );
}

export function AccountHero() {
  const t = useI18n((s) => s.t);
  const perf = useAccountPerformance();
  const day = useAccountPeriod("DAY");
  const growth = useAccountGrowth();
  const strategies = useAccountStrategies();

  const live = perf.data?.live;
  const totalTrades = day.data?.period?.total_trades;
  const growthRows = growth.data;
  const strategyRows = strategies.data?.strategies;

  // The engine word mirrors TelemetryHeader exactly: LIVE only when the
  // backend says available === true, otherwise the honest DISCONNECTED word.
  const engineValue = perf.isError ? (
    t("account.live.unavailable", "UNAVAILABLE")
  ) : live?.available === true ? (
    t("account.telemetry.live_engine", "LIVE ENGINE")
  ) : (
    t("account.telemetry.disconnected", "DISCONNECTED")
  );

  return (
    <section className="acc-hero" aria-labelledby="acc-hero-title">
      <span className="acc-hero-mesh" aria-hidden="true" />
      <div className="acc-hero-main">
        <div className="acc-kicker">
          <span className="acc-kicker-dot" aria-hidden="true" />
          {t("account.hero.kicker", "OPERATIONS · ACCOUNTING · ANALYTICS")}
          <span className="acc-kicker-rule" aria-hidden="true" />
        </div>
        <h1 className="acc-title" id="acc-hero-title">
          <span className="glyph" aria-hidden="true">
            ◈
          </span>
          <span className="word">{t("nav.feature.account", "Accounting")}</span>
        </h1>
        <p className="acc-desc">
          {t(
            "account.hero.desc",
            "Live account state, equity and drawdown, risk-adjusted metrics, strategy contributions and closed-trade forensics — every figure below is read verbatim from the accounting core. A value the backend does not send renders as an em dash, never a guess.",
          )}
        </p>
        <div className="acc-endpoints" aria-label={t("account.hero.endpoints_aria", "endpoints surfaced by this page")}>
          {ENDPOINTS.map((ep) => (
            <span
              className="acc-ep"
              key={ep}
              title={t(
                "account.hero.endpoint_title",
                "Provenance — backend endpoint {ep} (every figure on this page is read from it, never inferred here)",
                { ep },
              )}
            >
              <span className="acc-ep-dot" aria-hidden="true" />
              {ep}
            </span>
          ))}
        </div>
      </div>

      <div className="acc-hero-side" role="group" aria-label={t("account.hero.rail_aria", "accounting data sources on this page")}>
        <RailCell
          label={t("account.fresh.performance", "performance")}
          tone={toneOf(perf)}
          value={engineValue}
        />
        <RailCell
          label={t("account.fresh.period", "period")}
          tone={toneOf(day)}
          value={day.isError ? t("account.live.unavailable", "UNAVAILABLE") : t("account.summary.l_trades", "{n} trades", { n: totalTrades ?? DASH })}
        />
        <RailCell
          label={t("account.fresh.growth", "growth")}
          tone={toneOf(growth)}
          value={growth.isError ? t("account.live.unavailable", "UNAVAILABLE") : t("account.charts.snapshots", "{n} snapshots", { n: growthRows?.length ?? DASH })}
        />
        <RailCell
          label={t("account.fresh.strategies", "strategies")}
          tone={toneOf(strategies)}
          value={strategies.isError ? t("account.live.unavailable", "UNAVAILABLE") : String(strategyRows?.length ?? DASH)}
        />
      </div>
    </section>
  );
}
