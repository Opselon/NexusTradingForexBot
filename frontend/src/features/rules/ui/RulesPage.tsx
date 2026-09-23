/**
 * Rules — trading rule matrix (parity: Web/app.js tab-rules, loadRules /
 * renderRuleCard / toggleRuleState / saveRuleParameters).
 *
 * Presentation only: data + commands come from ./hooks (-> useCases -> api ->
 * core transport). Deliberate differences from legacy, all preserved from the
 * previous iteration:
 *  - toggling asks for confirmation (30+ rules gate real order flow) and shows
 *    the BACKEND verdict inline, then refetches — never assumes success;
 *  - parameter edits run through the shared validation layer; an invalid
 *    payload is blocked client-side and never reaches the wire;
 *  - search + category filter + sortable headers replace the card wall.
 *
 * UI pass (this iteration): hero header, stat cards with enabled-ratio donut,
 * category chip filter, counts in the status segmented control, header-level
 * sorting with sticky category group rows, click-to-copy rule ids, search
 * highlighting, switch-style status, modal parameter editor, `/` focus hotkey.
 * All state/derive/transport logic is unchanged — this is a view-layer
 * deepening over the same model.ts / useCases.ts contracts.
 */

import { useEffect, useMemo, useRef, useState, useCallback } from "react";
import { useUiStore } from "@/stores/uiStore";
import { ConfirmModal, EmptyState, Skeleton, StatusBadge } from "@/components/primitives";
import {
  FreshnessCaption,
  PollControl,
  QuerySection,
  ResultStrip,
  usePolling,
} from "@/features/config/ui/kit";
import "@/features/config/ui/kit.css";
import { flattenErrors, hasErrors } from "@/features/config/validation";
import type { ShellPageProps } from "@/app/featureModule";
import { useRulesQuery, useToggleRule, type RuleCommandOutcome } from "../hooks";
import {
  catTone,
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
import { RulesStats } from "./RulesStats";
import { RuleTable } from "./RuleTable";
import { RuleParamDialog, type RuleDraft } from "./RuleParamDialog";
import "./rules.css";

const STATUS_OPTIONS: Array<{ id: RuleStatusFilter; label: string }> = [
  { id: "all", label: "ALL" },
  { id: "enabled", label: "ENABLED" },
  { id: "disabled", label: "DISABLED" },
];

export default function RulesPage(props: ShellPageProps) {
  const poll = usePolling(30_000);
  const query = useRulesQuery(poll.paused);
  const toggle = useToggleRule();
  const pushToast = useUiStore((s) => s.pushToast);

  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("all");
  const [status, setStatus] = useState<RuleStatusFilter>("all");
  const [sort, setSort] = useState<RuleSort>({ key: "category", dir: "asc" });
  const [pending, setPending] = useState<{
    rule: RuleVO;
    enable: boolean;
    parameters: Record<string, unknown> | null;
    label: string;
  } | null>(null);
  const [result, setResult] = useState<RuleCommandOutcome | null>(null);
  const [editing, setEditing] = useState<{ rule: RuleVO; draft: RuleDraft } | null>(null);
  const [showErrors, setShowErrors] = useState(false);
  const searchRef = useRef<HTMLInputElement | null>(null);

  // "/" focuses search (unless the operator is already typing somewhere).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      const typing = !!t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable);
      if (e.key === "/" && !typing) {
        e.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const rows = useMemo(
    () => sortRules(filterRules(query.data ?? [], { query: search, category, status }), sort),
    [query.data, search, category, status, sort],
  );

  const cats = useMemo(() => ruleCategories(query.data ?? []), [query.data]);
  const statusCounts = useMemo(() => {
    const all = query.data ?? [];
    const enabled = all.filter((r) => r.enabled).length;
    return { all: all.length, enabled, disabled: all.length - enabled };
  }, [query.data]);
  const perCategory = useMemo(() => {
    const m = new Map<string, number>();
    for (const r of query.data ?? []) m.set(r.category, (m.get(r.category) ?? 0) + 1);
    return m;
  }, [query.data]);

  // Stable identities: the memoized RuleRow/RulesStats children must not be
  // invalidated by the shell's 1s tick — these only touch state setters.
  const onSort = useCallback(
    (key: RuleSortKey) =>
      setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" })),
    [],
  );

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

  const requestToggle = useCallback(
    (rule: RuleVO) =>
      setPending({
        rule,
        enable: !rule.enabled,
        parameters: null,
        label: `${rule.enabled ? "DISABLE" : "ENABLE"} rule "${rule.name}"`,
      }),
    [],
  );

  const openParams = useCallback((rule: RuleVO) => {
    const draft: RuleDraft = {};
    for (const p of rule.params) draft[p.key] = p.value;
    setShowErrors(false);
    setEditing({ rule, draft });
  }, []);

  const saveParams = () => {
    if (!editing) return;
    const rule = editing.rule;
    const errors = validateParamEdits(rule, editing.draft);
    if (hasErrors(errors)) {
      setShowErrors(true);
      setResult({ ok: false, message: `${rule.name}: ${flattenErrors(errors).join(" · ")}`, requestId: null });
      return;
    }
    const parameters = paramPayload(rule, editing.draft);
    setPending({ rule, enable: rule.enabled, parameters, label: `Save parameters for "${rule.name}"` });
  };

  return (
    <div className="rl-page">
      <header className="rl-hero">
        <div className="rl-hero-main">
          <div className="rl-kicker">
            <span className="dot" aria-hidden="true" />
            SAFETY &amp; GOVERNANCE
          </div>
          <h1 className="rl-title">
            <span className="glyph" aria-hidden="true">§</span>
            <span className="word">Rules</span>
          </h1>
          <p className="rl-desc">
            Trading rule matrix — enablement + thresholds (legacy tab-rules). Rules gate the live
            order path: every change is confirmed, sent to{" "}
            <span className="inline-mono">/api/rules/toggle</span>, and the table is re-read from the
            backend — the UI never assumes success.
          </p>
        </div>
        <div className="rl-hero-side">
          <span
            className="rl-shield"
            title="Commands are backend-authoritative: the verdict comes from the server, then the table refetches."
          >
            <span className="d" aria-hidden="true" />
            backend-authoritative
          </span>
        </div>
      </header>

      <div className="l3-note">
        The rule cache is refreshed server-side (rule_matrix) on accepted toggles. Parameter edits
        are validated client-side and re-confirmed before they are sent.
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
            <FreshnessCaption
              fetchedAtMs={query.dataUpdatedAt || null}
              nowMs={props.nowMs}
              intervalMs={30_000}
              stale={poll.paused}
            />
            <PollControl
              paused={poll.paused}
              onToggle={poll.togglePaused}
              intervalMs={30_000}
              busy={query.isFetching}
            />
          </>
        }
      >
        {(all) => (
          <div>
            <RulesStats all={all} cats={cats} />

            <div className="rl-cats" role="group" aria-label="Filter by category">
              <button
                className={`rl-cat-chip ${category === "all" ? "on" : ""} rl-tone-0`}
                aria-pressed={category === "all"}
                onClick={() => setCategory("all")}
              >
                <span className="lbl">all categories</span>
                <span className="cnt">{all.length}</span>
              </button>
              {cats.map((c) => (
                <button
                  key={c}
                  className={`rl-cat-chip ${category === c ? "on" : ""} rl-tone-${catTone(c)}`}
                  aria-pressed={category === c}
                  title={c}
                  onClick={() => setCategory(c)}
                >
                  <span className="lbl">{c}</span>
                  <span className="cnt">{perCategory.get(c) ?? 0}</span>
                </button>
              ))}
            </div>

            <div className="rl-toolbar">
              <span className="rl-search">
                <svg
                  className="mag"
                  width="13"
                  height="13"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.4"
                  strokeLinecap="round"
                  aria-hidden="true"
                >
                  <circle cx="11" cy="11" r="7" />
                  <path d="M21 21l-4.3-4.3" />
                </svg>
                <input
                  ref={searchRef}
                  id="rl-rules-search"
                  name="rl-rules-search"
                  className="input"
                  placeholder="search rule / category / parameter…"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  aria-label="Search rules"
                />
                <kbd aria-hidden="true">/</kbd>
              </span>

              <span className="rl-seg" role="tablist" aria-label="Filter by status">
                {STATUS_OPTIONS.map((o) => (
                  <button
                    key={o.id}
                    role="tab"
                    aria-selected={status === o.id}
                    className={`f-${o.id} ${status === o.id ? "on" : ""}`}
                    onClick={() => setStatus(o.id)}
                  >
                    {o.label}
                    <span className="cnt">{statusCounts[o.id]}</span>
                  </button>
                ))}
              </span>

              <span className="rl-shown timestamp-note">
                {rows.length}/{all.length} shown · sort: {sort.key} {sort.dir === "asc" ? "▲" : "▼"}
              </span>
            </div>

            {rows.length === 0 ? (
              <EmptyState
                message="No rules match the current filter."
                hint="Clear the search box or switch the category."
              />
            ) : (
              <RuleTable
                rows={rows}
                grouped={sort.key === "category"}
                query={search}
                sort={sort}
                onSort={onSort}
                onEdit={openParams}
                onToggleRequest={requestToggle}
                busy={toggle.isPending}
              />
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
              <div className="rl-under">
                {result.requestId ? `request_id: ${result.requestId} · ` : ""}
                table state above is the fresh backend read
                {result.ok
                  ? " (refetched after the accepted change)."
                  : " (unchanged — the command was refused)."}
              </div>
            )}
            {query.isFetching && rows.length > 0 && <Skeleton count={1} height={10} />}
          </div>
        )}
      </QuerySection>

      {editing && (
        <RuleParamDialog
          rule={editing.rule}
          draft={editing.draft}
          busy={toggle.isPending}
          locked={pending !== null}
          showErrors={showErrors}
          onChange={(key, value) =>
            setEditing((e) => (e ? { ...e, draft: { ...e.draft, [key]: value } } : e))
          }
          onClose={() => setEditing(null)}
          onSave={saveParams}
        />
      )}

      {pending && (
        <ConfirmModal
          title={pending.label}
          danger={!pending.parameters && pending.enable === false}
          confirmLabel={
            pending.parameters
              ? "Save parameters"
              : pending.enable
                ? "Enable rule"
                : "Disable rule"
          }
          busy={toggle.isPending}
          onCancel={() => setPending(null)}
          onConfirm={() => void runCommand()}
        >
          <div className="confirm-box">
            <div className="note">
              {pending.parameters
                ? "The edited parameter set is persisted with the enable flag. The backend confirms, then the table re-reads."
                : pending.enable
                  ? "Enabling this rule re-opens a gate on the live decision path. The backend stores the change and force-refreshes the rule matrix."
                  : `Disabling "${pending.rule.name}" removes its protection from every subsequent evaluation. Category: ${pending.rule.category}.`}
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
