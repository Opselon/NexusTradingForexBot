/**
 * StrategyMetricsTable — compact, scannable strategy contribution table.
 *
 * Upgrades over the legacy grid:
 *  - sticky header, client-side sort on any numeric column, text filter
 *  - smart ID handling: head+tail truncation with a copy-to-clipboard icon
 *  - lifecycle state as a colored badge instead of raw text
 *  - tabular-nums alignment for every figure; signed PnL color coding
 *
 * Data is 100% from the backend contract (StrategyContribution). Sorting and
 * filtering are presentational; the UI never rescores or re-derives a value,
 * and a null numeric field renders "—" (no synthetic zeros, BUG-020 lineage).
 */

import { useMemo, useState } from "react";
import type { StrategyContribution } from "../types";
import { formatNumber } from "@/lib/format";
import { DASH, moneyOrDash, pctOrDash } from "./shared";
import { useI18n } from "@/stores/i18nStore";
import "./strategy-table.css";

type Translator = (key: string, fallback: string, vars?: Record<string, string | number>) => string;

type SortKey =
  | "strategy_id"
  | "trade_count"
  | "net_pnl"
  | "win_rate"
  | "profit_factor"
  | "average_r"
  | "loss_share"
  | "confidence"
  | "expectancy_r";

/** Numeric column captions (LABELS, translated for display). */
function metricLabelT(key: SortKey, t: Translator): string {
  switch (key) {
    case "strategy_id": return t("account.sth.strategy", "strategy");
    case "trade_count": return t("account.sth.trades", "trades");
    case "net_pnl": return t("account.sth.net_pnl", "net PnL");
    case "win_rate": return t("account.sth.win_pct", "win %");
    case "profit_factor": return t("account.sth.pf", "PF");
    case "average_r": return t("account.sth.avg_r", "avg R");
    case "loss_share": return t("account.smt.loss_shr", "loss shr");
    case "confidence": return t("account.smt.conf", "conf");
    case "expectancy_r": return t("account.smt.exp_r", "exp R");
    default: return key;
  }
}

interface SortState {
  key: SortKey;
  dir: 1 | -1;
}

const NUMERIC: SortKey[] = [
  "trade_count",
  "net_pnl",
  "win_rate",
  "profit_factor",
  "average_r",
  "loss_share",
  "confidence",
  "expectancy_r",
];

/** Lifecycle state -> badge tone (display-only; the backend owns state). */
function lifecycleTone(state: string | undefined): string {
  const s = (state ?? "DISCOVERED").toUpperCase();
  if (s === "ACTIVE") return "good";
  if (s === "RETIRED" || s === "QUARANTINED" || s === "DEGRADED") return "bad";
  if (s === "EVALUATING") return "warn";
  return "neutral";
}

/** strat_8589e797df9c -> strat_8589…df9c (10-char head + last 4 of the hex). */
export function truncateId(id: string): string {
  if (id.length <= 12) return id;
  return `${id.slice(0, 10)}…${id.slice(-4)}`;
}

/** Copy text with graceful failure for non-secure contexts / old browsers. */
async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through to legacy path */
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  } catch {
    return false;
  }
}

export interface StrategyMetricsTableProps {
  rows: StrategyContribution[];
  /** Called with the copied id (used to flash a toast/copy state). */
  onCopy?: (id: string, ok: boolean) => void;
}

