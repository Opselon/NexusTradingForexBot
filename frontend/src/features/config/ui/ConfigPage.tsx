/**
 * Settings — engine mode, runtime configuration (validate-before-apply),
 * model hot-swap, settings provenance editor, Telegram (masked secrets).
 *
 * Parity: Web/app.js loadConfiguration/saveConfiguration + the telegram
 * handlers (BUG-072/080 mask discipline) + runtime-config apply flow.
 *
 * HARD RULES enforced here:
 *  - every mutation follows validate(client) -> validate(server, per key) ->
 *    apply -> report backend verdict verbatim -> refetch. An invalid payload
 *    never reaches the wire;
 *  - the masked bot_token served by the backend is treated as "unchanged",
 *    never resubmitted as a credential (BUG-080 path);
 *  - engine-mode changes to LIVE need a typed confirmation.
 */

import { useMemo, useState } from "react";
import { Panel, Skeleton, StatusBadge } from "@/components/primitives";
import { useUiStore } from "@/stores/uiStore";
import { useI18n } from "@/stores/i18nStore";
import {
  CheckField,
  FieldRow,
  FreshnessCaption,
  KeyValueList,
  MonoValue,
  NumberField,
  PollControl,
  QuerySection,
  ResultStrip,
  SelectField,
  TextField,
  TypedConfirmModal,
  usePolling,
} from "@/features/config/ui/kit";
import "@/features/config/ui/kit.css";
import {
  BOT_TOKEN_PATTERN,
  ADMIN_ID_PATTERN,
  DEFAULT_RULES,
  buildPayload,
  changedKeys,
  firstError,
  flattenErrors,
  hasErrors,
  isMaskedValue,
  validateFields,
  type FieldErrors,
  type FieldValue,
  type FieldValues,
} from "@/features/config/validation";
import type { ShellPageProps } from "@/app/featureModule";
import {
  useConfigFormQuery,
  useModelSwap,
  useRuntimeDiagnosticsQuery,
  useRuntimeEffectiveQuery,
  useRuntimeModeQuery,
  useSaveTelegram,
  useSetEngineMode,
  useSettingsSnapshotQuery,
  useTelegramStatusQuery,
  useTestTelegram,
  useApplyRuntimeConfig,
  type CommandOutcome,
} from "../useCases";
import {
  allowedModes,
  checkModeTransition,
  configBaseline,
  runtimeConfigSpecs,
  specSections,
  type SpecWithMutability,
} from "../model";

function outcomeLine(o: CommandOutcome | null): { running: boolean; lastResult: boolean | null; lastMessage: string | null } | null {
  if (!o) return null;
  return { running: false, lastResult: o.ok, lastMessage: o.requestId ? `${o.message} · request_id: ${o.requestId}` : o.message };
}

/* ------------------------------------------------------------------ */
/* Engine mode card                                                    */
/* ------------------------------------------------------------------ */

