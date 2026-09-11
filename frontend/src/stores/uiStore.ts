/**
 * Client/UI state store (Zustand) — visual preferences ONLY.
 *
 * Architecture rule: server state lives in TanStack Query, realtime state in
 * the websocket layer. This store never holds authoritative NSE values
 * (mode, engine_running, positions...). It holds sidebar collapse and the
 * operator-acknowledged banner dismissal — pure visual state.
 */

import { create } from "zustand";

interface UiState {
  sidebarCollapsed: boolean;
  toggleSidebar: () => void;
  dismissedStaleBannerVersion: number | null;
  dismissStaleBanner: (version: number) => void;
}

export const useUiStore = create<UiState>((set) => ({
  sidebarCollapsed: false,
  toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
  dismissedStaleBannerVersion: null,
  dismissStaleBanner: (version: number) => set({ dismissedStaleBannerVersion: version }),
}));