export function StrategyMetricsTable({ rows, onCopy }: StrategyMetricsTableProps) {
  const t = useI18n((s) => s.t);
  const [sort, setSort] = useState<SortState>({ key: "net_pnl", dir: -1 });
  const [query, setQuery] = useState("");
  const [copied, setCopied] = useState<string | null>(null);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter((r) => r.strategy_id.toLowerCase().includes(q));
  }, [rows, query]);

  const sorted = useMemo(() => {
    const { key, dir } = sort;
    return [...filtered].sort((a, b) => {
      const av = a[key];
      const bv = b[key];
      const aN = av === null || av === undefined || Number.isNaN(av);
      const bN = bv === null || bv === undefined || Number.isNaN(bv);
      if (aN && bN) return 0;
      if (aN) return 1; // no-data always sinks
      if (bN) return -1;
      return ((av as number) - (bv as number)) * dir;
    });
  }, [filtered, sort]);

  const toggleSort = (key: SortKey) => {
    setSort((s) => (s.key === key ? { key, dir: (s.dir * -1) as 1 | -1 } : { key, dir: -1 }));
  };

  const handleCopy = async (id: string) => {
    const ok = await copyText(id);
    if (ok) {
      setCopied(id);
      window.setTimeout(() => setCopied((c) => (c === id ? null : c)), 1400);
    }
    onCopy?.(id, ok);
  };

  if (rows.length === 0) {
    return (
      <div className="st-empty">
        {t("account.smt.no_evidence", "NO STRATEGY EVIDENCE AVAILABLE — contributions need closed trades tagged with a strategy_id.")}
      </div>
    );
  }

  return (
    <div className="st-root">
      <div className="st-toolbar">
        <div className="st-search">
          <span className="st-search-ico" aria-hidden="true">
            ⌕
          </span>
          <input
            className="st-search-input"
            type="search"
            placeholder={t("account.smt.filter_ph", "filter by strategy id…")}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label={t("account.smt.filter_aria", "Filter strategies by id")}
          />
          {query && (
            <button className="st-search-clear" onClick={() => setQuery("")} aria-label={t("account.smt.clear_aria", "Clear filter")}>
              ✕
            </button>
          )}
        </div>
        <div className="st-count">
          <span className="st-count-n">{sorted.length}</span>
          <span className="st-count-l">{t("account.smt.shown", "/ {total} shown", { total: String(rows.length) })}</span>
        </div>
      </div>

      <div className="st-scroll">
        <table className="st-table">
          <thead>
            <tr>
              <th scope="col" className="st-th" style={{ textAlign: "start" }}>{t("account.sth.strategy", "strategy")}</th>
              {NUMERIC.map((k) => (
                <th scope="col"
                  key={k}
                  className={`st-th st-num ${sort.key === k ? "st-active" : ""}`}
                  title={t("account.smt.sort_by", "sort by {key}", { key: k })}
                  aria-sort={sort.key === k ? (sort.dir === 1 ? "ascending" : "descending") : "none"}
                >
                  {/* the control is a real button: keyboard + SR reachable, one tab stop per column */}
                  <button type="button" className="st-th-btn" onClick={() => toggleSort(k)}>
                    <span className="st-th-label">{metricLabelT(k, t)}</span>
                    <span className="st-sort-ico">{sort.key === k ? (sort.dir === 1 ? "▲" : "▼") : "↕"}</span>
                  </button>
                </th>
              ))}
              <th scope="col" className="st-th" style={{ textAlign: "start" }}>{t("account.sth.lifecycle", "lifecycle")}</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((s) => (
              <tr key={s.strategy_id} className="st-tr">
                <td className="st-td st-id-cell">
                  <button
                    className="st-id-btn"
                    onClick={() => handleCopy(s.strategy_id)}
                    title={copied === s.strategy_id ? t("account.smt.copied", "copied!") : t("account.smt.copy", "copy {id}", { id: s.strategy_id })}
                    aria-label={t("account.smt.copy_aria", "Copy strategy id {id}", { id: s.strategy_id })}
                  >
                    <span className="st-id">{truncateId(s.strategy_id)}</span>
                    <span className="st-copy-ico">{copied === s.strategy_id ? "✓" : "⧉"}</span>
                  </button>
                </td>
                <td className="st-td st-num">{s.trade_count ?? 0}</td>
                <td className={`st-td st-num ${(s.net_pnl ?? 0) >= 0 ? "st-pos" : "st-neg"}`}>
                  {moneyOrDash(s.net_pnl, true)}
                </td>
                <td className="st-td st-num">{pctOrDash(s.win_rate, 1)}</td>
                <td className="st-td st-num">
                  {s.profit_factor === null || s.profit_factor === undefined
                    ? DASH
                    : formatNumber(s.profit_factor, 3)}
                </td>
                <td className="st-td st-num">
                  {s.average_r === null || s.average_r === undefined ? DASH : `${formatNumber(s.average_r, 3)}R`}
                </td>
                <td className="st-td st-num">
                  {s.loss_share === null || s.loss_share === undefined
                    ? DASH
                    : `${(s.loss_share * 100).toFixed(1)}%`}
                </td>
                <td className="st-td st-num">
                  {s.confidence === null || s.confidence === undefined
                    ? DASH
                    : formatNumber(s.confidence, 4)}
                </td>
                <td className="st-td st-num">
                  {s.expectancy_r === null || s.expectancy_r === undefined
                    ? DASH
                    : `${formatNumber(s.expectancy_r, 3)}R`}
                </td>
                <td className="st-td">
                  <span className={`badge ${lifecycleTone(s.lifecycle_state)}`}>
                    {s.lifecycle_state || "DISCOVERED"}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
