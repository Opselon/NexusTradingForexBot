/** Shared trading-page constants. Orchestrator-owned; CommandDeck (lane B)
 * imports these — never redefine them. */

/** The phrase the user must type to enable the LIVE-mode apply button. */
export const LIVE_CONFIRM_TEXT = "LIVE";

/** What each execution mode means — verbatim backend/ops wording. */
export const MODE_IMPACT: Record<string, string> = {
  PAPER: "Simulated fills only — no real orders reach the broker.",
  SHADOW: "Signals are computed but never dispatched as orders.",
  LIVE: "The engine will dispatch REAL orders to the connected broker account.",
};
