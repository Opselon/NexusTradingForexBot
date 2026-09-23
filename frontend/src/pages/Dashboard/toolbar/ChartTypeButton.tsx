import { useChartSettings, type ChartKind } from "../chart/chartSettings";
import "./chartControls.css";

const KINDS: ReadonlyArray<readonly [ChartKind, string, string]> = [
  ["candles", "Candles", "candlestick bodies with wicks"],
  ["line", "Line", "close-price line"],
  ["area", "Area", "close-price line with fill"],
  ["hollow", "Hollow", "hollow candle bodies"],
];

/** Wave-2 LANE D: presentation-kind switch as a segmented control — one
 *  rounded group, no per-button borders, only the active segment is filled.
 *  Titles keep the hints; aria-label keeps the full kind name. */
export function ChartTypeButton() {
  const { chartKind, setChartKind } = useChartSettings();
  return (
    <span className="seg" role="group" aria-label="chart type">
      {KINDS.map(([kind, label, hint]) => (
        <button
          key={kind}
          type="button"
          className={`seg__btn${chartKind === kind ? " is-active" : ""}`}
          title={hint}
          aria-label={label}
          aria-pressed={chartKind === kind}
          onClick={() => setChartKind(kind)}
        >
          {label}
        </button>
      ))}
    </span>
  );
}