function EngineModeCard() {
  const t = useI18n((s) => s.t);
  const poll = usePolling(15_000);
  const modeQuery = useRuntimeModeQuery(poll.paused);
  const setMode = useSetEngineMode(t);
  const pushToast = useUiStore((s) => s.pushToast);
  const [target, setTarget] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [result, setResult] = useState<CommandOutcome | null>(null);

  const current = modeQuery.data?.mode ?? null;
  const check = target ? checkModeTransition(current, target, t) : null;

  const requestSwitch = () => {
    if (!target || !check?.ok) return;
    setConfirmOpen(true);
  };

  const confirmSwitch = async () => {
    const outcome = await setMode.mutateAsync(target);
    setResult(outcome);
    pushToast(outcome.ok ? "ok" : "fail", outcome.message);
    if (outcome.ok) {
      setConfirmOpen(false);
      setTarget("");
    }
  };

  const badgeClass = (m: string | null) =>
    m === "LIVE" ? "live" : m === "PAPER" ? "paper" : m === "SHADOW" ? "shadow" : "other";

  return (
    <Panel
      title={t("config.engine.title", "Engine execution mode")}
      accent
      right={
        <>
          <FreshnessCaption fetchedAtMs={modeQuery.dataUpdatedAt || null} intervalMs={15_000} stale={poll.paused} />
          <PollControl paused={poll.paused} onToggle={poll.togglePaused} intervalMs={15_000} busy={modeQuery.isFetching} />
        </>
      }
    >
      {modeQuery.isPending ? (
        <Skeleton count={2} />
      ) : modeQuery.isError ? (
        <div className="l3-note bad">
          {modeQuery.error instanceof Error ? modeQuery.error.message : t("config.engine.mode_unreadable", "runtime/mode unreadable")} —{" "}
          {t("config.engine.mode_unknown_note", "the current mode is UNKNOWN, never guessed.")}{" "}
          <button className="btn small" onClick={() => void modeQuery.refetch()}>
            {t("common.retry", "Retry")}
          </button>
        </div>
      ) : (
        <div className="l3-mode-card">
          <div>
            <div className="timestamp-note">{t("config.engine.configured", "configured")}</div>
            <div className={`l3-mode-badge ${badgeClass(current)}`}>{current ?? t("config.engine.offline", "ENGINE OFFLINE")}</div>
          </div>
          <div>
            <div className="timestamp-note">{t("config.engine.effective", "effective (runtime)")}</div>
            <div className="inline-mono">{modeQuery.data?.effective_mode ?? "—"}</div>
          </div>
          <div>
            <div className="timestamp-note">{t("config.engine.attached", "engine attached")}</div>
            <StatusBadge status={modeQuery.data?.engine_attached ? "CONNECTED" : "DISCONNECTED"} />
          </div>
          <div className="l3-field">
            <div className="l3-field-head">
              <span className="lab">{t("config.engine.switch_mode", "switch mode")}</span>
            </div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
              <SelectField
                value={target}
                onChange={setTarget}
                options={allowedModes(current).includes(target) || target === "" ? ["", ...allowedModes(current)] : allowedModes(current)}
                label={t("config.engine.target_mode", "target execution mode")}
              />
              <button className="btn danger" disabled={!target || target === current || setMode.isPending} onClick={requestSwitch}>
                {t("config.engine.switch_btn", "Switch…")}
              </button>
            </div>
            {check && check.errors.length > 0 && (
              <div className="l3-field-error" role="alert">{check.errors.join(" · ")}</div>
            )}
            {check && check.warnings.length > 0 && (
              <div className="l3-field-hint">⚠ {check.warnings.join(" · ")}</div>
            )}
            {target === "LIVE" && check?.ok && (
              <div className="l3-note bad" style={{ marginTop: 4 }}>
                {t("config.engine.live_note", "LIVE = real capital at risk through the broker adapter. The backend re-checks everything (BUG-148 path) — a 200 alone is never treated as success; the response payload is.")}
              </div>
            )}
          </div>
          <ResultStrip result={setMode.isPending ? { running: true, lastResult: null, lastMessage: null } : outcomeLine(result)} />
        </div>
      )}
      {confirmOpen && target && (
        <TypedConfirmModal
          title={t("config.engine.confirm_title", "Switch execution mode → {target}", { target })}
          word={target}
          confirmLabel={t("config.engine.confirm_ok", "Switch mode")}
          busy={setMode.isPending}
          onCancel={() => setConfirmOpen(false)}
          onConfirm={() => void confirmSwitch()}
          body={
            <>
              {t("config.engine.confirm_body", "Current mode {from} → {to}. The engine hot-swaps the adapter boundary, persists to the settings DB and the versioned runtime store (source=WEB_UI).", {
                from: current ?? t("config.engine.unknown", "UNKNOWN"),
                to: target,
              })}{" "}
              {target === "LIVE" ? t("config.engine.confirm_live", "Orders will be placed with the real broker.") : ""}
            </>
          }
        />
      )}
    </Panel>
  );
}

/* ------------------------------------------------------------------ */
/* Runtime configuration form (validate-before-apply showpiece)        */
/* ------------------------------------------------------------------ */

