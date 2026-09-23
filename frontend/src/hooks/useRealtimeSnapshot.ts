/**
 * useRealtimeSnapshot — React binding for the realtime client.
 *
 * The implementation lives in `@/hooks/useRealtime` (the single home of the
 * realtime hooks). This module keeps its own import path — AppShell and every
 * lane that imported `@/hooks/useRealtimeSnapshot` must keep resolving — and
 * delegates instead of maintaining a second copy of the subscription logic
 * (the two bodies had already drifted; one implementation = one fix site).
 *
 * Server state comes from the backend's canonical snapshot; the REST snapshot
 * query seeds initial data and the socket keeps it live. When the socket is
 * down, consumers see `connectionState !== "connected"` and must render stale
 * indicators instead of pretending the data is current.
 */

export { useRealtimeSnapshot } from "./useRealtime";
