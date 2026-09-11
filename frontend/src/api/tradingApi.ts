/**
 * tradingApi — trading commands the backend actually supports.
 *
 * Every action here routes through the backend's authoritative paths:
 *  - /api/positions/close and /api/positions/modify go through the
 *    OrderLifecycleManager (BUG-242 INV-004: adapter never called directly
 *    from the web layer).
 *  - /api/engine/toggle + /api/engine/mode are the UI source of control for
 *    engine loop + execution mode (BUG-148 hot-swap path).
 * There is NO manual order placement or pending-order cancel endpoint on the
 * web layer — the UI deliberately does not expose one (never fake actions).
 */

import { send } from "./client";
import type { LegacyMutationResult } from "@/types/api";

export interface ModifyPositionPayload {
  ticket: number;
  stop_loss: number;
  take_profit: number;
}

export const tradingApi = {
  closePosition: (ticket: number): Promise<LegacyMutationResult> =>
    send<LegacyMutationResult>("/api/positions/close", { ticket }),

  modifyPosition: (payload: ModifyPositionPayload): Promise<LegacyMutationResult> =>
    send<LegacyMutationResult>("/api/positions/modify", payload),
};

/** Actions the backend genuinely supports (drives button rendering). */
export const SUPPORTED_ACTIONS = ["close_position", "modify_position", "toggle_engine", "set_mode"] as const;
export type SupportedAction = (typeof SUPPORTED_ACTIONS)[number];
