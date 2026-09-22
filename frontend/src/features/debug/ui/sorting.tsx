/**
 * Debug: one sorting primitive shared by every debug table/grid.
 *
 * SRP: this module knows ONLY "how a column sort works" — it never fetches,
 * never formats, and never decides what a value means. Tabs hand it a pure
 * accessor over backend rows and get back a new array (input is never
 * mutated), so a sort can never fabricate or reorder the backend's data
 * model — only the operator's view of it.
 *
 * Invariants:
 *  - null / undefined accessors ALWAYS sink to the end, in both directions
 *    (missing data is never presented as "first" or "last by value");
 *  - numeric mode compares numbers, string mode uses numeric-aware
 *    localeCompare (feat_2 < feat_10);
 *  - the default state is `key: null` = "backend order", so the untouched
 *    table shows exactly what the endpoint returned.
 */

import { useCallback, useState, type ReactNode } from "react";

export type SortDir = "asc" | "desc";

export interface SortState<K extends string = string> {
  key: K | null;
  dir: SortDir;
}

export interface SortApi<K extends string = string> {
  sort: SortState<K>;
  /** Click handler for a header: same key flips direction, new key starts asc. */
  toggle: (key: K) => void;
  isActive: (key: K) => boolean;
}

export function useSortState<K extends string>(initial: SortState<K> = { key: null, dir: "asc" }): SortApi<K> {
  const [sort, setSort] = useState<SortState<K>>(initial);
  const toggle = useCallback((key: K) => {
    setSort((prev) => (prev.key === key ? { key, dir: prev.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" }));
  }, []);
  const isActive = useCallback((key: K) => sort.key === key, [sort.key]);
  return { sort, toggle, isActive };
}

export type SortMode = "string" | "number";

/** Non-mutating sort. Unknown values sink; equal keys keep insertion order. */
export function sortRows<T>(
  rows: readonly T[],
  accessor: (row: T) => string | number | null | undefined,
  dir: SortDir,
  mode: SortMode = "string",
): T[] {
  const sign = dir === "asc" ? 1 : -1;
  return rows
    .map((row, i) => ({ row, i, v: accessor(row) }))
    .sort((a, b) => {
      const av = a.v;
      const bv = b.v;
      const aMissing = av === null || av === undefined || (typeof av === "number" && !Number.isFinite(av));
      const bMissing = bv === null || bv === undefined || (typeof bv === "number" && !Number.isFinite(bv));
      if (aMissing && bMissing) return a.i - b.i;
      if (aMissing) return 1; // sink regardless of direction — honest "unknown"
      if (bMissing) return -1;
      if (mode === "number") {
        const diff = (av as number) - (bv as number);
        return diff !== 0 ? sign * diff : a.i - b.i;
      }
      const cmp = String(av).localeCompare(String(bv), undefined, { numeric: true, sensitivity: "base" });
      return cmp !== 0 ? sign * cmp : a.i - b.i;
    })
    .map((x) => x.row);
}

/**
 * Sortable `<th>` for a `.data-table`. Renders the same uppercase header the
 * unsortable tables get, plus a direction arrow only on the active column.
 */
export function SortTh<K extends string>({
  label,
  col,
  api,
  num,
  title,
}: {
  label: string;
  col: K;
  api: SortApi<K>;
  num?: boolean;
  title?: string;
}): ReactNode {
  const active = api.isActive(col);
  const dir = active ? api.sort.dir : null;
  return (
    <th className={`${num ? "num " : ""}${active ? "sorted" : ""}`} aria-sort={dir === "asc" ? "ascending" : dir === "desc" ? "descending" : "none"} title={title}>
      <button type="button" className="dbg-sortbtn" onClick={() => api.toggle(col)}>
        <span>{label}</span>
        <span className={`dbg-sortarrow ${active ? `on ${dir}` : ""}`} aria-hidden="true">
          {dir === "asc" ? "▲" : "▼"}
        </span>
      </button>
    </th>
  );
}
