/**
 * Auth store — zustand binding over core/auth so React components can select
 * reactive auth state without polling (single subscription to the bus).
 *
 * Rule: this store never decides anything about credentials; it mirrors
 * core/auth (the source of truth) for presentation. Token value is NOT kept
 * in the store (no secret in React state) — only booleans/timestamps.
 */

import { create } from "zustand";
import { getAuthState, type AuthState } from "@/core/auth";
import { onCore } from "@/core/events";

export interface AuthStoreState {
  hasToken: boolean;
  cookieBootstrapped: boolean;
  lastUnauthorizedAt: number | null;
  /** Re-read core/auth (call after login/logout or a healed 401). */
  sync: () => void;
}

export const useAuthStore = create<AuthStoreState>((set) => ({
  ...project(getAuthState()),
  sync: () => set(project(getAuthState())),
}));

function project(s: AuthState): Pick<AuthStoreState, "hasToken" | "cookieBootstrapped" | "lastUnauthorizedAt"> {
  return { hasToken: s.hasToken, cookieBootstrapped: s.cookieBootstrapped, lastUnauthorizedAt: s.lastUnauthorizedAt };
}

// Bus -> store (app-scoped; one subscription regardless of component count).
onCore("auth:changed", () => useAuthStore.getState().sync());
onCore("auth:expired", () => useAuthStore.getState().sync());

/** Selector helpers so pages never hand-compare fields. */
export const selectNeedsAuthAttention = (s: AuthStoreState): boolean =>
  s.lastUnauthorizedAt !== null || (!s.hasToken && !s.cookieBootstrapped);
