/**
 * useMutationFeedback — shared command-result handling.
 *
 * A command NEVER locally fakes success: the returned backend payload decides
 * the outcome. Legacy endpoints return {success: bool} / HTTP errors; both are
 * normalized into {ok, message}. The message includes the backend's own words
 * so refusals (e.g. simulation blocked in LIVE mode) surface verbatim.
 *
 * Pro UX layer: every settled command (accepted or refused) is also mirrored
 * into the corner toast stack. Toasts are a VISUAL MIRROR only — they never
 * replace the inline backend-verdict rendering.
 *
 * Wave-2 pass (Lane P): explicit 4 s per-message dedupe window ported from
 * legacy NX.confirmToast (Web/ux.js) — the store's dedupe guards the queue,
 * this guard also suppresses repeated *state churn* for identical verdicts, so
 * a retry loop hammering the same refusal cannot flood the inline result line.
 */

import { useRef, useState } from "react";
import { ApiError } from "@/types/api";
import type { LegacyMutationResult } from "@/types/api";
import { useUiStore } from "@/stores/uiStore";

export interface CommandState {
  running: boolean;
  /** null = idle; true = backend-confirmed success; false = backend refusal/error. */
  lastResult: boolean | null;
  lastMessage: string | null;
}

/** Legacy parity: NX.confirmToast dedupes identical messages for 4 s. */
const DEDUPE_MS = 4_000;

function verdictMessage(res: LegacyMutationResult, ok: boolean): string {
  if (res.message) return res.message; // the backend's own words, verbatim
  return ok ? "Command accepted by backend." : "Backend refused the command.";
}

function errorFromThrowable(e: unknown): string {
  if (e instanceof ApiError) return `${e.message}${e.requestId ? ` (request_id: ${e.requestId})` : ""}`;
  if (e instanceof Error) return e.message;
  return "Command failed.";
}

export function useMutationFeedback(): {
  state: CommandState;
  run: (fn: () => Promise<LegacyMutationResult>) => Promise<boolean>;
} {
  const [state, setState] = useState<CommandState>({ running: false, lastResult: null, lastMessage: null });
  const pushToast = useUiStore((s) => s.pushToast);
  const lastToast = useRef<{ key: string; at: number }>({ key: "", at: 0 });

  /** Mirror into the toast stack with the legacy 4 s identical-message window. */
  const toast = (kind: "ok" | "fail", message: string): void => {
    const key = `${kind}|${message}`;
    const now = Date.now();
    if (lastToast.current.key === key && now - lastToast.current.at < DEDUPE_MS) return;
    lastToast.current = { key, at: now };
    pushToast(kind, message);
  };

  const run = async (fn: () => Promise<LegacyMutationResult>): Promise<boolean> => {
    setState({ running: true, lastResult: null, lastMessage: null });
    try {
      const res = await fn();
      // Legacy contract: HTTP 200 + success:false is a REFUSAL, not success.
      const ok = res.ok && res.success !== false;
      const message = verdictMessage(res, ok);
      setState({ running: false, lastResult: ok, lastMessage: message });
      toast(ok ? "ok" : "fail", message);
      return ok;
    } catch (e) {
      const message = errorFromThrowable(e);
      setState({ running: false, lastResult: false, lastMessage: message });
      toast("fail", message);
      return false;
    }
  };

  return { state, run };
}
