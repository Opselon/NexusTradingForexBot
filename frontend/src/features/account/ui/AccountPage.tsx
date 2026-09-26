/**
 * Accounting tab — legacy tab-account parity (Web/app.js accounting block).
 *
 * Sections: page hero (kicker + gradient title + endpoint provenance + source
 * rail), hero telemetry ribbon, summary cards + period hero, equity /
 * drawdown / growth charts (shared viz kit), risk-adjusted metrics, per-kind
 * series, performance intelligence, strategy contributions, closed trades with
 * forensic drawer + PnL waterfall, and the live RiskEngine accounting panel.
 *
 * Hard rule inherited from the backend contract (BUG-020 lineage): no
 * synthetic numbers. An unavailable accounting core renders an honest error /
 * empty state in every section — never $0 rows. The ribbon shows "—" while the
 * data is loading or unavailable.
 */

import { AccountChartsSection } from "./AccountChartsSection";
import { AccountHero } from "./AccountHero";
import { AccountSummarySection } from "./AccountSummarySection";
import { LiveAccountingSection } from "./LiveAccountingSection";
import { AdvancedMetricsSection, PeriodSeriesSection, PerformanceIntelligenceSection } from "./PerformanceSections";
import { StrategiesDashboard } from "./StrategiesDashboard";
import { TelemetryHeader } from "./TelemetryHeader";
import { TradesSection } from "./TradesSection";
import "./account.css";
import { useI18n } from "@/stores/i18nStore";

const SECTIONS: Array<{ id: string; icon: string }> = [
  { id: "acct-summary", icon: "◈" },
  { id: "acct-charts", icon: "∿" },
  { id: "acct-metrics", icon: "Σ" },
  { id: "acct-intel", icon: "≈" },
  { id: "acct-strategies", icon: "▤" },
  { id: "acct-trades", icon: "☰" },
  { id: "acct-live", icon: "⛨" },
];

type Translator = (key: string, fallback: string, vars?: Record<string, string | number>) => string;

function sectionLabel(id: string, t: Translator): string {
  switch (id) {
    case "acct-summary": return t("account.nav.summary", "Summary");
    case "acct-charts": return t("account.nav.equity", "Equity");
    case "acct-metrics": return t("account.nav.metrics", "Metrics");
    case "acct-intel": return t("account.nav.intelligence", "Intelligence");
    case "acct-strategies": return t("account.nav.strategies", "Strategies");
    case "acct-trades": return t("account.nav.trades", "Trades");
    case "acct-live": return t("account.nav.risk_plan", "Risk Plan");
    default: return id;
  }
}

export default function AccountPage() {
  const t = useI18n((s) => s.t);
  return (
    <div className="acct-container">
      <AccountHero />
      <TelemetryHeader />
      <nav className="acct-nav" aria-label={t("account.nav.aria", "Accounting sections")}>
        {SECTIONS.map((s) => (
          <a key={s.id} className="acct-nav-item" href={`#${s.id}`}>
            <span className="ico">{s.icon}</span>
            {sectionLabel(s.id, t)}
          </a>
        ))}
      </nav>
      <div id="acct-summary">
        <AccountSummarySection />
      </div>
      <div id="acct-charts">
        <AccountChartsSection />
      </div>
      <div id="acct-metrics" className="grid cols-2">
        <AdvancedMetricsSection />
        <PeriodSeriesSection />
      </div>
      <div id="acct-intel">
        <PerformanceIntelligenceSection />
      </div>
      <div id="acct-strategies">
        <StrategiesDashboard />
      </div>
      <div id="acct-trades">
        <TradesSection />
      </div>
      <div id="acct-live">
        <LiveAccountingSection />
      </div>
    </div>
  );
}
