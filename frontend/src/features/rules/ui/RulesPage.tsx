/**
 * Rules — trading rule matrix (parity: Web/app.js tab-rules, loadRules /
 * renderRuleCard / toggleRuleState / saveRuleParameters).
 *
 * Presentation only: data + commands come from ./hooks (-> useCases -> api ->
 * core transport). Differences from legacy, deliberate:
 *  - toggling asks for confirmation (30+ rules gate real order flow) and shows
 *    the BACKEND verdict inline, then refetches — never assumes success;
 *  - parameter edits run through the shared validation layer; an invalid
 *    payload is blocked client-side and never reaches the wire;
 *  - search + category filter + sortable columns replace the card wall.
 */

import { useMemo, useState } from "react";
import { useUiStore } from "@/stores/uiStore";
import { ConfirmModal, DataTable, EmptyState, MetricCard, Panel, Skeleton, StatusBadge } from "@/components/primitives";
import { FieldRow, FreshnessCaption, NumberField, PollControl, QuerySection, ResultStrip, TextField, usePolling } from "@/features/config/ui/kit";
import "@/features/config/ui/kit.css";
import { flattenErrors, hasErrors } from "@/features/config/validation";
import type { ShellPageProps } from "@/app/featureModule";
import { useRulesQuery, useToggleRule, type RuleCommandOutcome } from "../hooks";
import {
  filterRules,
  paramPayload,
  ruleCategories,
  sortRules,
  validateParamEdits,
  type RuleSort,
  type RuleSortKey,
  type RuleStatusFilter,
  type RuleVO,
} from "../model";

type Draft = Record<string, string>;

const STATUS_OPTIONS: Array<{ id: RuleStatusFilter; label: string }> = [
  { id: "all", label: "ALL" },
  { id: "enabled", label: "ENABLED" },
  { id: "disabled", label: "DISABLED" },
];