const STR = (v: unknown): string => (v === null || v === undefined ? "" : String(v));

function RuntimeConfigForm() {
  const t = useI18n((s) => s.t);
  const poll = usePolling(60_000);
  const cfgQuery = useConfigFormQuery(poll.paused);
  const diagQuery = useRuntimeDiagnosticsQuery(poll.paused);
  const effectiveQuery = useRuntimeEffectiveQuery(poll.paused);
  const apply = useApplyRuntimeConfig();
  const pushToast = useUiStore((s) => s.pushToast);

  const [draft, setDraft] = useState<FieldValues | null>(null);
  const [result, setResult] = useState<CommandOutcome | null>(null);
  const [serverFieldErrors, setServerFieldErrors] = useState<FieldErrors>({});
  const [step, setStep] = useState<null | "client" | "server" | "applied">(null);

  const specs = useMemo(() => runtimeConfigSpecs(t), [t]);
  const baseline = useMemo(() => (cfgQuery.data ? configBaseline(cfgQuery.data) : null), [cfgQuery.data]);
  const values = draft ?? baseline ?? {};
  const errors = validateFields(specs, values, DEFAULT_RULES, t);
  const dirtyKeys = baseline ? changedKeys(baseline, values) : [];

  const setValue = (key: string, v: FieldValue) => {
    setValues((prev) => ({ ...prev, [key]: v }));
  };
  const setValues = (fn: (prev: FieldValues) => FieldValues) =>
    setDraft((prev) => fn(prev ?? values));

  const applyChanges = async () => {
    if (!baseline) return;
    setStep("client");
    const changes = buildPayload(specs, baseline, values);
    if (Object.keys(changes).length === 0) {
      setResult({ ok: false, message: t("config.apply.nothing_changed", "Nothing changed against the server baseline — payload would be empty."), requestId: null });
      setStep(null);
      return;
    }
    setServerFieldErrors({});
    const steps = await apply.mutateAsync(changes);
    setResult(steps.outcome);
    setStep(steps.sent ? "applied" : steps.clientErrors && hasErrors(steps.clientErrors) ? "client" : "server");
    if (steps.serverErrors && hasErrors(steps.serverErrors)) setServerFieldErrors(steps.serverErrors);
    const allErrors = { ...(steps.clientErrors ?? {}), ...(steps.serverErrors ?? {}) };
    pushToast(steps.outcome?.ok ? "ok" : "fail", steps.outcome?.message ?? t("config.apply.refused_toast", "apply refused"));
    if (steps.outcome?.ok) {
      setDraft(null);
    } else if (hasErrors(allErrors)) {
      setResult({ ok: false, message: `${steps.outcome?.message ?? t("config.apply.refused", "Refused.")} → ${flattenErrors(allErrors).join(" · ")}`, requestId: steps.outcome?.requestId ?? null });
    }
  };

  const sectionTitle = (key: string) =>
    key === "execution"
      ? t("config.section.execution", "execution")
      : key === "risk"
        ? t("config.section.risk", "risk")
        : key === "model"
          ? t("config.section.model", "model")
          : key;

  const renderSpec = (spec: SpecWithMutability) => {
    const v = values[spec.key];
    const err = firstError(errors, spec.key) ?? firstError(serverFieldErrors, spec.key);
    const dirty = dirtyKeys.includes(spec.key);
    return (
      <FieldRow key={spec.key} label={spec.label ?? spec.key} hint={spec.key} error={err} dirty={dirty} mutability={spec.mutability}>
        {spec.kind === "boolean" ? (
          <CheckField checked={v === true || v === "true"} onChange={(b) => setValue(spec.key, b)} label={spec.key} />
        ) : spec.kind === "enum" ? (
          <SelectField value={STR(v)} onChange={(s) => setValue(spec.key, s)} options={spec.options ?? []} error={err} label={spec.key} />
        ) : spec.kind === "number" || spec.kind === "integer" ? (
          <NumberField value={STR(v)} onChange={(s) => setValue(spec.key, s)} error={err} step={spec.kind === "integer" ? "1" : "any"} />
        ) : (
          <TextField value={STR(v)} onChange={(s) => setValue(spec.key, s)} error={err} />
        )}
      </FieldRow>
    );
  };

  return (
    <QuerySection<NonNullable<typeof cfgQuery.data>>
      title={t("config.apply.title", "Runtime configuration (execution · risk · model)")}
      accent
      query={cfgQuery}
      skeletonRows={6}
      emptyMessage={t("config.apply.empty", "Backend returned no configuration.")}
      right={
        <>
          <FreshnessCaption
            fetchedAtMs={cfgQuery.dataUpdatedAt || null}
            intervalMs={60_000}
            note={cfgQuery.data?.runtime_applied ? t("config.apply.note_live", "live store") : t("config.apply.note_fallback", "live.yaml fallback (engine offline)")}
            stale={!cfgQuery.data?.runtime_applied}
          />
          <PollControl paused={poll.paused} onToggle={poll.togglePaused} intervalMs={60_000} busy={cfgQuery.isFetching} />
        </>
      }
    >
      {(cfg) => (
        <div>
          {!cfg.runtime_applied && (
            <div className="l3-note warn" style={{ marginBottom: 10 }}>
              {t("config.apply.engine_offline", "ENGINE OFFLINE — values below come from the live.yaml bootstrap fallback (diagnostic only). Applies will be refused or persisted-only; the backend decides.")}
            </div>
          )}
          {cfg.telegram?.bot_token && isMaskedValue(cfg.telegram.bot_token) && (
            <div className="l3-note" style={{ marginBottom: 10 }}>
              {t("config.apply.masked_before", "Telegram token arrives masked")} (
              <span className="inline-mono">{cfg.telegram.bot_token}</span>){" "}
              {t("config.apply.masked_after", "— manage it in the Telegram panel; the form never re-submits a mask (BUG-080).")}
            </div>
          )}
          {specSections(specs).map((section) => (
            <div key={section} style={{ marginBottom: 12 }}>
              <div className="section-title">{sectionTitle(section)}</div>
              <div className="l3-form" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 10 }}>
                {specs.filter((s) => s.section === section).map(renderSpec)}
              </div>
            </div>
          ))}
          <div className="l3-toolbar" style={{ justifyContent: "flex-end" }}>
            {dirtyKeys.length > 0 && (
              <button className="btn ghost" onClick={() => { setDraft(null); setServerFieldErrors({}); }}>
                {dirtyKeys.length === 1
                  ? t("config.apply.revert_one", "Revert 1 edit")
                  : t("config.apply.revert_many", "Revert {n} edits", { n: dirtyKeys.length })}
              </button>
            )}
            <span className="timestamp-note">
              {t("config.apply.pipeline", "validate({v}) → apply({a}) → report → refetch", { v: "/api/settings/validate", a: "/api/runtime-config/apply" })}
            </span>
            <button
              className="btn primary"
              disabled={dirtyKeys.length === 0 || hasErrors(errors) || apply.isPending}
              title={
                hasErrors(errors)
                  ? t("config.apply.title_fix", "fix the highlighted fields first — an invalid payload is never sent")
                  : t("config.apply.title_batch", "validate each key server-side, then apply the batch")
              }
              onClick={() => void applyChanges()}
            >
              {apply.isPending
                ? step === "applied"
                  ? t("config.apply.applying", "applying…")
                  : t("config.apply.validating", "validating…")
                : t("config.apply.validate_apply", "Validate & apply ({n})", { n: dirtyKeys.length })}
            </button>
          </div>
          {hasErrors(errors) && dirtyKeys.length > 0 && (
            <div className="l3-note bad">
              {t("config.apply.client_blocked", "CLIENT VALIDATION BLOCKED SUBMISSION: {errors}", { errors: flattenErrors(errors).join(" · ") })}
            </div>
          )}
          <ResultStrip result={apply.isPending ? { running: true, lastResult: null, lastMessage: null } : outcomeLine(result)} />

          <div style={{ marginTop: 12 }}>
            <div className="section-title">{t("config.diag.title", "Version truth (runtime-config diagnostics)")}</div>
            {diagQuery.isPending ? (
              <Skeleton count={2} height={12} />
            ) : diagQuery.data ? (
              <div className="l3-runtime-ver">
                <span>{t("config.diag.persistent", "persistent v{v}", { v: String(diagQuery.data.persistent_version ?? "—") })}</span>
                <span>{t("config.diag.runtime", "runtime v{v}", { v: String(diagQuery.data.runtime_version ?? "—") })}</span>
                <span className={diagQuery.data.mismatch ? "mismatch" : ""}>
                  {diagQuery.data.mismatch ? t("config.diag.mismatch", "⚠ VERSION MISMATCH") : t("config.diag.match", "versions match")}
                </span>
                <span>{t("config.diag.last_apply", "last apply: {status}", { status: diagQuery.data.last_apply_status })}</span>
                {diagQuery.data.last_apply_error && (
                  <span className="mismatch">{t("config.diag.error", "error: {msg}", { msg: diagQuery.data.last_apply_error })}</span>
                )}
                <span>
                  {diagQuery.data.live_yaml_exists
                    ? t("config.diag.live_yaml_yes", "live.yaml: yes ({hash})", { hash: `${diagQuery.data.live_yaml_hash.slice(0, 12)}…` })
                    : t("config.diag.live_yaml_absent", "live.yaml: absent")}
                </span>
              </div>
            ) : (
              <div className="l3-note bad">{t("config.diag.unavailable", "diagnostics unavailable")}</div>
            )}
            {effectiveQuery.data && (
              <div className="tiny faint" style={{ marginTop: 4 }}>
                {t("config.diag.effective", "effective snapshot: v{v} · source {src} · updated {at}", {
                  v: String(effectiveQuery.data.configuration_version ?? "—"),
                  src: String(effectiveQuery.data.source ?? "—"),
                  at: String(effectiveQuery.data.updated_at ?? "—"),
                })}
              </div>
            )}
          </div>
        </div>
      )}
    </QuerySection>
  );
}

