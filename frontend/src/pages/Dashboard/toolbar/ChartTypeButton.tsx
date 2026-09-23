import { useChartSettings, type ChartKind } from "../chart/chartSettings";
import { useI18n } from "@/stores/i18nStore";
import "./chartControls.css";

const KINDS: ReadonlyArray<ChartKind> = ["candles", "line", "area", "hollow"];

/** Wave-2 LANE D: presentation-kind switch as a segmented control — one
 *  rounded group, no per-button borders, only the active segment is filled.
 *  Titles keep the hints; aria-label keeps the full kind name. */
export function ChartTypeButton() {
  const { chartKind, setChartKind } = useChartSettings();
  const t = useI18n((s) => s.t);

  /* Labels/hints resolve at render so a language switch re-translates them
     (KINDS stays module-scope presentation order only). */
  const kindText: Record<ChartKind, { label: string; hint: string }> = {
    candles: {
      label: t("dash.tools.kind_candles", "Candles"),
      hint: t("dash.tools.hint_candles", "candlestick bodies with wicks"),
    },
    line: {
      label: t("dash.tools.kind_line", "Line"),
      hint: t("dash.tools.hint_line", "close-price line"),
    },
    area: {
      label: t("dash.tools.kind_area", "Area"),
      hint: t("dash.tools.hint_area", "close-price line with fill"),
    },
    hollow: {
      label: t("dash.tools.kind_hollow", "Hollow"),
      hint: t("dash.tools.hint_hollow", "hollow candle bodies"),
    },
  };

  return (
    <span className="seg" role="group" aria-label={t("dash.tools.chart_type_aria", "chart type")}>
      {KINDS.map((kind) => (
        <button
          key={kind}
          type="button"
          className={`seg__btn${chartKind === kind ? " is-active" : ""}`}
          title={kindText[kind].hint}
          aria-label={kindText[kind].label}
          aria-pressed={chartKind === kind}
          onClick={() => setChartKind(kind)}
        >
          {kindText[kind].label}
        </button>
      ))}
    </span>
  );
}
