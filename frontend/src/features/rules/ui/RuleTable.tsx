/**
 * RuleTable — the matrix itself: sortable headers, category group rows,
 * click-to-copy rule ids, search highlighting, switch + ON/OFF status,
 * parameter chips, row actions.
 *
 * Contract: the switch never flips optimistically — clicking it only ARMS the
 * page's ConfirmModal; aria-checked keeps reflecting the last backend read
 * until the refetch lands. Sort/grouping is display-only over the already
 * filtered rows the page passes in. Pure presentational component.
 */

import { memo, type ReactNode } from "react";
import { useUiStore } from "@/stores/uiStore";
import { catTone, type RuleSort, type RuleSortKey, type RuleVO } from "../model";

/** Case-insensitive `<mark>` highlight of every `q` occurrence in `text`. */
function highlight(text: string, q: string): ReactNode {
  const needle = q.trim();
  if (needle === "") return text;
  const lower = text.toLowerCase();
  const n = needle.toLowerCase();
  const out: ReactNode[] = [];
  let i = 0;
  let key = 0;
  for (;;) {
    const idx = lower.indexOf(n, i);
    if (idx === -1) {
      out.push(text.slice(i));
      break;
    }
    if (idx > i) out.push(text.slice(i, idx));
    out.push(
      <mark className="rl-hl" key={key++}>
        {text.slice(idx, idx + needle.length)}
      </mark>,
    );
    i = idx + needle.length;
  }
  return out;
}

const SORTABLE: Array<{ key: RuleSortKey; label: string }> = [
  { key: "name", label: "rule" },
  { key: "category", label: "category" },
  { key: "status", label: "status" },
];

function Th({
  col,
  label,
  sort,
  onSort,
  end,
}: {
  col?: { key: RuleSortKey; label: string };
  /** Plain (non-sortable) column label — used when `col` is absent. */
  label?: string;
  sort: RuleSort;
  onSort: (k: RuleSortKey) => void;
  end?: boolean;
}) {
  const active = col ? sort.key === col.key : false;
  return (
    <th
      className={end ? "a-end" : undefined}
      aria-sort={col ? (active ? (sort.dir === "asc" ? "ascending" : "descending") : "none") : undefined}
    >
      <div className="th-in">
        {col ? (
          <button className="rl-th-btn" onClick={() => onSort(col.key)} title={`sort by ${col.label}`}>
            {col.label}
            {active && <span className="arrow" aria-hidden="true">{sort.dir === "asc" ? "▲" : "▼"}</span>}
          </button>
        ) : (
          label
        )}
      </div>
    </th>
  );
}

/** Memoized rule row (matrix up to ~40 rows). Primitive/stable props only:
 *  the shell re-renders every second (nowMs tick into the page) — rows must
 *  bail out of that tick instead of re-rendering 5 cells x N rows. The switch
 *  still never flips optimistically: it only arms the page's ConfirmModal. */
