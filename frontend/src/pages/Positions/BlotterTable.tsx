/**
 * PURPOSE:  Trade-style blotter table for the open-positions panel: filter
 *           toolbar, sticky sortable header (aria-sort), roving arrow-key row
 *           focus with hover/focus rings, compact-density row padding.
 * OWNER:    uiux-wave5-positions  (future edits to this file belong to this lane)
 * CONSUMES: Column<T> from pages/_shared/widgets, l4-* helpers from
 *           pages.css (.l4-toolbar/.l4-th-sort/.l4-table--scroll), pos-*
 *           classes from positions.css, Density from ./density.
 * PROVIDES: default BlotterTable<T> component.
 * INVARIANTS: renders only the rows it is handed — no row or value is ever
 *             invented; sort/filter are display-only; an empty result says so
 *             in text ("No rows match the filter."), never with fake rows;
 *             null sort values sink in both directions so an unknown never
 *             outranks a known value.
 * EXTEND:   columns arrive from the caller's Column<T> array; row chrome goes
 *           through rowClassName — never hardcode a row's meaning in here.
 */
import { memo, useMemo, useState, type KeyboardEvent } from "react";
import { type Column } from "@/pages/_shared/widgets";
import type { Density } from "./density";

interface SortState {
  key: string;
  dir: "asc" | "desc";
}

export interface BlotterTableProps<T> {
  columns: Array<Column<T>>;
  /** Rows AFTER the page-level filter — sort order is owned here. */
  rows: T[];
  /** Unfiltered row count, for the "x of y rows" toolbar readout. */
  totalCount: number;
  rowKey: (row: T, i: number) => string;
  initialSort?: SortState;
  /** Controlled filter text (the page keeps it to derive the strip sums). */
  query: string;
  onQueryChange: (q: string) => void;
  emptyMessage?: string;
  /** Extra class per row (side rail) — values are chosen by the caller. */
  rowClassName?: (row: T, i: number) => string | undefined;
  density: Density;
}

const BlotterTableImpl = function BlotterTable<T>({
  columns,
  rows,
  totalCount,
  rowKey,
  initialSort,
  query,
  onQueryChange,
  emptyMessage = "No rows.",
  rowClassName,
  density,
}: BlotterTableProps<T>) {
  const [sort, setSort] = useState<SortState | null>(initialSort ?? null);
  const [focusRow, setFocusRow] = useState(0);

  const visible = useMemo(() => {
    if (!sort) return rows;
    const col = columns.find((c) => c.key === sort.key);
    const sv = col?.sortValue;
    if (!sv) return rows;
    return [...rows].sort((a, b) => {
      const va = sv(a);
      const vb = sv(b);
      // Nulls sink in both directions — an unknown never outranks a value.
      if (va === null || va === undefined) return 1;
      if (vb === null || vb === undefined) return -1;
      const cmp = typeof va === "number" && typeof vb === "number" ? va - vb : String(va).localeCompare(String(vb));
      return sort.dir === "asc" ? cmp : -cmp;
    });
  }, [rows, sort, columns]);

  const activeRow = focusRow < visible.length ? focusRow : Math.max(0, visible.length - 1);
  const countLabel = visible.length === totalCount ? `${totalCount} rows` : `${visible.length} of ${totalCount} rows`;

  /** Arrow/Home/End move the roving row focus; keys inside action buttons are
   *  left alone (target !== currentTarget) so button shortcuts keep working. */
  const onRowKeyDown = (e: KeyboardEvent<HTMLTableRowElement>, i: number): void => {
    if (e.target !== e.currentTarget) return;
    const n = visible.length;
    let next: number | null = null;
    if (e.key === "ArrowDown") next = Math.min(i + 1, n - 1);
    else if (e.key === "ArrowUp") next = Math.max(i - 1, 0);
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = n - 1;
    if (next === null) return;
    e.preventDefault();
    const target = e.currentTarget.parentElement?.children.item(next) as HTMLElement | null;
    target?.focus();
  };

  return (
    <div>
      <div className="l4-toolbar" style={{ padding: "8px 10px 0" }}>
        <input
          className="input"
          style={{ inlineSize: 240 }}
          placeholder="filter rows (ticket / symbol / text)…"
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          aria-label="Filter table rows"
        />
        <span className="timestamp-note">{countLabel}</span>
        <span className="timestamp-note" title="keyboard row navigation — focus a row, then move with the arrow keys">
          rows: ↑ ↓ Home End
        </span>
        {query && (
          <button className="btn small ghost" onClick={() => onQueryChange("")}>
            clear
          </button>
        )}
      </div>
      <div className="table-wrap l4-table--scroll" style={{ maxBlockSize: 460 }}>
        <table className={`data-table pos-blotter${density === "compact" ? " pos-compact" : ""}`}>
          <thead>
            <tr>
              {columns.map((c) => {
                const active = sort?.key === c.key;
                return (
                  <th
                    key={c.key}
                    className={c.num ? "num" : undefined}
                    style={c.width ? { inlineSize: c.width } : undefined}
                    aria-sort={active ? (sort!.dir === "asc" ? "ascending" : "descending") : "none"}
                  >
                    {c.sortValue ? (
                      <button
                        className="l4-th-sort"
                        onClick={() =>
                          setSort((s) => (s?.key === c.key ? { key: c.key, dir: s.dir === "asc" ? "desc" : "asc" } : { key: c.key, dir: "desc" }))
                        }
                        title={`sort by ${c.label}`}
                      >
                        {c.label}
                        <span className="dir">{active ? (sort!.dir === "asc" ? "▲" : "▼") : "⇅"}</span>
                      </button>
                    ) : (
                      c.label
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {visible.map((row, i) => (
              <tr
                key={rowKey(row, i)}
                className={rowClassName?.(row, i)}
                tabIndex={i === activeRow ? 0 : -1}
                onFocus={() => setFocusRow(i)}
                onKeyDown={(e) => onRowKeyDown(e, i)}
              >
                {columns.map((c) => (
                  <td key={c.key} className={c.num ? "num" : undefined}>
                    {c.render(row, i)}
                  </td>
                ))}
              </tr>
            ))}
            {visible.length === 0 && (
              <tr>
                <td colSpan={columns.length} style={{ textAlign: "center", color: "var(--text-faint)", paddingBlock: 18 }}>
                  {rows.length === 0 ? emptyMessage : "No rows match the filter."}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};

/**
 * Memoized on props: with stable columns/rows/handlers the blotter skips the
 * AppShell 1s re-render cascade; internal sort/focus state still re-renders
 * it directly. The cast keeps the generic prop signature for callers intact
 * (React.memo is applied at runtime only).
 */
export default memo(BlotterTableImpl) as unknown as typeof BlotterTableImpl;
