/**
 * Decision Trace — application services (queries + lifecycle mutations).
 *
 * The page's data lifecycle (§08):
 *   mount   -> POST observer/start + open SSE (detailed tracing begins)
 *   live    -> batches stream in; topology/latency polled slowly
 *   unmount -> close SSE + POST observer/stop (auto-stop grace covers crashes)
 *
 * Polling is deliberately slow and read-only; the LIVE evidence arrives on
 * the SSE stream, not from polls.
 */

import { useEffect, useRef } from "react";
import { useMutation, useQuery, type QueryKey } from "@tanstack/react-query";
import { traceApi } from "./api";
import { TraceStreamClient, type TraceStreamStatus } from "./stream";
import {
  selectFilteredDecisions,
  useDecisionTraceStore,
} from "./store";
import type { TraceStreamFrame } from "./types";

export const OBSERVER_KEY: QueryKey = ["trace", "observer"];
export const TOPOLOGY_KEY: QueryKey = ["trace", "topology"];
export const LATENCY_KEY: QueryKey = ["trace", "latency"];
export const INTEGRITY_KEY: QueryKey = ["trace", "integrity"];
export const DECISIONS_KEY: QueryKey = ["trace", "decisions"];
export const BUNDLE_KEY: QueryKey = ["trace", "bundle"];

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
 */
export function useTraceStream(active: boolean): {
  status: TraceStreamStatus;
  error: string | null;
} {
  const setStreamStatus = useDecisionTraceStore((s) => s.setStreamStatus);
  const appendFrame = useDecisionTraceStore((s) => s.appendFrame);
  const noteGap = useDecisionTraceStore((s) => s.noteGap);
  const status = useDecisionTraceStore((s) => s.streamStatus);
  const error = useDecisionTraceStore((s) => s.streamError);
  const clientRef = useRef<TraceStreamClient | null>(null);

  useEffect(() => {
    if (!active) return undefined;
    const store = useDecisionTraceStore.getState();
    const client = new TraceStreamClient({
      lastSeq: store.lastSeq,
      onStatus: (s, info) => setStreamStatus(s, info?.error),
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
    // setStreamStatus/appendFrame/noteGap are stable zustand actions
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  return { status, error };
}

/** Filtered rows for the feed (re-derived when filter/search changes). */
export function useFilteredDecisions() {
  const store = useDecisionTraceStore();
  return selectFilteredDecisions(store);
}