/* ------------------------------------------------------------------ */
/* Model hot-swap                                                      */
/* ------------------------------------------------------------------ */

function ModelSwapCard() {
  const t = useI18n((s) => s.t);
  const swap = useModelSwap(t);
  const pushToast = useUiStore((s) => s.pushToast);
  const [path, setPath] = useState("");
  const spec = {
    key: "model_artifact_path",
    label: t("config.field.artifact_path_short", "artifact path"),
    kind: "path" as const,
    required: true,
    pattern: "\\.(pt|onnx|joblib|pkl)$",
    patternMessage: t("config.field.artifact_pattern", "artifact must be a .pt / .onnx / .joblib / .pkl file"),
  };
  const errors = validateFields([spec], { model_artifact_path: path }, DEFAULT_RULES, t);
  const err = firstError(errors, spec.key);

  const run = async () => {
    if (hasErrors(errors) || path.trim() === "") return;
    const outcome = await swap.mutateAsync(path.trim());
    pushToast(outcome.ok ? "ok" : "fail", outcome.message);
  };

  return (
    <Panel
      title={t("config.swap.title", "Model artifact hot-swap")}
      right={<span className="timestamp-note">{t("config.swap.pipeline", "load → validate → warm → atomic swap")}</span>}
    >
      <div className="l3-form">
        <FieldRow label={spec.label} hint={spec.key} error={path === "" ? null : err}>
          <TextField value={path} onChange={setPath} error={err} placeholder="artifacts/model.pt" />
        </FieldRow>
      </div>
      <div className="l3-toolbar" style={{ justifyContent: "flex-end" }}>
        <button className="btn primary" disabled={path.trim() === "" || hasErrors(errors) || swap.isPending} onClick={() => void run()}>
          {swap.isPending ? t("config.swap.swapping", "swapping…") : t("config.swap.action", "Swap model…")}
        </button>
      </div>
      {hasErrors(errors) && path !== "" && <div className="l3-note bad">{flattenErrors(errors).join(" · ")}</div>}
      <ResultStrip result={swap.isPending ? { running: true, lastResult: null, lastMessage: null } : outcomeLine(swap.data ?? null)} />
      <div className="tiny faint" style={{ marginTop: 6 }}>
        {t("config.swap.note", "A healthy serving model is never replaced before the new artifact loads and warms — the engine’s verdict is shown above verbatim.")}
      </div>
    </Panel>
  );
}

