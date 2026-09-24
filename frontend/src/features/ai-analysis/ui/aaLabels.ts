/** Display-only localization for closed word sets in AI Analysis.
 *  Data/logic keep the raw token (comparisons, tone maps, payloads);
 *  only words the user reads are localized. Unknown values render verbatim.
 */
import type { Translate } from "@/features/control-center/ui/tones";

export type { Translate };
export { modeLabel } from "@/features/control-center/ui/tones";

export function actionLabel(t: Translate, action: string): string {
  const a = action.trim().toUpperCase();
  if (a === "BUY") return t("ux.signal.buy", "BUY");
  if (a === "SELL") return t("ux.signal.sell", "SELL");
  if (a === "NO_TRADE") return t("ux.signal.no_trade", "NO_TRADE");
  if (a === "WAIT") return t("ux.signal.wait", "WAIT");
  return action;
}

export function verdictLabel(t: Translate, verdict: string): string {
  const v = verdict.trim().toUpperCase();
  if (v === "STRONG_BUY") return t("ai-analysis.verdict.strong_buy", "STRONG BUY");
  if (v === "BUY") return t("ai-analysis.verdict.buy", "BUY");
  if (v === "NEUTRAL") return t("ai-analysis.verdict.neutral", "NEUTRAL");
  if (v === "SELL") return t("ai-analysis.verdict.sell", "SELL");
  if (v === "STRONG_SELL") return t("ai-analysis.verdict.strong_sell", "STRONG SELL");
  return verdict;
}
