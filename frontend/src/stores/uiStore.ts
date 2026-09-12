/**
 * Client/UI state store (Zustand) — visual preferences ONLY.
 *
 * Architecture rule: server state lives in TanStack Query, realtime state in
 * the websocket layer. This store never holds authoritative NSE values
 * (mode, engine_running, positions...). It holds sidebar collapse, density
 * mode, banner dismissals and the transient toast queue — pure visual state.
 */

import { create } from "zustand";

export interface ToastItem {
  id: number;
  kind: "ok" | "fail" | "info";
  text: string;
}

interface UiState {
  sidebarCollapsed: boolean;
  toggleSidebar: () => void;
  /** density=false → comfortable; true → "dense" (tighter rows/padding). Visual only. */
  dense: boolean;
  toggleDense: () => void;
  dismissedStaleBannerVersion: number | null;
  dismissStaleBanner: (version: number) => void;
  toasts: ToastItem[];
  pushToast: (kind: ToastItem["kind"], text: string) => void;
  dismissToast: (id: number) => void;
}

/** localStorage is used for VISUAL prefs only (never NSE state). Fail-soft. */
function readPref(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}
function writePref(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* private mode / disabled storage — prefs just won't persist */
  }
}

let toastSeq = 0;
/** Non-spam dedupe (4s window) — parity with legacy NX.toast (Web/ux.js). */
const toastGuard = new Map<string, number>();

export const useUiStore = create<UiState>((set) => ({
  sidebarCollapsed: readPref("nse.altui.sidebar") === "1",
  toggleSidebar: () =>
    set((s) => {
      writePref("nse.altui.sidebar", s.sidebarCollapsed ? "0" : "1");
      return { sidebarCollapsed: !s.sidebarCollapsed };
    }),
  dense: readPref("nse.altui.dense") === "1",
  toggleDense: () =>
    set((s) => {
      writePref("nse.altui.dense", s.dense ? "0" : "1");
      return { dense: !s.dense };
    }),
  dismissedStaleBannerVersion: null,
  dismissStaleBanner: (version: number) => set({ dismissedStaleBannerVersion: version }),
  toasts: [],
  pushToast: (kind, text) =>
    set((s) => {
      const key = `${kind}|${text}`;
      const now = Date.now();
      const last = toastGuard.get(key);
      if (last !== undefined && now - last < 4000) return s; // dedupe repeated toast
      toastGuard.set(key, now);
      return { toasts: [...s.toasts.slice(-3), { id: ++toastSeq, kind, text }] };
    }),
  dismissToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));