/* ------------------------------------------------------------------ */
/* Settings provenance editor (/api/settings)                          */
/* ------------------------------------------------------------------ */

function SettingsProvenance() {
  const t = useI18n((s) => s.t);
  const poll = usePolling(60_000);
  const query = useSettingsSnapshotQuery(poll.paused);
  const [open, setOpen] = useState(false);

  return (
    <Panel
      title={t("config.prov.title", "Settings provenance (application_settings)")}
      right={
        <>
          <FreshnessCaption fetchedAtMs={query.dataUpdatedAt || null} intervalMs={60_000} stale={poll.paused} />
          <button className="btn small ghost" onClick={() => setOpen((o) => !o)}>
            {open ? t("config.prov.hide_table", "hide table") : t("config.prov.show_table", "show table")}
          </button>
        </>
      }
    >
      {query.isPending ? (
        <Skeleton count={3} />
      ) : query.isError ? (
        <div className="l3-note bad">{query.error instanceof Error ? query.error.message : t("config.prov.unreadable", "settings unreadable")}</div>
      ) : (
        <>
          <div className="l3-runtime-ver">
            <span>
              {t("config.prov.state", "state:")} <b>{query.data?.state ?? "—"}</b>
            </span>
            <span>
              {t("config.prov.db", "db:")} <MonoValue value={query.data?.db_path} />
            </span>
            <span>{t("config.prov.tracked", "{n} tracked keys", { n: Object.keys(query.data?.settings ?? {}).length })}</span>
          </div>
          {open && query.data && (
            <div className="l3-scroll sm" style={{ marginTop: 8 }}>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>{t("config.prov.th_key", "KEY")}</th>
                    <th>{t("config.prov.th_value", "VALUE")}</th>
                    <th>{t("config.prov.th_source", "SOURCE")}</th>
                    <th>{t("config.prov.th_ver", "VER")}</th>
                    <th>{t("config.prov.th_mutability", "MUTABILITY")}</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(query.data.settings)
                    .filter(([k]) => k !== "telegram")
                    .map(([key, row]) => {
                      const r = (row ?? {}) as Record<string, unknown>;
                      const mut = String(r.mutability ?? "");
                      return (
                        <tr key={key}>
                          <td className="inline-mono">{key}</td>
                          <td className="cell l3-cell" title={STR(r.value)}>
                            {mut.toUpperCase() === "SECRET" ? <span className="l3-mask">••••••••</span> : <MonoValue value={r.value} />}
                          </td>
                          <td>{STR(r.source) || "—"}</td>
                          <td className="num">{STR(r.version) || "—"}</td>
                          <td><StatusBadge status={mut || "UNKNOWN"} /></td>
                        </tr>
                      );
                    })}
                </tbody>
              </table>
            </div>
          )}
          <div className="tiny faint" style={{ marginTop: 6 }}>
            {t("config.prov.note", "Read-only provenance (\"which value is active, where did it come from\"). Writes go through the runtime-config gate above or the Telegram panel — never by editing this table; secret rows are masked by the backend itself.")}
          </div>
          <div className="section-title" style={{ marginTop: 10 }}>
            {t("config.prov.recent_audit", "Recent settings audit")}
          </div>
          <KeyValueList
            rows={(query.data?.recent_audit ?? []).slice(0, 8).map((row, i) => [
              `${STR(row.key) || STR((row as Record<string, unknown>).setting_key) || t("config.prov.event", "event")} · ${STR(row.timestamp) || `#${i}`}`,
              <span key={i} className="small">{STR(row.actor) || "—"} {STR(row.old_value) ? `: ${STR(row.old_value)} → ${STR(row.new_value)}` : STR(row.new_value) ? `→ ${STR(row.new_value)}` : ""}</span>,
            ])}
          />
        </>
      )}
    </Panel>
  );
}

