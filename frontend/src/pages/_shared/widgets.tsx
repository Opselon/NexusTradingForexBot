/**
 * Lane-4 page-local widget kit: sortable/filterable table shell, exposure
 * meters, drawer. Lives under pages/_shared because `components/` is another
 * lane's surface this wave (lane 2 owns components/viz) — the pages must not
 * depend on it. Presentation only: no fetch, no derived NSE verdicts.
 *
 * Every tone decision here restates a backend value (a boolean, a number vs a
 * backend-supplied limit, or a backend status word). Missing input renders
 * `unknown` (hatched, never green): an unproven zero is not a satisfied gate.
 */

import { memo, useMemo, useRef, useState, type ReactNode } from "react";
import { useDialogA11y } from "../../components/useDialogA11y";
import { formatNumber } from "@/lib/format";
import "@/pages/_shared/pages.css";

// ---------------------------------------------------------------------------
// Sortable + filterable table
// ---------------------------------------------------------------------------

export interface Column<T> {
  key: string;
  label: string;
  num?: boolean;
  /** Value used for sorting — raw (number/string), not the display string. */
  sortValue?: (row: T) => number | string | null;
  render: (row: T, index: number) => ReactNode;
  /** Hide on narrow screens? (kept simple: min-width only) */
  width?: number;
}

/** Default sort value: the first non-null cell the column renders is not
 *  knowable here, so callers supply sortValue; without it sorting is disabled
 *  for that column (no string-compare surprises on objects). */
