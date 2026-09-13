/**
 * viz — shared hand-rolled SVG chart kit (no dependencies, theme tokens only).
 *
 * Contract for every component in this folder:
 *  - props carry backend values; the kit never derives a verdict or fills a gap
 *  - null/absent sample == visible gap or an explicit UNKNOWN, never zero
 *  - colors come from theme.css custom properties only (RTL-safe, density-safe)
 *  - no fetching, no business logic (components/ layer rule)
 */

export { Sparkline, type SparklineProps } from "./Sparkline";
export { Gauge, defaultToneFor, type GaugeProps, type GaugeTone } from "./Gauge";
export { EquityCurveChart, type EquityCurveChartProps, type EquityPoint } from "./EquityCurveChart";
export { DrawdownChart, type DrawdownChartProps, type DrawdownPoint } from "./DrawdownChart";
export { HeatBar, type HeatBarProps, type HeatBarItem } from "./HeatBar";
export { PnlWaterfall, buildTradeWaterfall, type PnlWaterfallProps, type WaterfallStep } from "./PnlWaterfall";
export { SignedBucketChart, type SignedBucketChartProps, type SignedBucket } from "./SignedBucketChart";
export { fmtCompact } from "./geometry";
