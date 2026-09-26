/**
 * PURPOSE:  Sortable data table for the news panels — sticky header, aria-sort
 *           on every column, a visible direction arrow, and row hover/focus.
 * OWNER:    uiux-modern-20260926 lane 8 (news) — future edits go here.
 * CONSUMES: props only — rows already fetched by the caller's query, plus a
 *           per-column `sortValue` that reads a backend field. No query, no
 *           endpoint, no payload of its own.
 * PROVIDES: NewsSortTable(columns, rows, rowKey, renderCells) — renders the
 *           shared `.table-wrap`/`.data-table` markup with news-* modifiers.
 * INVARIANTS: presentation only — the DEFAULT order is the backend's own
 *           (no sort active), sorting re-orders already-fetched rows and
 *           never re-fetches, filters or derives a new value; null/absent
 *           sort keys always sink to the bottom of either direction so a
 *           missing backend field is never presented as a low number;
 *           class prefix `news-` (contract §3).
 * EXTEND:   a sortable column = one `sortValue` reading an existing payload
 *           field; never sort on a frontend-computed score.
 */

import { useMemo, useState, type ReactNode } from "react";

export type SortDir = "asc" | "desc";

export interface SortColumn<T> {
  /** Stable column id (also the sort state key). */
  key: string;
  /** Header label — already localized by the caller. */
  label: string;
  /** Right-aligned tabular-numeral column (matches `.num` in the shared table). */
  num?: boolean;
  /** Backend field used for ordering only — the cell still renders the raw value. */
  sortValue: (row: T) => number | string | null;
}

interface Props<T> {
  columns: ReadonlyArray<SortColumn<T>>;
  rows: readonly T[];
  rowKey: (row: T, index: number) => string;
  /** The `<td>` cells for one row (the raw backend values). */
  renderCells: (row: T, index: number) => ReactNode;
  /** Optional starting order; `null` (default) keeps the backend's order. */
  initial?: { key: string; dir: SortDir } | null;
  /** Accessible name for the scrollable table wrapper. */
  label?: string;
}

/** Comparable key: numbers/strings compare naturally, null sinks last. */
function compareValues(a: number | string | null, b: number | string | null): number {
  if (a === null && b === null) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b));
}

export function NewsSortTable<T>({ columns, rows, rowKey, renderCells, initial = null, label }: Props<T>) {
  const [sort, setSort] = useState<{ key: string; dir: SortDir } | null>(initial);

  const active = sort ? columns.find((c) => c.key === sort.key) ?? null : null;

  // perf: one ordered copy per rows identity / sort change — not per render.
  const ordered = useMemo(() => {
    if (!active || !sort) return rows;
    const dir = sort.dir === "asc" ? 1 : -1;
    const decorated = rows.map((row, index) => ({ row, index, k: active.sortValue(row) }));
    decorated.sort((x, y) => {
      const c = compareValues(x.k, y.k);
      // ties (and null-vs-null) fall back to the backend's own order
      return c !== 0 ? c * dir : x.index - y.index;
    });
    return decorated.map((d) => d.row);
  }, [rows, active, sort]);

  const toggle = (key: string): void => {
    setSort((prev) => (prev?.key === key ? { key, dir: prev.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" }));
  };

  return (
    <div tabIndex={0} className="table-wrap news-table-wrap" aria-label={label} role="group">
      <table className="data-table news-table">
        <thead>
          <tr>
            {columns.map((c) => {
              const isActive = active?.key === c.key;
              const ariaSort = isActive ? (sort?.dir === "asc" ? "ascending" : "descending") : "none";
              return (
                <th
                  scope="col"
                  key={c.key}
                  className={c.num ? "num" : undefined}
                  aria-sort={ariaSort}
                >
                  <button type="button" className={`news-th-sort ${isActive ? "active" : ""}`} onClick={() => toggle(c.key)}>
                    <span>{c.label}</span>
                    <span className="news-th-arrow" aria-hidden="true">
                      {isActive ? (sort?.dir === "asc" ? "▲" : "▼") : "⇅"}
                    </span>
                  </button>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {ordered.map((row, i) => (
            <tr key={rowKey(row, i)}>{renderCells(row, i)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
