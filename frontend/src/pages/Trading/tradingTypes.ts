/**
 * PURPOSE:  Local types, constants and small snapshot helpers shared by the
           Trading trade-desk components: the reconciliation row shape, the
           LIVE typed-confirmation phrase and the per-mode impact copy.
           Constants and pure helpers only — no state, no fetch.
 * OWNER:    uiux-w6-trading  (future edits belong to this lane)
 * CONSUMES: Position from types/domain, EngineSnapshot from types/domain.
 * PROVIDES: LIVE_CONFIRM_TEXT, MODE_IMPACT, ReconState, ReconRow,
            currentModeOf().
 * INVARANTS: MODE_IMPACT copy restates backend execution semantics and is the
             same text the page has always shown; LIVE_CONFIRM_TEXT is the
             phrase the existing typed-confirmation guard requires — this file
             never loosens or tightens that guard.
 * EXTEND:   New page-local row shapes belong here; keep them derivable from fields
          that already exist on the domain types (never invented).
 */

import type { EngineSnapshot, Position } from "@/types/domain";

/** Typed phrase the existing LIVE guard requires; backend validation still applies. */
export const LIVE_CONFIRM_TEXT = "LIVE";

/** Static copy explaining what each execution mode does (backend semantics). */
export const MODE_IMPACT: Record<string, string> = {
  PAPER: "Simulated fills only — no real orders reach the broker.",
  SHADOW: "Signals are computed but never dispatched as orders.",
  LIVE: "The engine will dispatch REAL orders to the connected broker account.",
};

export type ReconState = "MATCHED" | "ENGINE_ONLY" | "BROKER_ONLY";

/** One ticket reconciled across the engine ledger and the broker read. */
export interface ReconRow {
  ticket: string;
  engine: { symbol: string | null; direction: string | null; volume: number | null } | null;
  broker: Position | null;
  state: ReconState;
}

/** Uppercase mode the engine actually runs in (runtime_mode wins, as before). */
export function currentModeOf(snapshot: EngineSnapshot): string {
  return (snapshot.runtime_mode ?? snapshot.execution_mode ?? "").toUpperCase();
}
