/**
 * websocket/realtimeSocket — BACK-COMPAT SHIM.
 *
 * The realtime client now lives at `@/core/realtime` (SSE + typed event bus,
 * state_version guard preserved). This shim keeps every existing import path
 * working (`@/websocket/realtimeSocket`) — other lanes were told not to move
 * their imports, so the name stays resolvable forever within the wave.
 */

export {
  realtimeClient,
  NseRealtimeClient,
  emptySnapshot,
} from "@/core/realtime";
