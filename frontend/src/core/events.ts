/**
 * core/events — the typed application event bus.
 *
 * WHY: layers must not import each other sideways (dependency rule). The
 * realtime client, the transport middleware, auth and the UI all need to
 * talk to each other; an in-process typed pub/sub is the seam. Subscribe with
 * a topic key and the payload type is inferred from CoreEventMap — a new
 * producer/consumer pair adds one interface line (Open/Closed).
 *
 * Guarantees:
 *  - publish() is synchronous and listener-errors are isolated (one throwing
 *    subscriber can never break the feed or the others).
 *  - subscribe() returns an unsubscribe function safe to call twice.
 *  - lastValue() keeps the newest payload per topic so a late subscriber
 *    (a feature page mounting after boot) can hydrate without re-fetching.
 */

import type { ConnectionState, RealtimeStatus } from "@/types/realtime";
import type { EngineSnapshot } from "@/types/domain";

/** Incremental SSE `tick` frame: the section names actually present. */
export interface TickSections {
  sections: string[];
  state_version: number | null;
}

/** Everything published on the bus, keyed by topic. */
export interface CoreEventMap {
  // --- realtime (core/realtime) ---
  "realtime:snapshot": EngineSnapshot;
  "realtime:tick": TickSections;
  "realtime:status": RealtimeStatus;
  "realtime:connection": ConnectionState;
  "realtime:version-jump": { from: number; to: number };
  // --- auth (core/auth) ---
  "auth:changed": { hasToken: boolean; source: "login" | "logout" | "cookie-bootstrap" | "token-consumed" };
  "auth:expired": { at: number };
  // --- transport (core/middleware) ---
  "transport:error": { status: number; code: string; message: string; requestId: string | null; path: string };
  "transport:healed": { path: string };
  // --- shell lifecycle ---
  "shell:logout-requested": Record<string, never>;
}

export type CoreTopic = keyof CoreEventMap;
type Handler<T> = (payload: T) => void;

export interface Subscription {
  unsubscribe(): void;
}

export class EventBus {
  private handlers = new Map<CoreTopic, Set<Handler<never>>>();
  private latest = new Map<CoreTopic, unknown>();

  subscribe<T extends CoreTopic>(topic: T, handler: Handler<CoreEventMap[T]>): Subscription {
    let set = this.handlers.get(topic);
    if (!set) {
      set = new Set();
      this.handlers.set(topic, set);
    }
    set.add(handler as Handler<never>);
    let active = true;
    return {
      unsubscribe: () => {
        if (!active) return;
        active = false;
        this.handlers.get(topic)?.delete(handler as Handler<never>);
      },
    };
  }

  publish<T extends CoreTopic>(topic: T, payload: CoreEventMap[T]): void {
    this.latest.set(topic, payload);
    const set = this.handlers.get(topic);
    if (!set || set.size === 0) return;
    // Copy: a handler may unsubscribe while we iterate.
    for (const h of [...set]) {
      try {
        (h as Handler<CoreEventMap[T]>)(payload);
      } catch (err) {
        // Never let one subscriber break the feed (console noise is intentional).
        console.error(`[core/events] listener failed for ${String(topic)}`, err);
      }
    }
  }

  /** Newest payload for a topic, or null if never published. */
  lastValue<T extends CoreTopic>(topic: T): CoreEventMap[T] | null {
    return (this.latest.get(topic) as CoreEventMap[T] | undefined) ?? null;
  }

  /** Test/shell-teardown helper: drop every subscriber. */
  clear(): void {
    this.handlers.clear();
  }
}

/** App-scoped singleton bus. */
export const coreEvents = new EventBus();

/** Convenience: subscribe and get a stable unsubscribe (React effect friendly). */
export function onCore<T extends CoreTopic>(topic: T, handler: Handler<CoreEventMap[T]>): () => void {
  const sub = coreEvents.subscribe(topic, handler);
  return () => sub.unsubscribe();
}
