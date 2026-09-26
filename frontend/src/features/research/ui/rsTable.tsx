/**
 * PURPOSE:  research-scoped sortable table shell — .table-wrap/.data-table
 *           markup the house DataTable renders, plus aria-sort headers with a
 *           visible direction arrow and a non-mutating row sort.
 * OWNER:    uiux-modern-20260926 lane 4 (research) — future edits go here.
 * CONSUMES: theme.css classes (.table-wrap, .data-table) + .rs-sortbtn /
 *           .rs-sortarrow rules in ./research.css; backend row values only
 *           through the caller-supplied accessors.
 * PROVIDES: useSort(), applySort(), RsTable().
 * INVARIANTS: default state is `key: null` = backend order (an untouched
 *           table shows exactly what the endpoint returned); sort never
 *           mutates the input array, never fabricates a value, and sinks
 *           missing/null cells to the end in BOTH directions so an absent
 *           value is never presented as "first"; no fetch, no new data.
 * EXTEND:   add a column by giving it a `key` + an accessor in the caller's
 *           ACCESSORS map — the header becomes sortable automatically.
 */
import { useCallback, useState, type ReactNode } from "react";

export type SortDir = "asc" | "desc";
export type SortValue = string | number | null | undefined;

export interface SortState<K extends string = string> {
  key: K | null;
  dir: SortDir;
}

/** Column header: `key` present + a SortApi passed to RsTable = sortable. */
export interface RsColumn<K extends string = string> {
  label: string;
  key?: K;
  num?: boolean;
  /** Extra header hint (title attribute) — optional. */
  title?: string;
}

/**
 * Sort toggle state. `key: null` is the untouched state (backend order);
 * clicking an inactive column starts ascending, clicking it again flips.
 */
export function useSort<K extends string>(): {
  sort: SortState<K>;
  toggle: (key: K) => void;
} {
  const [sort, setSort] = useState<SortState<K>>({ key: null, dir: "asc" });
  const toggle = useCallback((key: K) => {
    setSort((prev) => (prev.key === key ? { key, dir: prev.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" }));
  }, []);
  return { sort, toggle };
}

/**
 * Non-mutating sort over already-loaded rows. `accessor === null` (untouched
 * header) returns the input untouched. Missing/non-finite values sink in both
 * directions; equal cells keep their original order (stable).
 */
export function applySort<T>(
  rows: readonly T[],
  accessor: ((row: T) => SortValue) | null,
  dir: SortDir,
): readonly T[] {
  if (!accessor) return rows;
  const sign = dir === "asc" ? 1 : -1;
  return rows
    .map((row, i) => ({ row, i, v: accessor(row) }))
    .sort((a, b) => {
      const av = a.v;
      const bv = b.v;
      const aMissing = av === null || av === undefined || (typeof av === "number" && !Number.isFinite(av));
      const bMissing = bv === null || bv === undefined || (typeof bv === "number" && !Number.isFinite(bv));
      if (aMissing && bMissing) return a.i - b.i;
      if (aMissing) return 1;
      if (bMissing) return -1;
      if (typeof av === "number" && typeof bv === "number") {
        const diff = av - bv;
        return diff !== 0 ? sign * diff : a.i - b.i;
      }
      const cmp = String(av).localeCompare(String(bv), undefined, { numeric: true, sensitivity: "base" });
      return cmp !== 0 ? sign * cmp : a.i - b.i;
    })
    .map((x) => x.row);
}

/** Scroll container + table; sticky headers come from the house .data-table. */
export function RsTable<K extends string>({
  cols,
  sort,
  onToggle,
  children,
}: {
  cols: Array<RsColumn<K>>;
  /** Present = sortable headers (aria-sort + arrow). */
  sort?: SortState<K>;
  onToggle?: (key: K) => void;
  children: ReactNode;
}) {
  return (
    <div tabIndex={0} className="table-wrap rs-table">
      <table className="data-table">
        <thead>
          <tr>
            {cols.map((c) => {
              if (c.key === undefined || !sort || !onToggle) {
                return (
                  <th scope="col" key={c.label || "actions"} className={c.num ? "num" : undefined} title={c.title}>
                    {c.label === "" ? <span className="sr-only">actions</span> : c.label}
                  </th>
                );
              }
              const active = sort.key === c.key;
              const dir = active ? sort.dir : null;
              return (
                <th
                  scope="col"
                  key={c.key}
                  className={c.num ? `num${active ? " sorted" : ""}` : active ? "sorted" : undefined}
                  aria-sort={dir === "asc" ? "ascending" : dir === "desc" ? "descending" : "none"}
                  title={c.title}
                >
                  <button type="button" className="rs-sortbtn" onClick={() => onToggle(c.key as K)}>
                    <span>{c.label}</span>
                    <span className={`rs-sortarrow${active ? ` on ${dir}` : ""}`} aria-hidden="true">
                      {active ? (dir === "asc" ? "▲" : "▼") : "↕"}
                    </span>
                  </button>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}
