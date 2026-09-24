/** Display-only localization for the closed action-word set (BUY/SELL/NO_TRADE).
 *  Comparisons, queries and backend payloads must keep the raw token — only
 *  words the user reads are localized. Unknown backend values render verbatim.
 */
export type Translate = (key: string, fallback: string) => string;

export function actionLabel(t: Translate, action: string): string {
  const a = action.trim().toUpperCase();
  if (a === "BUY") return t("ux.signal.buy", "BUY");
  if (a === "SELL") return t("ux.signal.sell", "SELL");
  if (a === "NO_TRADE") return t("ux.signal.no_trade", "NO_TRADE");
  if (a === "WAIT") return t("ux.signal.wait", "WAIT");
  return action;
}
