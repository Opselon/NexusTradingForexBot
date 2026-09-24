/**
 * Decision Trace — application services (queries + lifecycle mutations).
 *
 * The page's data lifecycle (§08):
 *   mount   -> POST observer/start + open SSE (detailed tracing begins)
 *   live    -> batches stream in; topology/latency polled slowly;
 *              server-filtered /events pages fetched only while filters are on
 *   unmount -> close SSE + POST observer/stop (auto-stop grace covers crashes)
 *
 * Polling is deliberately slow and read-only; the LIVE evidence arrives on
 * the SSE stream, not from polls. The WHY query (§37) is a one-shot forensic
 * read of immutable history (staleTime 30s) with an explicit 404 state —
 * never retried into a fabricated answer.
 */

import { useEffect, useRef } from "react";
import { useMutation, useQuery, type QueryKey } from "@tanstack/react-query";
import { ApiError } from "@/types/api";
import { traceApi } from "./api";
import { TraceStreamClient, type TraceStreamStatus } from "./stream";
import {
  selectFilteredDecisions,
  selectFilteredEvents,
  selectQueue,
  useDecisionTraceStore,
} from "./store";
import type { EventsSince, TraceEvent, TraceStreamFrame, WhyResponse } from "./types";
import { activeFilterWords } from "./types";

export const OBSERVER_KEY: QueryKey = ["trace", "observer"];
export const TOPOLOGY_KEY: QueryKey = ["trace", "topology"];
export const LATENCY_KEY: QueryKey = ["trace", "latency"];
export const INTEGRITY_KEY: QueryKey = ["trace", "integrity"];
export const DECISIONS_KEY: QueryKey = ["trace", "decisions"];
export const BUNDLE_KEY: QueryKey = ["trace", "bundle"];
export const FILTERED_EVENTS_KEY: QueryKey = ["trace", "events", "filtered"];
export const WHY_KEY: QueryKey = ["trace", "why"];

export function useObserverQuery(paused = false) {
  return useQuery({
    queryKey: OBSERVER_KEY,
    queryFn: ({ signal }) => traceApi.observer(signal),
    refetchInterval: paused ? false : 5_000,
    staleTime: 2_000,
  });
}

export function useTopologyQuery(paused = false) {
  return useQuery({
    queryKey: TOPOLOGY_KEY,
    queryFn: ({ signal }) => traceApi.topology(signal),
    refetchInterval: paused ? false : 4_000,
    staleTime: 2_000,
  });
}

export function useLatencyQuery(paused = false) {
  return useQuery({
    queryKey: LATENCY_KEY,
    queryFn: ({ signal }) => traceApi.latency(signal),
    refetchInterval: paused ? false : 6_000,
  });
}

export function useIntegrityQuery(paused = false) {
  return useQuery({
    queryKey: INTEGRITY_KEY,
    queryFn: ({ signal }) => traceApi.integrity(signal),
    refetchInterval: paused ? false : 8_000,
  });
}

export function useDecisionsQuery(limit = 100) {
  return useQuery({
    queryKey: DECISIONS_KEY,
    queryFn: async () => {
      const page = await traceApi.decisions(limit, 0);
      useDecisionTraceStore.getState().setDecisions(page.rows, page.total);
      return page;
    },
    refetchInterval: 6_000,
    staleTime: 2_000,
  });
}

/**
 * Server-filtered /events page (§36): enabled ONLY while at least one AND
 * filter is active. The response's `filters` echo proves which params the
 * backend applied; an empty `events` array is the honest filtered-empty state
 * (§73), never backfilled with unfiltered rows.
 */
export function useFilteredEventsQuery() {
  const filters = useDecisionTraceStore((s) => s.eventFilters);
  const active =
    !!(filters.trace_id || filters.stage || filters.status || filters.mode || filters.position_id);
  return useQuery<EventsSince>({
    queryKey: [...FILTERED_EVENTS_KEY, filters],
    queryFn: ({ signal }) => traceApi.events(0, 500, filters, signal),
    enabled: active,
    refetchInterval: active ? 5_000 : false,
    staleTime: 2_000,
  });
}

/** WHY (§37): one stored event's reason evidence. 404 => explicit NOT_FOUND. */
export function useWhyQuery(eventId: string | null, enabled = true) {
  return useQuery<WhyResponse | null>({
    queryKey: [...WHY_KEY, eventId],
    queryFn: async ({ signal }) => {
      try {
        return await traceApi.why(eventId!, signal);
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) {
          return { found: false } as WhyResponse;
        }
        throw err;
      }
    },
    enabled: !!eventId && enabled,
    staleTime: 30_000, // a stored event's WHY answer is immutable history
    retry: false,
  });
}

/** Hydrate on mount: recent bounded tail so the canvas isn't blank (§8.5). */
export function useResumeHydration() {
  const lastSeq = useDecisionTraceStore((s) => s.lastSeq);
  const done = useRef(false);
  useEffect(() => {
    if (done.current) return;
    done.current = true;
    traceApi
      .events(lastSeq, 1000)
      .then((payload) =>
        useDecisionTraceStore.getState().appendFrame({ kind: "resume", payload }),
      )
      .catch(() => {
        /* hydration failure must never break the page */
      });
  }, [lastSeq]);
}