const SortableTableImpl = function SortableTable<T>({
  columns,
  rows,
  rowKey,
  initialSort,
  filter,
  emptyMessage = "No rows.",
  maxHeight = 460,
  onRowClick,
  dense = false,
}: {
  columns: Array<Column<T>>;
  rows: T[];
  rowKey: (row: T, i: number) => string;
  initialSort?: { key: string; dir: "asc" | "desc" };
  filter?: (row: T, query: string) => boolean;
  emptyMessage?: string;
  maxHeight?: number | null;
  onRowClick?: (row: T) => void;
  dense?: boolean;
}) {
  const [sort, setSort] = useState(initialSort ?? null);
  const [query, setQuery] = useState("");

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    let out = rows;
    if (filter && q) out = out.filter((r) => filter(r, q));
    if (sort) {
      const col = columns.find((c) => c.key === sort.key);
      if (col?.sortValue) {
        const sv = col.sortValue;
        out = [...out].sort((a, b) => {
          const va = sv(a);
          const vb = sv(b);
          if (va === null || va === undefined) return 1;
          if (vb === null || vb === undefined) return -1;
          const cmp = typeof va === "number" && typeof vb === "number" ? va - vb : String(va).localeCompare(String(vb));
          return sort.dir === "asc" ? cmp : -cmp;
        });
      }
    }
    return out;
  }, [rows, sort, columns, query, filter]);

  return (
    <div>
      {filter && (
        <div className="l4-toolbar" style={{ padding: "8px 10px 0" }}>
          <input
            className="input"
            style={{ inlineSize: 240 }}
            placeholder="filter rows (ticket / symbol / text)…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="Filter table rows"
          />
          <span className="timestamp-note">
            {visible.length === rows.length ? `${rows.length} rows` : `${visible.length} of ${rows.length} rows`}
          </span>
          {query && (
            <button className="btn small ghost" onClick={() => setQuery("")}>
              clear
            </button>
          )}
        </div>
      )}
      <div className={`table-wrap ${maxHeight ? "l4-table--scroll" : ""}`} style={maxHeight ? { maxBlockSize: maxHeight } : undefined}>
        <table className="data-table">
          <thead>
            <tr>
              {columns.map((c) => {
                const active = sort?.key === c.key;
                return (
                  <th scope="col"
                    key={c.key}
                    className={c.num ? "num" : undefined}
                    style={c.width ? { inlineSize: c.width } : undefined}
                    aria-sort={active ? (sort!.dir === "asc" ? "ascending" : "descending") : undefined}
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
                className={onRowClick ? "l4-clickable" : undefined}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                tabIndex={onRowClick ? 0 : undefined}
                onKeyDown={
                                  onRowClick
                                    ? (e) => {
                                        if (e.key === "Enter" || e.key === " ") {
                                          e.preventDefault();
                                          e.currentTarget.click();
                                        }
                                      }
                                    : undefined
                                }
              >
                {columns.map((c) => (
                  <td key={c.key} className={c.num ? "num" : undefined} style={dense ? { paddingBlock: 2 } : undefined}>
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
 * SortableTable — memoized on its props. With stable columns/rows/rowKey/
 * filter references the table skips the AppShell 1s re-render cascade; its
 * internal sort/filter state still re-renders it directly. Call sites that
 * pass inline lambdas simply re-render as before (no behaviour change). The
 * cast keeps the generic prop signature intact for every caller.
 */
export const SortableTable = memo(SortableTableImpl) as unknown as typeof SortableTableImpl;

// ---------------------------------------------------------------------------
// Meter (value vs backend limit) — exposure / utilization bars
// ---------------------------------------------------------------------------

export type MeterTone = "ok" | "warn" | "bad" | "unknown";

export function MeterBar({
  label,
  value,
  limit,
  unit = "",
  digits = 2,
  tone,
  caption,
  fraction,
}: {
  label: string;
  /** Backend-measured value (null → unknown). */
  value: number | null | undefined;
  /** Backend-configured limit; null → the budget is unknown, never assumed. */
  limit?: number | null;
  unit?: string;
  digits?: number;
  /** Caller-supplies-the-verdict tone (pages decide with backend numbers). */
  tone: MeterTone;
  caption?: string;
  /** Explicit 0..1 fill; defaults to value/limit arithmetic. */
  fraction?: number | null;
}) {
  const hasValue = typeof value === "number" && Number.isFinite(value);
  const hasLimit = typeof limit === "number" && Number.isFinite(limit) && (limit as number) > 0;
  const frac = fraction ?? (hasValue && hasLimit ? (value as number) / (limit as number) : null);
  const widthPct = frac === null ? null : Math.max(0, Math.min(1, frac)) * 100;
  return (
    <div className="l4-meter">
      <div className="l4-meter__row">
        <span className="l4-meter__lab" title={label}>
          {label}
        </span>
        <span className="l4-meter__track" role="img" aria-label={`${label}: ${hasValue ? `${value}${unit}` : "unknown"}`}>
          <i
            className={`l4-meter__fill ${widthPct === null ? "unknown" : tone}`}
            style={{ inlineSize: widthPct === null ? "100%" : `${widthPct}%` }}
          />
        </span>
        <span className="l4-meter__val">
          {hasValue ? `${formatNumber(value, digits)}${unit}` : "UNKNOWN"}
          {hasLimit ? <span className="faint"> / {formatNumber(limit, digits)}{unit}</span> : null}
        </span>
      </div>
      <span className="l4-meter__caption">
        {caption ??
          (widthPct === null
            ? hasValue
              ? "no backend limit in payload — bar is indeterminate, never shown as satisfied"
              : "no backend value — nothing measured"
            : `${Math.round(frac! * 100)}% of the backend limit (arithmetic on two backend values)`)}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Drawer — row detail without leaving the table
// ---------------------------------------------------------------------------

export function Drawer({
  title,
  onClose,
  children,
  footer,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}) {
  const boxRef = useRef<HTMLElement | null>(null);
  useDialogA11y(boxRef, onClose);

  return (
    <>
      <div className="l4-drawer-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()} />
      <aside ref={boxRef} className="l4-drawer" role="dialog" aria-modal="true" aria-label={title}>
        <div className="l4-drawer__head">
          <span>{title}</span>
          <button className="btn small ghost" style={{ marginInlineStart: "auto" }} onClick={onClose} aria-label="Close panel">
            esc ✕
          </button>
        </div>
        <div tabIndex={0} className="l4-drawer__body">{children}</div>
        {footer && <div className="l4-drawer__foot">{footer}</div>}
      </aside>
    </>
  );
}

/** Raw-JSON block for payload drill-downs (never prettified into prose). */
export function JsonBlock({ value, label }: { value: unknown; label?: string }) {
  let text = "—";
  try {
    text = JSON.stringify(value, null, 2) ?? "—";
  } catch {
    text = String(value);
  }
  return (
    <div>
      {label && <div className="section-title">{label}</div>}
      <pre tabIndex={0} className="l4-json">{text}</pre>
    </div>
  );
}

/** Small labelled value chip used in header strips. */
export function InfoChip({ k, v, tone = "" }: { k: string; v: ReactNode; tone?: "" | "good" | "bad" | "warn" | "accent" }) {
  return (
    <span className={`l4-chip ${tone}`} title={`${k}: ${typeof v === "string" || typeof v === "number" ? v : ""}`}>
      <span className="faint">{k} </span>
      {v}
    </span>
  );
}
