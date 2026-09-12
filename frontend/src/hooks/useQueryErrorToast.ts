/**
 * useQueryErrorToast — React-side installer for the global query→toast bridge.
 *
 * Mounts `installQueryBridge` once per app with a refcount (StrictMode's
 * double-invoke and multi-page usage share one subscription). It deliberately
 * contains no toast rendering: uiStore.pushToast + ToastHost remain the only
 * toast system (4s dedupe already enforced there); auth errors stay with the
 * AppShell banner (skipped inside the bridge).
 */

import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { installQueryBridge } from "@/lib/queryBridge";

let subscribers = 0;
let sharedUnsub: (() => void) | null = null;

export function useQueryErrorToast(): void {
  const queryClient = useQueryClient();
  useEffect(() => {
    subscribers += 1;
    if (sharedUnsub === null) sharedUnsub = installQueryBridge(queryClient);
    return () => {
      subscribers -= 1;
      if (subscribers <= 0 && sharedUnsub !== null) {
        sharedUnsub();
        sharedUnsub = null;
        subscribers = 0;
      }
    };
  }, [queryClient]);
}
