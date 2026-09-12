/**
 * queryBridge — global react-query error → toast mirror (REALTIME lane).
 *
 * Every failed query (any page, any endpoint) is mirrored into the EXISTING
 * corner toast stack via `useUiStore.pushToast`. Rules:
 *  - ONE toast system only: uiStore already dedupes identical toasts in a 4s
 *    window — this bridge never builds a second queue/timer system.
 *  - Auth errors (ApiError.isAuthError) are SKIPPED: they route to the auth
 *    banner already rendered by AppShell (banner > toast for a global block).
 *  - Aborts (unmount / invalidation races) are not failures.
 *  - Text is the backend's own words (ApiError.message) + its request_id
 *    trail — never a fabricated summary. The decision/text rules are pure
 *    functions in lib/feedHealth.ts, unit-tested in tests/js/pro_feed.test.mjs.
 *
 * `installQueryBridge` returns the unsubscribe; useQueryErrorToast refcounts
 * it so repeated mounts share one subscription.
 */

import type { QueryCache, QueryClient } from "@tanstack/react-query";
import { shouldToastError, toastTextForError } from "@/lib/feedHealth";
import { useUiStore } from "@/stores/uiStore";

export { shouldToastError, toastTextForError };

/** Subscribe the QueryCache's error transitions to uiStore.pushToast. */
export function installQueryBridge(queryClient: QueryClient): () => void {
  const cache: QueryCache = queryClient.getQueryCache();
  const unsubscribe = cache.subscribe((event) => {
    if (event.type !== "updated") return;
    // Only the transition INTO error counts — not the retry/update loop
    // while already errored (uiStore's 4s dedupe is the backstop anyway).
    if (event.action?.type !== "error") return;
    const error = event.query.state.error;
    if (!error) return;
    if (!shouldToastError(error)) return;
    useUiStore.getState().pushToast("fail", toastTextForError(error, event.query.queryKey));
  });
  return unsubscribe;
}