export function useBundleQuery(key: string | null, enabled = true) {
  return useQuery({
    queryKey: [...BUNDLE_KEY, key],
    queryFn: ({ signal }) => traceApi.bundle(key!, signal),
    enabled: !!key && enabled,
    staleTime: 30_000, // a forensic bundle is immutable history
  });
}

export function useStartObserver() {
  return useMutation({ mutationFn: () => traceApi.start() });
}
export function useStopObserver() {
  return useMutation({ mutationFn: () => traceApi.stop() });
}

/**
 * The full stream lifecycle bound to the page (§08 open/close protocol).
 * Opening the page activates the observer; closing it stops the stream —
 * the backend's no-subscriber grace sweep covers a crashed tab.
 *
 * §62 honesty: after any reconnect the client runs a RESYNC pass — a bounded
 * REST fetch from its own `last_seq` — so the page fills what the SSE resume
 * may have missed, without duplicating or fabricating continuity. A `gap`
 * flag from either source is surfaced as TRACE GAP, never smoothed over.
 */
export function useTraceStream(active: boolean): {
  status: TraceStreamStatus;
  error: string | null;
} {
  const setStreamStatus = useDecisionTraceStore((s) => s.setStreamStatus);
  const appendFrame = useDecisionTraceStore((s) => s.appendFrame);
  const noteGap = useDecisionTraceStore((s) => s.noteGap);
  const setResyncing = useDecisionTraceStore((s) => s.setResyncing);
  const status = useDecisionTraceStore((s) => s.streamStatus);
  const error = useDecisionTraceStore((s) => s.streamError);
  const clientRef = useRef<TraceStreamClient | null>(null);

  useEffect(() => {
    if (!active) return undefined;
    const store = useDecisionTraceStore.getState();
    let hadDisconnect = false;
    const runResync = () => {
      const st = useDecisionTraceStore.getState();
      if (st.lastSeq <= 0) return;
      setResyncing(true);
      traceApi
        .events(st.lastSeq, 2000)
        .then((payload) => {
          useDecisionTraceStore.getState().appendFrame({ kind: "resume", payload });
        })
        .catch(() => {
          /* resync failure is surfaced by the lifecycle word, never fabricated */
        })
        .finally(() => setResyncing(false));
    };
    const client = new TraceStreamClient({
      lastSeq: store.lastSeq,
      onStatus: (s, info) => {
        if (s === "reconnecting" || s === "disconnected") hadDisconnect = true;
        if (s === "connected" && hadDisconnect) {
          hadDisconnect = false;
          runResync();
        }
        setStreamStatus(s, info?.error);
      },
      onGap: (msg) => noteGap(msg),
      onFrame: (frame: TraceStreamFrame) => {
        if (frame.event === "batch") {
          appendFrame({
            kind: "batch",
            events: frame.events ?? [],
            coalesced: frame.coalesced_events ?? 0,
            dropped: frame.dropped_visual_events ?? 0,
            lastSeq: frame.last_seq ?? 0,
          });
        } else if (frame.event === "error" && frame.code === "MALFORMED_FRAME") {
          const st = useDecisionTraceStore.getState();
          useDecisionTraceStore.setState({ malformed: st.malformed + 1 });
        }
        // hello/heartbeat/gap carry no event payload; handled via callbacks.
      },
    });
    clientRef.current = client;
    client.connect();
    return () => {
      client.close();
      clientRef.current = null;
    };
    // setStreamStatus/appendFrame/noteGap/setResyncing are stable zustand actions
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  return { status, error };
}

/** Bounded trace queue (§31) — derived from retained events only. */
export function useTraceQueue() {
  return useDecisionTraceStore(selectQueue);
}

/** Filtered rows for the feed (re-derived when filter/search changes). */
export function useFilteredDecisions() {
  const store = useDecisionTraceStore();
  return selectFilteredDecisions(store);
}

/**
 * Events for the list/stream panels: server-filtered page while a filter is
 * active (with its echoed `filters`), else the bounded live tail.
 */
export function useFilteredEvents(): {
  events: TraceEvent[];
  fromServer: boolean;
  serverFilters: Record<string, string> | null;
  isPending: boolean;
  isError: boolean;
} {
  const store = useDecisionTraceStore();
  const filteredQ = useFilteredEventsQuery();
  const clientFiltered = selectFilteredEvents(store);
  const hasFilters = !!(
    store.eventFilters.trace_id ||
    store.eventFilters.stage ||
    store.eventFilters.status ||
    store.eventFilters.mode ||
    store.eventFilters.position_id
  );
  if (!hasFilters) {
    return {
      events: clientFiltered,
      fromServer: false,
      serverFilters: null,
      isPending: false,
      isError: false,
    };
  }
  if (filteredQ.isPending) {
    return { events: [], fromServer: true, serverFilters: null, isPending: true, isError: false };
  }
  if (filteredQ.isError) {
    return { events: [], fromServer: true, serverFilters: null, isPending: false, isError: true };
  }
  return {
    events: filteredQ.data?.events ?? [],
    fromServer: true,
    serverFilters:
      filteredQ.data?.filters ??
      Object.fromEntries(activeFilterWords(store.eventFilters).map((w) => [w.key, w.value])),
    isPending: false,
    isError: false,
  };
}
