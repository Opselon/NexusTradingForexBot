/**
 * useMutationFeedback — shared command-result handling.
 *
 * A command NEVER locally fakes success: the returned backend payload decides
 * the outcome. Legacy endpoints return {success: bool} / HTTP errors; both are
 * normalized into {ok, message}. The message includes the backend's own words
 * so refusals (e.g. simulation blocked in LIVE mode) surface verbatim.
 */

import { useState } from "react";
import { ApiError } from "@/types/api";
import type { LegacyMutationResult } from "@/types/api";

export interface CommandState {
  running: boolean;
  /** null = idle; true = backend-confirmed success; false = backend refusal/error. */
  lastResult: boolean | null;
  lastMessage: string | null;
}

export function useMutationFeedback(): {
  state: CommandState;
  run: (fn: () => Promise<LegacyMutationResult>) => Promise<boolean>;
} {
  const [state, setState] = useState<CommandState>({ running: false, lastResult: null, lastMessage: null });

  const run = async (fn: () => Promise<LegacyMutationResult>): Promise<boolean> => {
    setState({ running: true, lastResult: null, lastMessage: null });
    try {
      const res = await fn();
      // Legacy contract: HTTP 200 + success:false is a REFUSAL, not success.
      const ok = res.ok && res.success !== false;
      const message = res.message ?? (ok ? "Command accepted by backend." : "Backend refused the command.");
      setState({ running: false, lastResult: ok, lastMessage: message });
      return ok;
    } catch (e) {
      const message =
        e instanceof ApiError
          ? `${e.message}${e.requestId ? ` (request_id: ${e.requestId})` : ""}`
          : e instanceof Error
            ? e.message
            : "Command failed.";
      setState({ running: false, lastResult: false, lastMessage: message });
      return false;
    }
  };

  return { state, run };
}