export default function RulesPage(props: ShellPageProps) {
  void props;
  const poll = usePolling(30_000);
  const query = useRulesQuery(poll.paused);
  const toggle = useToggleRule();
  const pushToast = useUiStore((s) => s.pushToast);

  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("all");
  const [status, setStatus] = useState<RuleStatusFilter>("all");
  const [sort, setSort] = useState<RuleSort>({ key: "category", dir: "asc" });
  const [pending, setPending] = useState<{ rule: RuleVO; enable: boolean; parameters: Record<string, unknown> | null; label: string } | null>(null);
  const [result, setResult] = useState<RuleCommandOutcome | null>(null);
  const [editing, setEditing] = useState<{ rule: RuleVO; draft: Draft } | null>(null);

  const rows = useMemo(() => {
    const all = query.data ?? [];
    return sortRules(filterRules(all, { query: search, category, status }), sort);
  }, [query.data, search, category, status, sort]);

  const cats = useMemo(() => ruleCategories(query.data ?? []), [query.data]);
  const counts = useMemo(() => {
    const all = query.data ?? [];
    const enabled = all.filter((r) => r.enabled).length;
    const broken = all.filter((r) => r.paramsError).length;
    return { total: all.length, enabled, disabled: all.length - enabled, broken };
  }, [query.data]);

  const toggleSort = (key: RuleSortKey) =>
    setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" }));

  const runCommand = async () => {
    if (!pending) return;
    const outcome = await toggle.mutateAsync({
      rule_name: pending.rule.name,
      is_enabled: pending.enable,
      parameters: pending.parameters,
    });
    setResult(outcome);
    pushToast(outcome.ok ? "ok" : "fail", outcome.message);
    setPending(null);
    if (outcome.ok) setEditing(null);
  };

  const openParams = (rule: RuleVO) => {
    const draft: Draft = {};
    for (const p of rule.params) draft[p.key] = p.value;
    setEditing({ rule, draft });
  };

  const saveParams = () => {
    if (!editing) return;
    const rule = editing.rule;
    const errors = validateParamEdits(rule, editing.draft);
    if (hasErrors(errors)) {
      setResult({ ok: false, message: `${rule.name}: ${flattenErrors(errors).join(" · ")}`, requestId: null });
      return;
    }
    const parameters = paramPayload(rule, editing.draft);
    setPending({ rule, enable: rule.enabled, parameters, label: `Save parameters for "${rule.name}"` });
  };

  const SORT_KEYS: Array<{ id: RuleSortKey; label: string }> = [
    { id: "name", label: "name" },
    { id: "category", label: "category" },
    { id: "status", label: "status" },
  ];

  return (
    <div className="l3-wrap">
      <div className="l3-head">
        <h1>Rules</h1>
        <span className="crumb">SAFETY &amp; GOVERNANCE</span>
        <span className="desc">Trading rule matrix — enablement + thresholds (legacy tab-rules)</span>
      </div>
      <div className="l3-note">
        Rules gate the live order path. Every change is confirmed, sent to <span className="inline-mono">/api/rules/toggle</span>,
        and the table is re-read from the backend — the UI never assumes success. The rule cache is refreshed server-side
        (rule_matrix) on accepted toggles.
      </div>

      <QuerySection<RuleVO[]>
        title="Rule matrix"
        accent
        query={query}
        skeletonRows={6}
        emptyMessage="Backend returned zero rules (trading_rules_config empty or PostgreSQL provider)."
        emptyHint="The rule store seeds with SQLite; on other providers the repository returns []. This is reported, not hidden."
        right={
          <>
            <FreshnessCaption fetchedAtMs={query.dataUpdatedAt || null} nowMs={props.nowMs} intervalMs={30_000} stale={poll.paused} />
            <PollControl paused={poll.paused} onToggle={poll.togglePaused} intervalMs={30_000} busy={query.isFetching} />
          </>
        }
      >
        {(all) => (
          <div>
            <div className="l3-rules-summary">
              <MetricCard label="rules" value={counts.total} sub={`${all.length} rows from /api/rules`} />
              <MetricCard label="enabled" value={counts.enabled} tone="pos" sub={`${counts.disabled} disabled`} />
              <MetricCard label="categories" value={cats.length} sub={cats.slice(0, 3).join(" · ") || "—"} />
              <MetricCard label="bad parameters" value={counts.broken} tone={counts.broken ? "neg" : "dim"} sub="stored JSON unparseable" />
            </div>

            <div className="l3-toolbar">
              <input
                className="input"
                style={{ minWidth: 240 }}
                placeholder="search rule / category / parameter…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                aria-label="Search rules"
              />
              <select className="select" value={category} onChange={(e) => setCategory(e.target.value)} aria-label="Filter by category">
                <option value="all">ALL CATEGORIES</option>
                {cats.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
              <span className="segmented" role="tablist">
                {STATUS_OPTIONS.map((o) => (
                  <button key={o.id} role="tab" aria-selected={status === o.id} className={status === o.id ? "active" : ""} onClick={() => setStatus(o.id)}>
                    {o.label}
                  </button>
                ))}
              </span>
              <span className="l3-poll">
                <span className="timestamp-note">sort</span>
                {SORT_KEYS.map((k) => (
                  <button
                    key={k.id}
                    className={`btn small ghost ${sort.key === k.id ? "" : "l3-unsorted"}`}
                    onClick={() => toggleSort(k.id)}
                    title={`sort by ${k.label}`}
                  >
                    {k.label}
                    {sort.key === k.id ? (sort.dir === "asc" ? " ▲" : " ▼") : ""}
                  </button>
                ))}
              </span>
              <span className="timestamp-note">
                {rows.length}/{all.length} shown
              </span>
            </div>

            {rows.length === 0 ? (
              <EmptyState message="No rules match the current filter." hint="Clear the search box or switch the category." />
            ) : (
              <DataTable
                headers={[
                  { label: "RULE" },
                  { label: "CATEGORY" },
                  { label: "STATUS" },
                  { label: "PARAMETERS / THRESHOLDS" },
                  { label: "ACTIONS" },
                ]}
              >
                {rows.map((rule) => (
                  <tr key={rule.name}>
                    <td className="inline-mono" title={rule.name}>{rule.name}</td>
                    <td>{rule.category}</td>
                    <td>
                      <StatusBadge status={rule.enabled ? "ACTIVE" : "STOPPED"} label={rule.enabled ? "enabled" : "disabled"} />
                    </td>
                    <td>
                      {rule.paramsError ? (
                        <span className="badge bad" title={rule.paramsError}>PARAMS PARSE ERROR</span>
                      ) : rule.params.length === 0 ? (
                        <span className="faint">no parameters</span>
                      ) : (
                        <div className="l3-rule-params">
                          {rule.params.map((p) => (
                            <span key={p.key} className={`l3-param-chip ${p.threshold ? "threshold" : ""}`} title={`${p.key}=${p.value}`}>
                              {p.key}=<b>{p.value}</b>
                            </span>
                          ))}
                        </div>
                      )}
                    </td>
                    <td>
                      <div className="l3-row-actions">
                        <button className="btn small" onClick={() => openParams(rule)} disabled={rule.params.length === 0}>
                          Edit…
                        </button>
                        <button
                          className={`btn small ${rule.enabled ? "danger" : "primary"}`}
                          onClick={() => setPending({ rule, enable: !rule.enabled, parameters: null, label: `${rule.enabled ? "DISABLE" : "ENABLE"} rule "${rule.name}"` })}
                          disabled={toggle.isPending}
                        >
                          {rule.enabled ? "Disable…" : "Enable…"}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </DataTable>
            )}
            <ResultStrip
              result={
                toggle.isPending
                  ? { running: true, lastResult: null, lastMessage: null }
                  : result
                    ? { running: false, lastResult: result.ok, lastMessage: result.message }
                    : null
              }
            />
            {result && (
              <div className="tiny faint" style={{ marginTop: 4 }}>
                {result.requestId ? `request_id: ${result.requestId} · ` : ""}
                table state above is the fresh backend read{result.ok ? " (refetched after the accepted change)." : " (unchanged — the command was refused)."}
              </div>
            )}
            {query.isFetching && rows.length > 0 && <Skeleton count={1} height={10} />}
          </div>
        )}
      </QuerySection>

      {editing && (
        <Panel title={`Parameters — ${editing.rule.name}`} accent right={<span className="timestamp-note">numeric parameters must be ≥ 0 · empty payload never sent</span>}>
          <div className="l3-form" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", display: "grid" }}>
            {editing.rule.params.map((p) => (
              <FieldRow key={p.key} label={p.key} hint={p.kind === "number" && p.threshold ? "threshold" : p.kind}>
                {p.kind === "number" ? (
                  <NumberField
                    value={editing.draft[p.key] ?? ""}
                    step="any"
                    onChange={(v) => setEditing({ ...editing, draft: { ...editing.draft, [p.key]: v } })}
                  />
                ) : (
                  <TextField
                    value={editing.draft[p.key] ?? ""}
                    onChange={(v) => setEditing({ ...editing, draft: { ...editing.draft, [p.key]: v } })}
                  />
                )}
              </FieldRow>
            ))}
          </div>
          <div className="l3-toolbar" style={{ marginTop: 10, justifyContent: "flex-end" }}>
            <button className="btn" onClick={() => setEditing(null)}>Cancel</button>
            <button className="btn primary" onClick={saveParams} disabled={toggle.isPending}>
              Save parameters…
            </button>
          </div>
        </Panel>
      )}

      {pending && (
        <ConfirmModal
          title={pending.label}
          danger={pending.enable === false}
          confirmLabel={pending.enable ? "Enable rule" : "Disable rule"}
          busy={toggle.isPending}
          onCancel={() => setPending(null)}
          onConfirm={() => void runCommand()}
        >
          <div className="confirm-box">
            <div className="note">
              {pending.enable
                ? "Enabling this rule re-opens a gate on the live decision path. The backend stores the change and force-refreshes the rule matrix."
                : `Disabling "${pending.rule.name}" removes its protection from every subsequent evaluation. Category: ${pending.rule.category}.`}
              {pending.parameters ? " The edited parameter set will be persisted with the enable flag." : ""}
            </div>
            <div className="row">
              <span className="inline-mono">{pending.rule.name}</span>
              <StatusBadge status={pending.enable ? "ACTIVE" : "STOPPED"} />
            </div>
          </div>
        </ConfirmModal>
      )}
    </div>
  );
}
