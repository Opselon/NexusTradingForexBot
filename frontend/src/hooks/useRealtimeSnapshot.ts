/**
 * useRealtimeSnapshot — React binding for the realtime WebSocket.
 *
 * Server state comes from the backend's canonical snapshot; the REST snapshot
 * query seeds initial data and the socket keeps it live. When the socket is
 * down, consumers see `connectionState !== "connected"` and must render stale
 * indicators instead of pretending the data is current.
 */

import { useEffect, useState } from "react";
import { realtimeClient } from "@/websocket/realtimeSocket";
import type { ConnectionState, RealtimeStatus } from "@/types/realtime";
import type { EngineSnapshot } from "@/types/domain";

export function useRealtimeSnapshot(restSnapshot: EngineSnapshot | undefined): {
  snapshot: EngineSnapshot | undefined;
  connectionState: ConnectionState;
  realtimeStatus: RealtimeStatus;
} {
  const [socketSnapshot, setSocketSnapshot] = useState<EngineSnapshot | undefined>(undefined);
  const [status, setStatus] = useState<RealtimeStatus>(realtimeClient.currentStatus());

  useEffect(() => {
    const unsubData = realtimeClient.subscribe((snap) => setSocketSnapshot(snap));
    const unsubStatus = realtimeClient.subscribeStatus(setStatus);
    realtimeClient.start();
    return () => {
      unsubData();
      unsubStatus();
      // Keep the client alive across route changes; it is app-scoped.
    };
  }, []);

  // Prefer socket data once we have any; REST snapshot fills the gap and
  // provides the baseline for merging tick deltas.
  const snapshot = socketSnapshot ?? restSnapshot;
  return { snapshot, connectionState: status.state, realtimeStatus: status };
}