const RuleRow = memo(function RuleRow({
  rule,
  query,
  busy,
  onEdit,
  onToggleRequest,
}: {
  rule: RuleVO;
  query: string;
  busy: boolean;
  onEdit: (rule: RuleVO) => void;
  onToggleRequest: (rule: RuleVO) => void;
}) {
  const pushToast = useUiStore((s) => s.pushToast);
  const copyName = (r: RuleVO) => {
    const clip = navigator.clipboard;
    if (!clip?.writeText) {
      pushToast("fail", "clipboard unavailable in this context");
      return;
    }
    void clip
      .writeText(r.name)
      .then(() => pushToast("ok", `rule id copied — ${r.name}`))
      .catch(() => pushToast("fail", "clipboard write blocked by the browser"));
  };

  const [pfx, ...rest] = rule.name.startsWith("RULE_") ? ["RULE_", rule.name.slice(5)] : ["", rule.name];
  const bare = rest.join("_") || rule.name;
  return (
    <tr className={rule.enabled ? "rl-on" : "rl-off"}>
      <td>
        <button
          className="rl-name"
          onClick={() => copyName(rule)}
          title={`copy ${rule.name}`}
          aria-label={`Copy rule id ${rule.name}`}
        >
          {pfx && <span className="pfx">{highlight(pfx, query)}</span>}
          <span className="txt">{highlight(bare, query)}</span>
          <span className="cpy" aria-hidden="true">⧉</span>
        </button>
      </td>
      <td>
        <span className={`rl-tag rl-tone-${catTone(rule.category)}`} title={rule.category}>
          <span className="lbl">{highlight(rule.category, query)}</span>
        </span>
      </td>
      <td>
        <div className="rl-status">
          <button
            className={`rl-sw ${rule.enabled ? "on" : ""}`}
            role="switch"
            aria-checked={rule.enabled}
            aria-label={`${rule.enabled ? "Disable" : "Enable"} rule ${rule.name}`}
            disabled={busy}
            onClick={() => onToggleRequest(rule)}
            title={rule.enabled ? "disable this rule…" : "enable this rule…"}
          />
          <span className={`rl-state ${rule.enabled ? "on" : ""}`}>{rule.enabled ? "ON" : "OFF"}</span>
        </div>
      </td>
      <td>
        {rule.paramsError ? (
          <span className="badge bad" title={rule.paramsError}>PARAMS PARSE ERROR</span>
        ) : rule.params.length === 0 ? (
          <span className="rl-pempty">no parameters</span>
        ) : (
          <div className="rl-params">
            {rule.params.map((p) => (
              <span
                key={p.key}
                className={`rl-pchip ${p.threshold ? "thr" : ""}`}
                title={`${p.key} = ${p.value}${p.threshold ? " (threshold-shaped key)" : ""}`}
              >
                {p.key}=<b>{p.value}</b>
              </span>
            ))}
          </div>
        )}
      </td>
      <td>
        <div className="rl-act">
          <button
            className="btn small ghost"
            onClick={() => onEdit(rule)}
            disabled={rule.params.length === 0}
            title={rule.params.length === 0 ? "this rule stores no parameters" : "edit parameters…"}
          >
            ✎ Edit
          </button>
        </div>
      </td>
    </tr>
  );
});

export function RuleTable({
  rows,
  grouped,
  query,
  sort,
  onSort,
  onEdit,
  onToggleRequest,
  busy,
}: {
  rows: RuleVO[];
  grouped: boolean;
  query: string;
  sort: RuleSort;
  onSort: (key: RuleSortKey) => void;
  onEdit: (rule: RuleVO) => void;
  onToggleRequest: (rule: RuleVO) => void;
  busy: boolean;
}) {
  // Group into consecutive same-category runs (valid only when the page sorted
  // by category — sortRules keeps each category contiguous).
  const groups: Array<{ cat: string; items: RuleVO[] }> = [];
  if (grouped) {
    for (const r of rows) {
      const last = groups[groups.length - 1];
      if (last && last.cat === r.category) last.items.push(r);
      else groups.push({ cat: r.category, items: [r] });
    }
  }

  const renderRow = (rule: RuleVO) => (
    <RuleRow key={rule.name} rule={rule} query={query} busy={busy} onEdit={onEdit} onToggleRequest={onToggleRequest} />
  );

  return (
    <div tabIndex={0} className="rl-table-wrap">
      <table className="rl-table">
        <thead>
          <tr>
            <Th col={SORTABLE[0]} sort={sort} onSort={onSort} />
            <Th col={SORTABLE[1]} sort={sort} onSort={onSort} />
            <Th col={SORTABLE[2]} sort={sort} onSort={onSort} />
            <Th label="parameters / thresholds" sort={sort} onSort={onSort} />
            <Th label="actions" sort={sort} onSort={onSort} end />
          </tr>
        </thead>
        {grouped ? (
          groups.map(({ cat, items }) => {
            const on = items.filter((r) => r.enabled).length;
            return (
              <tbody key={cat}>
                <tr className="rl-grp">
                  <td colSpan={5}>
                    <div className="g-in">
                      <span className={`rl-tag rl-tone-${catTone(cat)}`}>
                        <span className="lbl">{cat}</span>
                      </span>
                      <span className="g-meta">
                        {items.length} rule{items.length === 1 ? "" : "s"}
                      </span>
                      <span className={`g-meta ${on > 0 ? "on" : ""}`}>{on} enabled</span>
                    </div>
                  </td>
                </tr>
                {items.map(renderRow)}
              </tbody>
            );
          })
        ) : (
          <tbody>{rows.map(renderRow)}</tbody>
        )}
      </table>
    </div>
  );
}
