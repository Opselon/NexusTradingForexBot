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

import { type ReactNode } from "react";
import { useUiStore } from "@/stores/uiStore";
import { useI18n } from "@/stores/i18nStore";
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
  const t = useI18n((s) => s.t);
  const active = col ? sort.key === col.key : false;
  const text: string = !col
    ? (label ?? "")
    : col.key === "name"
      ? t("rules.th.rule", "rule")
      : col.key === "category"
        ? t("rules.th.category", "category")
        : t("rules.th.status", "status");
  return (
    <th
      className={end ? "a-end" : undefined}
      aria-sort={col ? (active ? (sort.dir === "asc" ? "ascending" : "descending") : "none") : undefined}
    >
      <div className="th-in">
        {col ? (
          <button
            className="rl-th-btn"
            onClick={() => onSort(col.key)}
            title={t("rules.toolbar.sort_by", "sort by {key}", { key: text })}
          >
            {text}
            {active && <span className="arrow" aria-hidden="true">{sort.dir === "asc" ? "▲" : "▼"}</span>}
          </button>
        ) : (
          label
        )}
      </div>
    </th>
  );
}

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
  const pushToast = useUiStore((s) => s.pushToast);
  const t = useI18n((s) => s.t);

  const copyName = (rule: RuleVO) => {
    const clip = navigator.clipboard;
    if (!clip?.writeText) {
      pushToast("fail", t("rules.row.clip_unavailable", "clipboard unavailable in this context"));
      return;
    }
    void clip
      .writeText(rule.name)
      .then(() => pushToast("ok", t("rules.row.clip_copied", "rule id copied — {name}", { name: rule.name })))
      .catch(() => pushToast("fail", t("rules.row.clip_blocked", "clipboard write blocked by the browser")));
  };

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

  const renderRow = (rule: RuleVO) => {
    const [pfx, ...rest] = rule.name.startsWith("RULE_")
      ? ["RULE_", rule.name.slice(5)]
      : ["", rule.name];
    const bare = rest.join("_") || rule.name;
    return (
      <tr key={rule.name} className={rule.enabled ? "rl-on" : "rl-off"}>
        <td>
          <button
            className="rl-name"
            onClick={() => copyName(rule)}
            title={t("rules.row.copy_title", "copy {name}", { name: rule.name })}
            aria-label={t("rules.row.copy_a11y", "Copy rule id {name}", { name: rule.name })}
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
              aria-label={`${rule.enabled ? t("rules.confirm.disable_label", "Disable rule") : t("rules.confirm.enable_label", "Enable rule")} ${rule.name}`}
              disabled={busy}
              onClick={() => onToggleRequest(rule)}
              title={rule.enabled ? t("rules.row.sw_disable", "disable this rule…") : t("rules.row.sw_enable", "enable this rule…")}
            />
            <span className={`rl-state ${rule.enabled ? "on" : ""}`}>{rule.enabled ? t("rules.row.on", "ON") : t("rules.row.off", "OFF")}</span>
          </div>
        </td>
        <td>
          {rule.paramsError ? (
            <span className="badge bad" title={rule.paramsError}>{t("rules.row.params_error", "PARAMS PARSE ERROR")}</span>
          ) : rule.params.length === 0 ? (
            <span className="rl-pempty">{t("rules.row.no_params", "no parameters")}</span>
          ) : (
            <div className="rl-params">
              {rule.params.map((p) => (
                <span
                  key={p.key}
                  className={`rl-pchip ${p.threshold ? "thr" : ""}`}
                  title={`${p.key} = ${p.value}${p.threshold ? t("rules.row.thr_key", " (threshold-shaped key)") : ""}`}
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
              title={
                rule.params.length === 0
                  ? t("rules.row.no_params_title", "this rule stores no parameters")
                  : t("rules.action.edit_params", "edit parameters…")
              }
            >
              ✎ {t("rules.action.edit", "Edit")}
            </button>
          </div>
        </td>
      </tr>
    );
  };

  return (
    <div tabIndex={0} className="rl-table-wrap">
      <table className="rl-table">
        <thead>
          <tr>
            <Th col={SORTABLE[0]} sort={sort} onSort={onSort} />
            <Th col={SORTABLE[1]} sort={sort} onSort={onSort} />
            <Th col={SORTABLE[2]} sort={sort} onSort={onSort} />
            <Th label={t("rules.th.params", "parameters / thresholds")} sort={sort} onSort={onSort} />
            <Th label={t("rules.th.actions", "actions")} sort={sort} onSort={onSort} end />
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
                        {items.length === 1
                          ? t("rules.group.rule_one", "{n} rule", { n: items.length })
                          : t("rules.group.rule_n", "{n} rules", { n: items.length })}
                      </span>
                      <span className={`g-meta ${on > 0 ? "on" : ""}`}>{t("rules.group.on_n", "{n} enabled", { n: on })}</span>
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
