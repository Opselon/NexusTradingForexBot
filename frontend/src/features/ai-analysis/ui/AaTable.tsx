/**
 * PURPOSE:  Sortable table shell for the AI Analysis ledger views — sticky
 *           header, aria-sort + visible direction arrow, row hover/focus and
 *           the shared .data-table surface (no restyling of cell content).
 * OWNER:    uiux-modern-20260926 lane 9 (ai-analysis) — future edits go here.
 * CONSUMES: props only — rows (already fetched by the caller), localized
 *           column labels and presentation-only comparators; theme tokens
 *           through aiAnalysis.css (.aa-th-*, .aa-table-wrap).
 * PROVIDES: AaTable<T> (memoized), AaColumn<T>/AaSort types and the null-safe
 *           cmpStr/cmpNum comparators.
 * INVARIANTS: presentation only — sorting re-orders the rows the backend
 *           already returned for THIS page; it never fetches, never drops a
 *           row, never derives or reformats a value; absent/null keys always
 *           sort to the END in both directions (they are neither 0 nor "");
 *           the default sort comes from the caller and must mirror the order
 *           the view already claims (the history table states "newest first");
 *           class prefix `aa` (contract §3); column headers keep `scope="col"`
 *           and non-sortable columns carry NO aria-sort attribute.
 * EXTEND:   a new sortable column = { id, label, keyOf, compare }; cell
 *           markup stays with the caller's row component.
 */
import { memo, useCallback, useMemo, useState } from "react";
import type { ReactNode } from "react";

export interface AaColumn<T> {
  /** Stable column id — sort state and React key. */
  id: string;
  /** Localized header label (already translated by the caller). */
  label: string;
  /** Right-aligns the header (cells keep their own classes). */
  num?: boolean;
  /** Value read for absent/null detection — a missing key never flips side. */
  keyOf?: (row: T) => unknown;
  /** Presentation-only comparator — omit it for a non-sortable column. */
  compare?: (a: T, b: T) => number;
}

export interface AaSort {
  id: string;
  dir: "asc" | "desc";
}

/** Absent / null / "" never compete with a real value: they stay last. */
function absent(v: unknown): boolean {
  return v === null || v === undefined || v === "";
}

/** String comparator with absent keys pinned last (direction-independent). */
export function cmpStr(a: unknown, b: unknown): number {
  const am = absent(a);
  const bm = absent(b);
  if (am && bm) return 0;
  if (am) return 1;
  if (bm) return -1;
  return String(a).localeCompare(String(b));
}

/** Numeric comparator with null/NaN pinned last — never coerced to 0. */
export function cmpNum(a: number | null | undefined, b: number | null | undefined): number {
  const am = a === null || a === undefined || !Number.isFinite(a);
  const bm = b === null || b === undefined || !Number.isFinite(b);
  if (am && bm) return 0;
  if (am) return 1;
  if (bm) return -1;
  return (a as number) - (b as number);
}

/** Sort a copy of the backend rows; direction flips real comparisons only,
 *  so missing keys remain at the end of both directions. */
function sortRows<T>(rows: readonly T[], col: AaColumn<T>, dir: "asc" | "desc"): T[] {
  const cmp = col.compare;
  if (!cmp) return rows as T[];
  const keyOf = col.keyOf;
  const out = [...rows];
  out.sort((a, b) => {
    const r = cmp(a, b);
    if (keyOf && (absent(keyOf(a)) || absent(keyOf(b)))) return r; // nulls last
    return dir === "asc" ? r : -r;
  });
  return out;
}

interface AaTableProps<T> {
  rows: readonly T[];
  columns: ReadonlyArray<AaColumn<T>>;
  /** Called once per row per (re)sort — the caller supplies key + cells. */
  renderRow: (row: T, index: number) => ReactNode;
  /** Initial order. Must describe what the view already shows (law 1). */
  defaultSort?: AaSort;
  /** Accessible name for the scrollable table region. */
  label?: string;
}

function AaTableInner<T>({ rows, columns, renderRow, defaultSort, label }: AaTableProps<T>) {
  const [sort, setSort] = useState<AaSort | null>(defaultSort ?? null);

  const sorted = useMemo(() => {
    if (!sort) return rows as T[];
    const col = columns.find((c) => c.id === sort.id);
    return col ? sortRows(rows, col, sort.dir) : (rows as T[]);
  }, [rows, columns, sort]);

  /** Click: a new column starts ascending, the active column flips. The order
   *  the view already CLAIMS is encoded in defaultSort, not in this handler. */
  const onSort = useCallback((id: string) => {
    setSort((prev) =>
      prev && prev.id === id ? { id, dir: prev.dir === "asc" ? "desc" : "asc" } : { id, dir: "asc" },
    );
  }, []);

  return (
    <div
      tabIndex={0}
      className="table-wrap aa-table-wrap"
      role={label ? "region" : undefined}
      aria-label={label}
    >
      <table className="data-table aa-table">
        <thead>
          <tr>
            {columns.map((c) => {
              const isActive = sort?.id === c.id;
              const ariaSort = !c.compare
                ? undefined
                : isActive
                  ? sort?.dir === "asc"
                    ? "ascending"
                    : "descending"
                  : "none";
              return (
                <th
                  scope="col"
                  key={c.id}
                  className={c.num ? "num" : undefined}
                  aria-sort={ariaSort}
                >
                  {c.compare ? (
                    <button type="button" className="aa-th-btn" onClick={() => onSort(c.id)}>
                      <span className="aa-th-label">{c.label}</span>
                      <span className="aa-th-arrow" aria-hidden="true">
                        {isActive ? (sort?.dir === "asc" ? "▲" : "▼") : "⇅"}
                      </span>
                    </button>
                  ) : (
                    c.label
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>{sorted.map((row, i) => renderRow(row, i))}</tbody>
      </table>
    </div>
  );
}

/** Generic component through memo — the cast keeps the row type intact. */
export const AaTable = memo(AaTableInner) as typeof AaTableInner;
