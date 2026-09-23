/**
 * PURPOSE:  Row-density preference for the Positions page, persisted in
 *           localStorage under the lane-prefixed key w5.positions.density.
 * OWNER:    uiux-wave5-positions  (future edits to this file belong to this lane)
 * CONSUMES: window.localStorage (guarded — private-mode/quota failures are
 *           swallowed and the preference simply stays session-only).
 * PROVIDES: Density type, readDensity(), writeDensity().
 * INVARIANTS: client-side display state only — never touches the backend or
 *             the query cache; unknown stored values fall back to
 *             "comfortable" instead of being trusted.
 * EXTEND:   add display preferences here as their own key/reader pair; do not
 *           store anything derived from backend data in localStorage.
 */

export type Density = "comfortable" | "compact";

const KEY = "w5.positions.density";
const DEFAULT: Density = "comfortable";

export function readDensity(): Density {
  try {
    const v = window.localStorage.getItem(KEY);
    return v === "comfortable" || v === "compact" ? v : DEFAULT;
  } catch {
    return DEFAULT;
  }
}

export function writeDensity(value: Density): void {
  try {
    window.localStorage.setItem(KEY, value);
  } catch {
    /* storage unavailable — the toggle still works for this session */
  }
}
