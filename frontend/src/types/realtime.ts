/**
 * WebSocket / realtime event types.
 *
 * The backend broadcasts the canonical `get_system_state()` snapshot over
 * `/ws` and `/web` (web/server.py websocket_endpoint). A full snapshot object
 * arrives on connect; subsequent pushes wrap incremental `tick` events as
 * `{event: "tick", ...sections}` (SSE loop fan-out).
 */

import type { EngineSnapshot } from "./domain";

export type ConnectionState = "connected" | "reconnecting" | "disconnected" | "failed";

/** A `tick` push: full snapshot minus the heavyweight lists. */
export type WsTickPayload = Omit<EngineSnapshot, "bars" | "features" | "predictions"> & {
  event: "tick";
};

export type WsMessage = EngineSnapshot | WsTickPayload;

export function isTickPayload(msg: WsMessage): msg is WsTickPayload {
  return (msg as { event?: string }).event === "tick";
}

export interface RealtimeStatus {
  state: ConnectionState;
  /** Server snapshot version of the last accepted frame. */
  lastVersion: number | null;
  /** Wall-clock of the last accepted frame (client side, ms epoch). */
  lastMessageAt: number | null;
  /** How many reconnect attempts since last successful connect. */
  reconnectAttempts: number;
  /**
   * REALTIME-HARDEN (additive; existing consumers never had to read it):
   * cumulative count of `state_version`s this stream never saw — version jumps
   * plus backend error frames. 0/undefined = no loss detected. Note the
   * honest limit: the server bumps state_version on EVERY snapshot build
   * (shared across all SSE streams AND REST polls), so a jump can also mean
   * "another consumer advanced the counter", not "frames were lost" — that is
   * why gap detection drives a throttled REST resync (cheap false positive)
   * and gapCount is reported as an observation, never a verdict.
   */
  gapCount?: number;
}
