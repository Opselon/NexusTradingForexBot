/**
 * Accounting tab — legacy tab-account parity (Web/app.js accounting block).
 *
 * Sections: hero telemetry ribbon, summary cards + period hero, equity /
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
import { AccountSummarySection } from "./AccountSummarySection";
import { LiveAccountingSection } from "./LiveAccountingSection";
import { AdvancedMetricsSection, PeriodSeriesSection, PerformanceIntelligenceSection } from "./PerformanceSections";
import { StrategiesDashboard } from "./StrategiesDashboard";
import { TelemetryHeader } from "./TelemetryHeader";
import { TradesSection } from "./TradesSection";
import "./account.css";

const SECTIONS: Array<{ id: string; label: string; icon: string }> = [
  { id: "acct-summary", label: "Summary", icon: "◈" },
  { id: "acct-charts", label: "Equity", icon: "∿" },
  { id: "acct-metrics", label: "Metrics", icon: "Σ" },
  { id: "acct-intel", label: "Intelligence", icon: "≈" },
  { id: "acct-strategies", label: "Strategies", icon: "▤" },
  { id: "acct-trades", label: "Trades", icon: "☰" },
  { id: "acct-live", label: "Risk Plan", icon: "⛨" },
];

export default function AccountPage() {
  return (
    <div className="acct-container">
      <TelemetryHeader />
      <nav className="acct-nav" aria-label="Accounting sections">
        {SECTIONS.map((s) => (
          <a key={s.id} className="acct-nav-item" href={`#${s.id}`}>
            <span className="ico">{s.icon}</span>
            {s.label}
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
