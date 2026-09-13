/**
 * Accounting tab — legacy tab-account parity (Web/app.js accounting block).
 *
 * Sections: summary cards + period hero, equity/drawdown/growth charts (shared
 * viz kit), risk-adjusted metrics, per-kind series, performance intelligence,
 * strategy contributions, closed trades with forensic drawer + PnL waterfall,
 * and the live RiskEngine accounting panel.
 *
 * Hard rule inherited from the backend contract (BUG-020 lineage): no
 * synthetic numbers. An unavailable accounting core renders an honest error /
 * empty state in every section — never $0 rows.
 */

import { AccountChartsSection } from "./AccountChartsSection";
import { AccountSummarySection } from "./AccountSummarySection";
import { LiveAccountingSection } from "./LiveAccountingSection";
import { AdvancedMetricsSection, PeriodSeriesSection, PerformanceIntelligenceSection } from "./PerformanceSections";
import { StrategiesSection } from "./StrategiesSection";
import { TradesSection } from "./TradesSection";
import "./account.css";

export default function AccountPage() {
  return (
    <div>
      <div className="page-head">
        <h1>Accounting</h1>
        <span className="crumb">legacy tab-account</span>
        <span className="desc">
          canonical accounting core — one methodology for equity, drawdown and realized R across dashboard, reports and the pre-trade gate
        </span>
      </div>
      <AccountSummarySection />
      <AccountChartsSection />
      <div className="grid cols-2">
        <AdvancedMetricsSection />
        <PeriodSeriesSection />
      </div>
      <PerformanceIntelligenceSection />
      <StrategiesSection />
      <TradesSection />
      <LiveAccountingSection />
    </div>
  );
}
