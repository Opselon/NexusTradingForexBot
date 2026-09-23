/**
 * usePersistedState — display-state persistence for the account studio.
 *
 * PURPOSE:  keep display preferences (density / sort / view) across reloads
 *           under the lane prefix w6.account.* — contract §3 allows exactly
 *           this, and nothing else: the value is never data.
 * OWNER:    uiux-w6-account
 * CONSUMES: nothing
 * PROVIDES: usePersistedState<T>(key, initial, opts?)
 * INVARIANTS: read+parse failures fall back to the initial value (a broken
 *           localStorage value must never crash the page); writes are
 *           wrapped in try/catch (private mode / quota); SSR-safe.
 * EXTEND:   reuse for any w6.account.* display key.
 */

import { useCallback, useState } from "react";

const PREFIX = "w6.account";

function fullKey(key: string): string {
  return key.startsWith("w6.") ? key : `${PREFIX}.${key}`;
}

function readPersisted<T>(key: string, initial: T, isValid: (v: unknown) => boolean): T {
  if (typeof window === "undefined") return initial;
  try {
    const raw = window.localStorage.getItem(fullKey(key));
    if (raw === null || raw === undefined) return initial;
    const parsed: unknown = JSON.parse(raw);
    return isValid(parsed) ? (parsed as T) : initial;
  } catch {
    return initial;
  }
}


export interface PersistOptions {
  /**
   * Type guard against a stale/garbage persisted value (domain whitelist) —
   * e.g. `isDensity` for w6.account.density.
   */
  isValid?: (v: unknown) => boolean;
  /** Skip writing (e.g. when the caller prefers session-only state). */
  noWrite?: boolean;
}

export function usePersistedState<T>(
  key: string,
  initial: T,
  opts: PersistOptions = {},
): [T, (v: T | ((prev: T) => T)) => void] {
  const { isValid, noWrite } = opts;
  const [state, setState] = useState<T>(() =>
    isValid ? readPersisted(key, initial, isValid) : readPersisted(key, initial, () => true),
  );

  const set = useCallback(
    (v: T | ((prev: T) => T)) => {
      setState(v);
      if (noWrite) return;
      try {
        const value = typeof v === "function" ? (v as (prev: T) => T)(state) : v;
        window.localStorage.setItem(fullKey(key), JSON.stringify(value));
      } catch {
        /* storage unavailable (private mode / quota) — state is still correct in-memory */
      }
    },
    [key, noWrite, state],
  );

  return [state, set];
}

/** Density whitelist for w6.account.density (padding-only, never hides data). */
export const DENSITY_VALUES = ["cozy", "compact", "roomy"] as const;
export type Density = (typeof DENSITY_VALUES)[number];

export function isDensity(v: unknown): v is Density {
  return typeof v === "string" && (DENSITY_VALUES as readonly string[]).includes(v);
}

export const DEFAULT_DENSITY: Density = "cozy";