/* ------------------------------------------------------------------ */
/* Telegram section (masked token, status, save, test)                 */
/* ------------------------------------------------------------------ */

function TelegramPanel() {
  const t = useI18n((s) => s.t);
  const poll = usePolling(30_000);
  const status = useTelegramStatusQuery(poll.paused);
  const save = useSaveTelegram(t);
  const test = useTestTelegram(t);
  const pushToast = useUiStore((s) => s.pushToast);

  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [token, setToken] = useState("");
  const [admin, setAdmin] = useState("");
  const [saveResult, setSaveResult] = useState<CommandOutcome | null>(null);
  const [testResult, setTestResult] = useState<CommandOutcome | null>(null);

  const st = status.data;
  const effectiveEnabled = enabled ?? st?.enabled ?? false;
  const specs = useMemo(
    () => [
      { key: "bot_token", label: t("config.field.bot_token", "bot token"), kind: "token" as const, secret: true, pattern: BOT_TOKEN_PATTERN, patternMessage: t("config.field.token_hint", "expected \\d+:\\w{20,} (BotFather shape)") },
      { key: "admin_id", label: t("config.field.admin_id", "admin chat id"), kind: "regex" as const, pattern: ADMIN_ID_PATTERN, patternMessage: t("config.field.admin_hint", "expected a numeric chat id (-?\\d{4,})") },
    ],
    [t],
  );
  const errors = validateFields(specs, { bot_token: token, admin_id: admin }, DEFAULT_RULES, t);
  const dirty = token !== "" || admin !== "" || (enabled !== null && enabled !== (st?.enabled ?? false));

  const runSave = async () => {
    if (hasErrors(errors)) return;
    const outcome = await save.mutateAsync({ enabled: effectiveEnabled, bot_token: token.trim(), admin_id: admin.trim() });
    setSaveResult(outcome);
    pushToast(outcome.ok ? "ok" : "fail", outcome.message);
    if (outcome.ok) {
      setToken("");
      setAdmin("");
      setEnabled(null);
    }
  };

  const runTest = async () => {
    const outcome = await test.mutateAsync();
    setTestResult(outcome);
    pushToast(outcome.ok ? "ok" : "fail", outcome.message);
  };

  return (
    <Panel
      title={t("config.tg.title", "Telegram alerts")}
      right={
        <>
          <FreshnessCaption fetchedAtMs={status.dataUpdatedAt || null} intervalMs={30_000} stale={poll.paused} />
          <PollControl paused={poll.paused} onToggle={poll.togglePaused} intervalMs={30_000} busy={status.isFetching} />
        </>
      }
    >
      {status.isPending ? (
        <Skeleton count={3} />
      ) : status.isError ? (
        <div className="l3-note bad">{status.error instanceof Error ? status.error.message : t("config.tg.unreadable", "telegram status unreadable")}</div>
      ) : (
        <div className="l3-form">
          <div className="l3-runtime-ver" style={{ marginBottom: 6 }}>
            <span>
              {t("config.tg.token", "token:")} <StatusBadge status={st?.token_status ?? "UNKNOWN"} />
            </span>
            <span className="l3-mask">{st?.masked_token || t("config.tg.no_mask", "no mask served")}</span>
            <span>
              {t("config.tg.configured", "configured:")} {st?.configured ? t("config.tg.yes", "yes") : t("config.tg.no", "no")}
            </span>
            <span>
              {t("config.tg.admin_shape", "admin shape:")}{" "}
              {st?.admin_id_shape_valid ? t("config.tg.valid", "valid") : t("config.tg.invalid", "invalid/missing")}
            </span>
            <span>
              {t("config.tg.source", "source:")} {st?.source ?? "—"}
            </span>
          </div>
          <FieldRow label={t("config.field.enabled", "enabled")} hint="telegram.enabled (HOT_RESTRICTED)">
            <CheckField checked={effectiveEnabled} onChange={setEnabled} label={t("config.tg.enabled_label", "telegram enabled")} />
          </FieldRow>
          <FieldRow label={specs[0]!.label!} hint={t("config.tg.token_hint_text", "leave empty to keep the stored secret — it never round-trips in plaintext (BUG-072)")} error={firstError(errors, "bot_token")}>
            <TextField value={token} onChange={setToken} error={firstError(errors, "bot_token")} placeholder={st?.masked_token || "123456:ABC-DEF…"} />
          </FieldRow>
          <FieldRow label={specs[1]!.label!} hint={t("config.tg.admin_hint_text", "numeric chat id of the operator")} error={firstError(errors, "admin_id")}>
            <TextField value={admin} onChange={setAdmin} error={firstError(errors, "admin_id")} placeholder="-1001234567890" />
          </FieldRow>
          <div className="l3-toolbar" style={{ justifyContent: "flex-end" }}>
            <button className="btn" disabled={!dirty || hasErrors(errors) || save.isPending} onClick={() => void runSave()}>
              {save.isPending ? t("config.tg.saving", "saving…") : t("config.tg.save_action", "Save telegram settings…")}
            </button>
            <button
              className="btn primary"
              disabled={test.isPending || !st?.configured}
              title={st?.configured ? t("config.tg.test_hint", "sends a real test message through the notifier") : t("config.tg.test_first", "configure token + admin first")}
              onClick={() => void runTest()}
            >
              {test.isPending ? t("config.kit.sending", "sending…") : t("config.tg.test_action", "Send test message")}
            </button>
          </div>
          {hasErrors(errors) && <div className="l3-note bad">{flattenErrors(errors).join(" · ")}</div>}
          <ResultStrip result={save.isPending ? { running: true, lastResult: null, lastMessage: null } : outcomeLine(saveResult)} />
          <ResultStrip result={test.isPending ? { running: true, lastResult: null, lastMessage: null } : outcomeLine(testResult)} />
          {st?.worker && (
            <div className="tiny faint" style={{ marginTop: 6 }}>
              {t("config.tg.worker", "notifier worker:")} {Object.entries(st.worker).slice(0, 6).map(([k, v]) => `${k}=${String(v)}`).join(" · ")}
            </div>
          )}
        </div>
      )}
    </Panel>
  );
}

