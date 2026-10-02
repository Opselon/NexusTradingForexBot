/**
 * features/provisioning/ui/useDbHealth.ts — the Model Setup page's PostgreSQL
 * health subscription (wave §C1/§C2, brief §26).
 *
 * ONE PROBE, SHARED STATE. The page does not open its own connection to
 * PostgreSQL: it subscribes to the engine's centralized health snapshot with a
 * query key aligned to the Database page's manage-status key, so both pages
 * read ONE cached probe. The health service additionally memoizes a healthy
 * result server-side (never a failure), so a broken database is re-probed
 * live rather than frozen.
 *
 * NO-REGRESSION: a healthy database, or a health endpoint that is simply
 * unavailable, yields nothing the page renders — the page's existing content
 * is untouched.
 */

import { useMemo } from "react";
import { useQuery, useQueryClient, type QueryKey } from "@tanstack/react-query";
import { getLegacy } from "@/api/client";
import type { DbHealthSnapshot, DbManageStatus } from "../dbHealth";
import { dbHealthFromStatus } from "../dbHealth";

/** Aligned to features/database/useCases.DB_KEYS.manageStatus so the Database
 *  page and this page share ONE probe and ONE cache entry. */
export const PROVISIONING_DB_HEALTH_KEYS = {
  manageStatus: ["database", "manage-status"] as QueryKey,
};

/** Poll cadence — matches the Database page's manage-status budget (30s). */
const DB_HEALTH_POLL_MS = 30_000;

/** Fetch the centralized DB health snapshot (the page's single DB probe). */
export function fetchDbManageStatus(signal?: AbortSignal): Promise<DbManageStatus> {
  return getLegacy<DbManageStatus>("/api/db/manage/status", signal);
}

/** Subscribe to PostgreSQL health for the Model Setup page. */
export function useDbHealth(): DbHealthSnapshot {
  const query = useQuery({
    queryKey: PROVISIONING_DB_HEALTH_KEYS.manageStatus,
    queryFn: ({ signal }) => fetchDbManageStatus(signal),
    refetchInterval: DB_HEALTH_POLL_MS,
    staleTime: 2_000,
    retry: false,
  });
  return useMemo(() => dbHealthFromStatus(query.data ?? null), [query.data]);
}

/**
 * True only when a health state should render.
 *
 * HEALTHY and the initial LOADING phase render nothing (LOADING is the health
 * probe's own fetch — an honest "checking" badge would flicker on every page
 * mount). UNKNOWN renders: the brief requires an honest empty state rather
 * than silence when the health surface cannot answer.
 */
export function shouldRenderDbHealth(state: DbHealthSnapshot): boolean {
  return state.phase === "DEGRADED" || state.phase === "UNKNOWN";
}

/**
 * Re-probe the health surface once, outside the poll cadence.
 *
 * The badge's "check again" control fires this after an operator has fixed the
 * condition. The health service never memoizes a failure, so a refetch always
 * re-reads the live database state. Invalidating the shared key also refreshes
 * the Database page's copy — one probe, one cache, both pages current.
 */
export function useRefetchDbHealth(): () => void {
  const queryClient = useQueryClient();
  return useMemo(
    () => () => {
      void queryClient.invalidateQueries({ queryKey: PROVISIONING_DB_HEALTH_KEYS.manageStatus });
    },
    [queryClient],
  );
}
