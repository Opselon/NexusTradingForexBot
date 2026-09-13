/**
 * core/di — the composition container for platform-scoped services.
 *
 * WHY (SOLID / dependency inversion): the shell and hooks need to start the
 * realtime feed, and lanes 2-5 need query-key namespacing conventions — but
 * importing the singletons directly couples consumers to a module graph that
 * cannot be swapped in tests. This container is the ONE place where app-scoped
 * services are registered and resolved; everything else receives them.
 *
 * Keep it tiny and typed: a string-keyed service registry with one token per
 * platform service, not a framework.
 */

import { realtimeClient, type NseRealtimeClient } from "./realtime";
import { coreEvents, type EventBus } from "./events";

export interface PlatformServices {
  /** The app-scoped live feed client. */
  realtime: NseRealtimeClient;
  /** The app-scoped typed event bus. */
  events: EventBus;
}

const container: PlatformServices = {
  realtime: realtimeClient,
  events: coreEvents,
};

/** Resolve a platform service by key. Never `new` a singleton in a consumer. */
export function platformService<K extends keyof PlatformServices>(key: K): PlatformServices[K] {
  return container[key];
}
