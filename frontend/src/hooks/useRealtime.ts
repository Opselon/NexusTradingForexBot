/**
 * useRealtime — convenience hook over the core realtime client + event bus.
 *
 * Lanes 2-5: reach for these instead of importing @/core/realtime directly.
 *  - useRealtimeSnapshot(rest)  -> merged snapshot + connection state (shell)
 *  - useRealtimeStatus()        -> live connection RealtimeStatus
 *  - useRealtimeEvent(topic, handler) -> any typed core event (bus)
 *  - useRealtimeVersion()       -> current accepted state_version + freshness
 *
 * All subscriptions are ref-stable: pass an inline arrow, the effect will not
 * re-run. The client is app-scoped — start() is idempotent across mounts.
 */

import { useEffect, useRef, useState } from "react";
import { realtimeClient } from "@/core/realtime";
import { coreEvents, type CoreEventMap, type CoreTopic } from "@/core/events";
import type { ConnectionState, RealtimeStatus } from "@/types/realtime";
import type { EngineSnapshot } from "@/types/domain";

/**
 * Seed-and-stream snapshot: the REST query result bootstraps, the SSE feed
 * keeps it live. When the feed is down, `connectionState` reflects it so the
 * consumer MUST render stale indicators instead of pretending freshness.
 */
export function useRealtimeSnapshot(restSnapshot: EngineSnapshot | undefined): {
  snapshot: EngineSnapshot | undefined;
  connectionState: ConnectionState;
  realtimeStatus: RealtimeStatus;
} {
  const [socketSnapshot, setSocketSnapshot] = useState<EngineSnapshot | undefined>(undefined);
  const [status, setStatus] = useState<RealtimeStatus>(realtimeClient.currentStatus());

  useEffect(() => {
    const unsubData = realtimeClient.subscribe(setSocketSnapshot);
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

/** Connection-only subscription (banner chips, freshness meters). */
export function useRealtimeStatus(): RealtimeStatus {
  const [status, setStatus] = useState<RealtimeStatus>(() => realtimeClient.currentStatus());
  useEffect(() => {
    const unsub = realtimeClient.subscribeStatus(setStatus);
    realtimeClient.start();
    return unsub;
  }, []);
  return status;
}

/** Subscribe to any typed core event without re-binding on every render. */
export function useCoreEvent<T extends CoreTopic>(topic: T, handler: (payload: CoreEventMap[T]) => void): void {
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => {
    const sub = coreEvents.subscribe(topic, (payload) => ref.current(payload as CoreEventMap[T]));
    return () => sub.unsubscribe();
  }, [topic]);
}

/** State version + last-frame age for compact displays (topbar chip). */
export function useRealtimeVersion(): { version: number | null; ageMs: number | null } {
  const [state, setState] = useState<{ version: number | null; ageMs: number | null }>(() => ({
    version: realtimeClient.currentStatus().lastVersion,
    ageMs: null,
  }));
  useEffect(() => {
    let alive = true;
    const tick = () => {
      if (!alive) return;
      const s = realtimeClient.currentStatus();
      setState({ version: s.lastVersion, ageMs: s.lastMessageAt !== null ? Date.now() - s.lastMessageAt : null });
    };
    tick();
    const t = window.setInterval(tick, 1000);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, []);
  return state;
}
