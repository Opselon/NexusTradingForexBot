/**
 * features/ai-providers/ui/useDbHealth.ts — the AI Providers page's PostgreSQL
 * health subscription (wave §C1/§C2, brief §26).
 *
 * ONE PROBE, SHARED STATE. The page does not open its own connection to
 * PostgreSQL and does not invent a health check: it subscribes to the
 * engine's centralized health snapshot via a TanStack query, which is exactly
 * the "cached/centralized health state" the brief asks for. The Database page
 * already polls the same endpoint on a 30s budget; a query with a matching key
 * shares that cache, so two pages cost one server-side probe (the health
 * service additionally memoizes a healthy result server-side and never
 * memoizes a failure — a broken database is re-probed live, not frozen).
 *
 * FALLBACK — the DEGRADED contract (Lane B). If the page's own
 * GET /api/ai-providers request fails with the classified 503 body, the
 * transport throws and the structured state is read off the ApiError. That
 * path is authoritative when present (it carries the server's own
 * classification + safe diagnostics), so it overrides the polled snapshot for
 * one render cycle. Every other failure is left alone: this hook never
 * converts a 500, a 401 or a network error into a database verdict.
 *
 * NO-REGRESSION: when the database is healthy, or when the health endpoint is
 * simply unavailable, this hook yields nothing the page renders — the page's
 * existing loading/error/content flow is untouched.
 */

import { useMemo } from "react";
import { useQuery, useQueryClient, type QueryKey } from "@tanstack/react-query";
import {
  type DbHealthState,
  degradedPayloadOf,
  fetchDbManageStatus,
  healthFromManageStatus,
} from "./dbHealth";

/** Query key aligned with the Database feature's manage-status key so the two
 *  pages read ONE cached probe instead of two (see features/database/useCases). */
export const DB_HEALTH_KEYS = {
  manageStatus: ["database", "manage-status"] as QueryKey,
};

/** Poll cadence — matches the Database page's manage-status budget (30s). */
const DB_HEALTH_POLL_MS = 30_000;

/**
 * Subscribe to PostgreSQL health for the AI Providers page.
 *
 * @param pageError the page's own last load error (any). When it carries the
 *   classified DEGRADED 503 body, its state is surfaced for as long as that
 *   error is current, so the banner names the condition the page actually hit.
 */
export function useDbHealth(pageError: unknown): DbHealthState {
  const query = useQuery({
    queryKey: DB_HEALTH_KEYS.manageStatus,
    queryFn: ({ signal }) => fetchDbManageStatus(signal),
    refetchInterval: DB_HEALTH_POLL_MS,
    staleTime: 2_000,
    retry: false,
  });

  const fromSnapshot = useMemo(
    () => healthFromManageStatus(query.data ?? null),
    [query.data],
  );

  // The classified 503 from the page's own route, if any. Memoized so a stable
  // error object does not flap the banner between renders.
  const fromPageError = useMemo(() => degradedPayloadOf(pageError), [pageError]);

  // Prefer the page's own classified state while that error is current; the
  // snapshot stays as the steady-state source and refreshes on its own.
  return useMemo<DbHealthState>(() => {
    if (fromPageError) {
      return {
        phase: "DEGRADED",
        database_state: fromPageError.database_state,
        diagnostics: fromPageError.safe_diagnostics,
        detail: fromPageError.detail,
      };
    }
    return fromSnapshot;
  }, [fromPageError, fromSnapshot]);
}

/**
 * True only when a health state should actually render a banner.
 *
 * HEALTHY and the initial LOADING phase render nothing (LOADING is the health
 * probe's own fetch — an honest "checking" state would flicker a banner on
 * every page mount, and the page already shows its own loading state). UNKNOWN
 * renders: the brief requires an honest empty state rather than silence when
 * the health surface cannot answer.
 */
export function shouldRenderDbHealth(state: DbHealthState): boolean {
  return state.phase === "DEGRADED" || state.phase === "UNKNOWN";
}

/**
 * Re-probe the health surface once, outside the poll cadence.
 *
 * The banner's "check again" control fires this after an operator has fixed
 * the condition (rotated the credential, started the service). The health
 * service never memoizes a failure, so a refetch always re-reads the live
 * database state. Invalidating the shared key also refreshes the Database
 * page's copy — one probe, one cache, both pages current.
 */
export function useRefetchDbHealth(): () => void {
  const queryClient = useQueryClient();
  return useMemo(
    () => () => {
      void queryClient.invalidateQueries({ queryKey: DB_HEALTH_KEYS.manageStatus });
    },
    [queryClient],
  );
}