/* ------------------------------------------------------------------ */
/* Page                                                                */
/* ------------------------------------------------------------------ */

export default function ConfigPage(props: ShellPageProps) {
  void props;
  const t = useI18n((s) => s.t);
  return (
    <div className="l3-wrap">
      <div className="l3-head">
        <h1>{t("config.head.title", "Settings")}</h1>
        <span className="crumb">{t("config.head.crumb", "PLATFORM")}</span>
        <span className="desc">{t("config.head.desc", "Engine mode · runtime configuration · model swap · provenance · Telegram (legacy tab-config)")}</span>
      </div>
      <div className="l3-note">
        {t(
          "config.head.intro_before",
          "Validate-before-apply is enforced on every write: client rules (type/range/enum/shape) block an invalid payload locally, then",
        )}{" "}
        <span className="inline-mono">/api/settings/validate</span>{" "}
        {t("config.head.intro_mid", "answers per-key mutability server-side, and only a fully-valid batch reaches")}{" "}
        <span className="inline-mono">/api/runtime-config/apply</span>.
        {t("config.head.intro_after", " The engine’s report — not the HTTP status — decides the verdict shown here.")}
      </div>
      <div className="l3-split">
        <div>
          <RuntimeConfigForm />
          <SettingsProvenance />
        </div>
        <div>
          <EngineModeCard />
          <ModelSwapCard />
          <TelegramPanel />
        </div>
      </div>
    </div>
  );
}
