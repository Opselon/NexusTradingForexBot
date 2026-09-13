/**
 * Rules: application use cases.
 *
 * Backend-authoritative contract: a toggle or parameter save NEVER optimistically
 * flips state. The mutation runs, the backend verdict (`{success:true}` or an
 * error envelope) is checked with the shared validation layer, and only on
 * confirmed success do we refetch `/api/rules` and let the server re-shape the
 * row. Refusals surface verbatim.
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryKey,
} from "@tanstack/react-query";
import { rulesApi, type RuleToggleResult } from "./api";
import { backendMessage, isBackendSuccess } from "@/features/config/validation";
import { mapRuleDto, type RuleVO } from "./model";

export const RULES_QUERY_KEY: QueryKey = ["rules", "list"];

export interface RuleCommandOutcome {
  ok: boolean;
  message: string;
  requestId: string | null;
}

/** GET /api/rules -> VO list (pure mapper in model.ts). Polling can be paused
 *  from the page (operator control over read pressure). */
export function useRulesQuery(paused = false) {
  return useQuery({
    queryKey: RULES_QUERY_KEY,
    queryFn: async ({ signal }): Promise<RuleVO[]> => {
      const rows = await rulesApi.list(signal);
      if (!Array.isArray(rows)) {
        // A legacy error envelope can arrive with HTTP 200 — surface it as an error.
        const body = rows as unknown as RuleToggleResult;
        throw new Error(backendMessage(body, "Backend returned no rule rows."));
      }
      return rows.map(mapRuleDto);
    },
    refetchInterval: paused ? false : 30_000,
    staleTime: 5_000,
  });
}

/**
 * POST /api/rules/toggle — enable/disable (and optional parameter persist).
 * Returns the normalized outcome; the caller renders it and the query is
 * invalidated on confirmed success (refetch-on-result, never assume).
 */
export function useToggleRule() {
  const queryClient = useQueryClient();
  return useMutation<RuleCommandOutcome, Error, { rule_name: string; is_enabled: boolean; parameters?: Record<string, unknown> | null }>({
    mutationFn: async ({ rule_name, is_enabled, parameters = null }): Promise<RuleCommandOutcome> => {
      try {
        const body = await rulesApi.toggle({ rule_name, is_enabled, parameters });
        const ok = isBackendSuccess(body);
        return {
          ok,
          message: ok
            ? `Rule "${rule_name}" ${is_enabled ? "ENABLED" : "DISABLED"} — confirmed by backend.`
            : backendMessage(body, "Backend refused the rule change."),
          requestId: (body.error && body.error.request_id) || null,
        };
      } catch (e) {
        return {
          ok: false,
          message: e instanceof Error ? e.message : "Rule command failed.",
          requestId: (e as { requestId?: string } | null)?.requestId ?? null,
        };
      }
    },
    onSuccess: (outcome) => {
      // Refetch on RESULT only — the server stays the authority over the row.
      if (outcome.ok) void queryClient.invalidateQueries({ queryKey: RULES_QUERY_KEY });
    },
  });
}
