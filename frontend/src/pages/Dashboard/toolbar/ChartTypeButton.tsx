import { useChartSettings, type ChartKind } from "../chart/chartSettings";

const KINDS: ReadonlyArray<readonly [ChartKind, string, string]> = [
  ["candles", "Candles", "candlestick bodies with wicks"],
  ["line", "Line", "close-price line"],
  ["area", "Area", "close-price line with fill"],
  ["hollow", "Hollow", "hollow candle bodies"],
];

/** Wave-2 LANE D slot: presentation-kind switch (scaffold wires the state;
 *  lane D restyles into a segmented control and refines labels). */
export function ChartTypeButton() {
  const { chartKind, setChartKind } = useChartSettings();
  return (
    <span className="mc-kindrow" role="group" aria-label="chart type">
      {KINDS.map(([kind, label, hint]) => (
        <button
          key={kind}
          className={`btn small ${chartKind === kind ? "primary" : "ghost"}`}
          title={hint}
          aria-pressed={chartKind === kind}
          onClick={() => setChartKind(kind)}
        >
          {label}
        </button>
      ))}
    </span>
  );
}
