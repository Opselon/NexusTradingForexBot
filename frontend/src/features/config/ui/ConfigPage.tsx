/**
 * Settings — engine mode, runtime configuration (validate-before-apply),
 * model hot-swap, settings provenance editor, Telegram (masked secrets).
 *
 * Parity: Web/app.js loadConfiguration/saveConfiguration + the telegram
 * handlers (BUG-072/080 mask discipline) + runtime-config apply flow.
 *
 * HARD RULES enforced here:
 *  - every mutation follows validate(client) -> validate(server, per key,
 *    WITH the proposed value) -> apply -> report backend verdict verbatim ->
 *    refetch. An invalid payload never reaches the wire; RESTART_REQUIRED keys
 *    are excluded from the hot-apply payload and named in the report;
 *  - the masked bot_token served by the backend is treated as "unchanged",
 *    never resubmitted as a credential (BUG-080 path);
 *  - engine-mode changes to LIVE need a typed confirmation, and the confirm
 *    modal shows the SERVER's preview (/api/v1/runtime/mode/preview) — the
 *    local matrix in model.ts is only a fast pre-filter.
 *
 * TASK-CFGUI-001: hero status strip, mode rail with server preview, restart
 * chip reporting, and the settings.css visual layer (tokens only).
 */

import { useMemo, useState, type ReactNode } from "react";
import { Panel, Skeleton, StatusBadge } from "@/components/primitives";
import { useI18n } from "@/stores/i18nStore";
import { modeLabel } from "@/features/control-center/ui/tones";
import { useUiStore } from "@/stores/uiStore";
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
import "@/features/config/ui/settings.css";
import {
  BOT_TOKEN_PATTERN,
  ADMIN_ID_PATTERN,
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
  useApplyRuntimeConfig,
  useConfigFormQuery,
  useModePreview,
  useModelSwap,
  useRuntimeDiagnosticsQuery,
  useRuntimeEffectiveQuery,
  useRuntimeModeQuery,
  useSaveTelegram,
  useSetEngineMode,
  useSettingsSnapshotQuery,
  useTelegramStatusQuery,
  useTestTelegram,
  type ApplySteps,
  type CommandOutcome,
} from "../useCases";
import {
  EXECUTION_MODES,
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

const modeTone = (m: string | null | undefined): "good" | "warn" | "bad" | "neutral" =>
  m === "LIVE" ? "bad" : m === "PAPER" ? "good" : m === "SHADOW" ? "warn" : "neutral";

/* ------------------------------------------------------------------ */
/* Hero status strip — live tiles above both columns                   */
/* ------------------------------------------------------------------ */

function Stat({ k, v, m, tone }: { k: string; v: ReactNode; m?: ReactNode; tone?: "good" | "warn" | "bad" | "neutral" }) {
  return (
    <div className={`cfg-stat ${tone && tone !== "neutral" ? tone : ""}`} role="listitem">
      <div className="k">{k}</div>
      <div className="v">{v}</div>
      {m !== undefined && <div className="m">{m}</div>}
    </div>
  );
}

function ConfigHero() {
  const t = useI18n((s) => s.t);
  // Same query keys as the cards below — the cache is shared, the hero adds
  // no extra polling of its own (only reads).
  const modeQuery = useRuntimeModeQuery(false);
  const cfgQuery = useConfigFormQuery(false);
  const diagQuery = useRuntimeDiagnosticsQuery(false);
  const tgQuery = useTelegramStatusQuery(false);

  const mode = modeQuery.data?.mode ?? null;
  const diag = diagQuery.data;
  const tg = tgQuery.data;

  return (
    <header className="cfg-hero">
      <div className="cfg-hero-top">
        <h1>{t("config.head.title", "Settings")}</h1>
                <span className="crumb">{t("config.head.crumb", "PLATFORM")}</span>
                <span className="desc">{t("config.head.desc", "Engine mode · runtime configuration · model swap · provenance · Telegram (legacy tab-config)")}</span>
      </div>
      <div className="cfg-stats" role="list" aria-label={t("config.head.stats_aria", "live status")}>
              <Stat
                k={t("config.head.k_mode", "execution mode")}
          tone={modeTone(mode)}
          v={
            modeQuery.isPending ? (
              <Skeleton count={1} height={16} />
            ) : modeQuery.isError || !mode ? (
              <span className="faint">—</span>
            ) : (
              <span className={`cfg-mode-live tone-${modeTone(mode)}`}>{modeLabel(t, mode)}</span>
                          )
                        }
                        m={modeQuery.data?.effective_mode ? <>{t("config.head.m_effective", "effective {mode}", { mode: modeQuery.data.effective_mode })}</> : t("config.head.m_effective_none", "effective —")}
                      />
        <Stat
                  k={t("config.head.k_engine", "engine")}
          tone={modeQuery.data?.engine_attached ? "good" : "bad"}
          v={
            modeQuery.isPending ? (
              <Skeleton count={1} height={16} />
            ) : modeQuery.isError ? (
              <span className="faint">—</span>
            ) : (
              <StatusBadge status={modeQuery.data?.engine_attached ? "CONNECTED" : "DISCONNECTED"} />
            )
          }
          m={modeQuery.data?.engine_attached ? (modeQuery.data?.replaying ? t("config.head.m_replaying", "replaying") : t("config.head.m_live_session", "live session")) : t("config.head.m_no_engine", "no engine reference")}
        />
        <Stat
          k={t("config.head.k_version", "config version")}
          tone={diag?.mismatch ? "warn" : diag ? "good" : undefined}
          v={cfgQuery.isPending ? <Skeleton count={1} height={16} /> : <>v{String(cfgQuery.data?.configuration_version ?? "—")}</>}
          m={
            diagQuery.isPending
                          ? t("config.head.m_checking", "checking store…")
                          : diagQuery.isError
                            ? t("config.head.m_diag_unreadable", "diagnostics unreadable")
                            : diag
                              ? diag.mismatch
                                ? t("config.head.m_mismatch", "⚠ persistent/runtime mismatch")
                                : t("config.head.m_match", "persistent = runtime")
                              : "—"
                      }
        />
        <Stat
          k={t("config.head.k_telegram", "telegram")}
          tone={tg?.configured ? "good" : undefined}
          v={tgQuery.isPending ? <Skeleton count={1} height={16} /> : <StatusBadge status={tg?.token_status ?? "UNKNOWN"} />}
          m={tg ? (tg.configured ? (tg.admin_id_shape_valid ? t("config.head.m_tg_ok", "configured · admin ok") : t("config.head.m_tg_bad", "configured · admin invalid")) : t("config.head.m_tg_off", "not configured")) : "—"}
        />
      </div>
      <p className="l3-note cfg-promise">
              {t("config.head.intro_before", "Validate-before-apply is enforced on every write: client rules (type/range/enum/shape) block an invalid payload locally, then")}{" "}
              <span className="inline-mono">/api/settings/validate</span>{" "}
              {t("config.head.intro_mid_apply", "dry-runs each proposed value server-side, and only the surviving hot batch reaches")}{" "}
              <span className="inline-mono">/api/runtime-config/apply</span>.{" "}
              {t("config.head.intro_after_restart", "Restart-bound keys are named, never sent. The engine’s report — not the HTTP status — decides the verdict shown here.")}
            </p>
    </header>
  );
}

/* ------------------------------------------------------------------ */
/* Engine mode card — mode rail + server preview before typed confirm  */
/* ------------------------------------------------------------------ */

function EngineModeCard() {
  const t = useI18n((s) => s.t);
  const poll = usePolling(15_000);
  const modeQuery = useRuntimeModeQuery(poll.paused);
  const setMode = useSetEngineMode();
  const preview = useModePreview();
  const pushToast = useUiStore((s) => s.pushToast);
  const [target, setTarget] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [result, setResult] = useState<CommandOutcome | null>(null);

  const current = modeQuery.data?.mode ?? null;
  const check = target ? checkModeTransition(current, target) : null;
  const allowed = allowedModes(current);
  const previewBusy = preview.isPending;
  const pv = !preview.isPending && preview.variables === target ? preview.data : undefined;
  const previewBlocked = pv !== undefined && pv.validation.valid === false;

  const requestSwitch = () => {
    if (!target || !check?.ok) return;
    setConfirmOpen(true);
    preview.mutate(target); // server matrix + impact for the modal (never applies)
  };

  const confirmSwitch = async () => {
    if (previewBusy) return;
    if (previewBlocked) return; // server refused the transition — never proceed
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
                    {t("config.engine.mode_unknown_note", "the current mode is UNKNOWN, never guessed.")} <button className="btn small" onClick={() => void modeQuery.refetch()}>{t("common.retry", "Retry")}</button>
        </div>
      ) : (
        <div className="cfg-mode-card">
          <div className="cfg-mode-facts">
            <div>
              <div className="timestamp-note">{t("config.engine.configured", "configured")}</div>
                            <div className={`l3-mode-badge ${badgeClass(current)}`}>{current ? modeLabel(t, current) : t("config.engine.offline", "ENGINE OFFLINE")}</div>
            </div>
            <div>
              <div className="timestamp-note">{t("config.engine.effective", "effective (runtime)")}</div>
              <div className="inline-mono">{modeQuery.data?.effective_mode ?? "—"}</div>
            </div>
            <div>
              <div className="timestamp-note">{t("config.engine.attached", "engine attached")}</div>
              <StatusBadge status={modeQuery.data?.engine_attached ? "CONNECTED" : "DISCONNECTED"} />
            </div>
          </div>

          <div className="l3-field">
            <div className="l3-field-head">
              <span className="lab">{t("config.engine.switch_mode", "switch mode")}</span>
                            <span className="cfg-rail-hint">{t("config.engine.rail_hint", "allowed from {current} · server re-checks at confirm", { current: current ?? t("config.engine.current_fallback", "current") })}</span>
            </div>
            <div className="cfg-rail" role="group" aria-label={t("config.engine.target_mode", "target execution mode")}>
              {EXECUTION_MODES.map((m) => {
                const isCurrent = m === current;
                const enabled = !isCurrent && allowed.includes(m);
                return (
                  <button
                    key={m}
                    type="button"
                    className={`cfg-chip mode-${m.toLowerCase()} ${isCurrent ? "is-current" : ""} ${m === "LIVE" ? "is-live" : ""} ${target === m ? "is-target" : ""}`}
                    aria-pressed={target === m}
                    disabled={!enabled}
                    title={
                                          isCurrent
                                            ? t("config.engine.title_current", "current mode")
                                            : enabled
                                              ? t("config.engine.chip_switch", "switch to {mode}", { mode: modeLabel(t, m) })
                                              : t("config.engine.chip_blocked", "{from} → {to} is not an allowed transition", { from: current ?? "?", to: m })
                                        }
                    onClick={() => setTarget(m)}
                  >
                    {isCurrent && <span className="cfg-chip-now">{t("config.engine.chip_now", "now")}</span>}
                                        {modeLabel(t, m)}
                  </button>
                );
              })}
            </div>
            <div className="cfg-switch-row">
              <button
                className="btn danger"
                disabled={!target || target === current || !check?.ok || setMode.isPending}
                onClick={requestSwitch}
              >
                {t("config.engine.switch_to", "Switch to {target}…", { target: target || "…" })}
              </button>
            </div>
            {check && check.errors.length > 0 && (
              <div className="l3-field-error" role="alert">{check.errors.join(" · ")}</div>
            )}
            {check && check.warnings.length > 0 && (
              <div className="l3-field-hint">⚠ {check.warnings.join(" · ")}</div>
            )}
            {target === "LIVE" && check?.ok && (
              <div className="l3-note bad">
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
          busy={previewBusy || setMode.isPending}
          busyLabel={previewBusy ? t("config.apply.validating", "validating…") : t("config.kit.sending", "sending…")}
          onCancel={() => setConfirmOpen(false)}
          onConfirm={() => void confirmSwitch()}
          body={
            <>
              <div>
                              {t("config.engine.confirm_body", "Current mode {from} → {to}. The engine hot-swaps the adapter boundary, persists to the settings DB and the versioned runtime store (source=WEB_UI).", { from: current ?? t("config.engine.unknown", "UNKNOWN"), to: target })}{" "}
                              {target === "LIVE" ? t("config.engine.confirm_live", "Orders will be placed with the real broker.") : ""}
                            </div>
              <div className="cfg-preview">
                {previewBusy && (
                  <div className="timestamp-note">{t("config.engine.preview_validating", "validating against the backend transition matrix…")}</div>
                )}
                {!previewBusy && preview.isError && (
                  <div className="l3-note warn">
                                      {t("config.engine.preview_unavailable", "Server preview unavailable ({error}) — the backend still re-checks the transition on switch; a refusal is shown verbatim.", { error: preview.error instanceof Error ? preview.error.message : t("config.engine.request_failed", "request failed") })}
                                    </div>
                )}
                {!previewBusy && !preview.isError && previewBlocked && pv && (
                  <div className="l3-note bad">
                                      {t("config.engine.preview_refused", "SERVER REFUSED THE TRANSITION — {errors}", { errors: pv.validation.errors.join(" · ") })}
                                    </div>
                )}
                {!previewBusy && !preview.isError && pv && pv.validation.valid && pv.validation.warnings.length > 0 && (
                  <div className="l3-note warn">{pv.validation.warnings.join(" · ")}</div>
                )}
                {!previewBusy && !preview.isError && pv && pv.validation.valid && pv.impact.touches.length > 0 && (
                  <div className="cfg-impact">
                    <span className="k">{t("config.engine.touches", "touches")}</span>
                    {pv.impact.touches.map((t) => (
                      <span key={t} className="cfg-impact-chip">{t}</span>
                    ))}
                  </div>
                )}
              </div>
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
  const [steps, setSteps] = useState<ApplySteps | null>(null);

  const specs = useMemo(() => runtimeConfigSpecs(), []);
  const baseline = useMemo(() => (cfgQuery.data ? configBaseline(cfgQuery.data) : null), [cfgQuery.data]);
  const values = draft ?? baseline ?? {};
  // perf: validation / dirty-key / section grouping derived only when their
  // inputs change (deps: specs, baseline, values — every reactive value read).
  const errors = useMemo(() => validateFields(specs, values), [specs, values]);
  const dirtyKeys = useMemo(() => (baseline ? changedKeys(baseline, values) : []), [baseline, values]);
  const sectionGroups = useMemo(
    () => specSections(specs).map((section) => ({ section, specs: specs.filter((s) => s.section === section) })),
    [specs],
  );

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
    const ran = await apply.mutateAsync(changes);
    setSteps(ran);
    setResult(ran.outcome);
    setStep(ran.sent ? "applied" : ran.clientErrors && hasErrors(ran.clientErrors) ? "client" : "server");
    if (ran.serverErrors && hasErrors(ran.serverErrors)) setServerFieldErrors(ran.serverErrors);
    const allErrors = { ...(ran.clientErrors ?? {}), ...(ran.serverErrors ?? {}) };
    pushToast(ran.outcome?.ok ? "ok" : "fail", ran.outcome?.message ?? t("config.apply.refused_toast", "apply refused"));
    if (ran.outcome?.ok) {
      // Keep the draft when restart-bound edits remain (they were NOT sent —
      // they must stay visible as unsaved local edits, never silently lost).
      if (!ran.restartRequired || ran.restartRequired.length === 0) setDraft(null);
    } else if (hasErrors(allErrors)) {
      setResult({ ok: false, message: `${ran.outcome?.message ?? t("config.apply.refused", "Refused.")} → ${flattenErrors(allErrors).join(" · ")}`, requestId: ran.outcome?.requestId ?? null });
    }
  };

  const restartLeft = steps?.restartRequired && steps.restartRequired.length > 0 ? steps.restartRequired : null;

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
          <NumberField value={STR(v)} onChange={(s) => setValue(spec.key, s)} error={err} step={spec.kind === "integer" ? "1" : "any"} spec={spec.label ?? spec.key} />
        ) : (
          <TextField value={STR(v)} onChange={(s) => setValue(spec.key, s)} error={err} spec={spec.label ?? spec.key} />
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
          <FreshnessCaption fetchedAtMs={cfgQuery.dataUpdatedAt || null} intervalMs={60_000} note={cfgQuery.data?.runtime_applied ? t("config.apply.note_live", "live store") : t("config.apply.note_fallback", "live.yaml fallback (engine offline)")} stale={!cfgQuery.data?.runtime_applied} />
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
                          {t("config.apply.masked_before", "Telegram token arrives masked")} (<span className="inline-mono">{cfg.telegram.bot_token}</span>){" "}
                          {t("config.apply.masked_after", "— manage it in the Telegram panel; the form never re-submits a mask (BUG-080).")}
                        </div>
          )}
          {sectionGroups.map((group) => (
            <div key={group.section} className="cfg-sec">
              <div className="cfg-sec-title">{group.section}</div>
              <div className="cfg-fields">
                {group.specs.map(renderSpec)}
              </div>
            </div>
          ))}
          <div className="l3-toolbar cfg-actions">
            {dirtyKeys.length > 0 && (
              <>
                <span className="cfg-dirtycount" title={t("config.apply.dirty_title", "edited locally, not yet applied")}>
                                  {t("config.apply.dirty_count", "{n} edited", { n: dirtyKeys.length })}
                                </span>
                <button className="btn ghost" onClick={() => { setDraft(null); setServerFieldErrors({}); setSteps(null); }}>
                                  {dirtyKeys.length === 1 ? t("config.apply.revert_one", "Revert 1 edit") : t("config.apply.revert_many", "Revert {n} edits", { n: dirtyKeys.length })}
                                </button>
              </>
            )}
            <span className="timestamp-note cfg-pipeline">
                          {t("config.apply.pipeline", "validate({v}) → apply({a}) → report → refetch", { v: "/api/settings/validate", a: "/api/runtime-config/apply" })}
                        </span>
            <button
              className="btn primary"
              disabled={dirtyKeys.length === 0 || hasErrors(errors) || apply.isPending}
              title={hasErrors(errors) ? t("config.apply.title_fix", "fix the highlighted fields first — an invalid payload is never sent") : t("config.apply.title_batch", "validate each key server-side, then apply the batch")}
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
            <div className="l3-note bad">{t("config.apply.client_blocked", "CLIENT VALIDATION BLOCKED SUBMISSION: {errors}", { errors: flattenErrors(errors).join(" · ") })}</div>
          )}
          <ResultStrip result={apply.isPending ? { running: true, lastResult: null, lastMessage: null } : outcomeLine(result)} />
          {restartLeft && (
            <div className="l3-note warn cfg-restart">
                          {t("config.apply.restart_chips", "RESTART REQUIRED — not sent through the hot-apply gate, kept as local edits:")}{" "}
              {restartLeft.map((k) => (
                <span key={k} className="cfg-restart-chip">{k}</span>
              ))}
            </div>
          )}

          <div className="cfg-sec">
            <div className="cfg-sec-title">{t("config.diag.title", "Version truth (runtime-config diagnostics)")}</div>
            {diagQuery.isPending ? (
              <Skeleton count={2} height={12} />
            ) : diagQuery.data ? (
              <div className="l3-runtime-ver">
                <span>{t("config.diag.persistent", "persistent v{v}", { v: String(diagQuery.data.persistent_version ?? "—") })}</span>
                                <span>{t("config.diag.runtime", "runtime v{v}", { v: String(diagQuery.data.runtime_version ?? "—") })}</span>
                                <span className={diagQuery.data.mismatch ? "mismatch" : ""}>{diagQuery.data.mismatch ? t("config.diag.mismatch", "⚠ VERSION MISMATCH") : t("config.diag.match", "versions match")}</span>
                                <span>{t("config.diag.last_apply", "last apply: {status}", { status: diagQuery.data.last_apply_status })}</span>
                                {diagQuery.data.last_apply_error && <span className="mismatch">{t("config.diag.error", "error: {msg}", { msg: diagQuery.data.last_apply_error })}</span>}
                                <span>{diagQuery.data.live_yaml_exists ? t("config.diag.live_yaml_yes", "live.yaml: yes ({hash})…", { hash: diagQuery.data.live_yaml_hash.slice(0, 12) }) : t("config.diag.live_yaml_absent", "live.yaml: absent")}</span>
              </div>
            ) : (
              <div className="l3-note bad">{t("config.diag.unavailable", "diagnostics unavailable")}</div>
            )}
            {effectiveQuery.data && (
              <div className="tiny faint" style={{ marginTop: 4 }}>
                {t("config.diag.effective", "effective snapshot: v{v} · source {src} · updated {at}", { v: String(effectiveQuery.data.configuration_version ?? "—"), src: String(effectiveQuery.data.source ?? "—"), at: String(effectiveQuery.data.updated_at ?? "—") })}
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
  const swap = useModelSwap();
  const pushToast = useUiStore((s) => s.pushToast);
  const [path, setPath] = useState("");
  // A fresh spec object literal per render made `errors` recompute every
  // keystroke and every poll. One stable identity, memoized result — same
  // messages, same disabled gating.
  const spec = useMemo(
    () => ({ key: "model_artifact_path", label: "artifact path", kind: "path" as const, required: true, pattern: "\\.(pt|onnx|joblib|pkl)$", patternMessage: "artifact must be a .pt / .onnx / .joblib / .pkl file" }),
    [],
  );
  const errors = useMemo(() => validateFields([spec], { model_artifact_path: path }), [spec, path]);
  const err = useMemo(() => firstError(errors, spec.key), [errors, spec.key]);

  const run = async () => {
    if (hasErrors(errors) || path.trim() === "") return;
    const outcome = await swap.mutateAsync(path.trim());
    pushToast(outcome.ok ? "ok" : "fail", outcome.message);
  };

  return (
    <Panel title={t("config.swap.title", "Model artifact hot-swap")} right={<span className="timestamp-note">{t("config.swap.pipeline", "load → validate → warm → atomic swap")}</span>}>
      <div className="l3-form">
        <FieldRow label={t("config.field.artifact_path", "artifact path")} hint={spec.key} error={path === "" ? null : err}>
          <TextField value={path} onChange={setPath} error={err} placeholder={t("config.swap.ph", "artifacts/model.pt")} spec={t("config.field.artifact_path_short", "artifact path")} />
        </FieldRow>
      </div>
      <div className="l3-toolbar cfg-actions">
        <button className="btn primary" disabled={path.trim() === "" || hasErrors(errors) || swap.isPending} onClick={() => void run()}>
          {swap.isPending ? t("config.swap.swapping", "swapping…") : t("config.swap.action", "Swap model…")}
        </button>
      </div>
      {hasErrors(errors) && path !== "" && <div className="l3-note bad">{flattenErrors(errors).join(" · ")}</div>}
      <ResultStrip result={swap.isPending ? { running: true, lastResult: null, lastMessage: null } : outcomeLine(swap.data ?? null)} />
      <div className="tiny faint" style={{ marginTop: 6 }}>
        {t("config.swap.note", "A healthy serving model is never replaced before the new artifact loads and warms — the engine's verdict is shown above verbatim.")}
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
          <button className="btn small ghost" onClick={() => setOpen((o) => !o)}>{open ? t("config.prov.hide_table", "hide") : t("config.prov.show_table", "show")} {t("config.prov.table_word", "table")}</button>
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
            <span>{t("config.prov.state", "state:")} <b>{query.data?.state ?? "—"}</b></span>
                        <span>{t("config.prov.db", "db:")} <MonoValue value={query.data?.db_path} /></span>
                        <span>{t("config.prov.tracked", "{n} tracked keys", { n: Object.keys(query.data?.settings ?? {}).length })}</span>
          </div>
          {open && query.data && (
            <div tabIndex={0} className="l3-scroll sm" style={{ marginTop: 8 }}>
              <table className="data-table">
                <thead>
                  <tr>
                    <th scope="col">{t("config.prov.th_key", "KEY")}</th><th scope="col">{t("config.prov.th_value", "VALUE")}</th><th scope="col">{t("config.prov.th_source", "SOURCE")}</th><th scope="col">{t("config.prov.th_ver", "VER")}</th><th scope="col">{t("config.prov.th_mutability", "MUTABILITY")}</th>
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
          <div className="cfg-sec-title" style={{ marginTop: 10 }}>{t("config.prov.recent_audit", "Recent settings audit")}</div>
          <KeyValueList
            rows={(query.data?.recent_audit ?? []).slice(0, 8).map((row, i) => [
              `${STR(row.key) || STR((row as Record<string, unknown>).setting_key) || "event"} · ${STR(row.timestamp) || `#${i}`}`,
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
  const save = useSaveTelegram();
  const test = useTestTelegram();
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
      { key: "bot_token", label: "bot token", kind: "token" as const, secret: true, pattern: BOT_TOKEN_PATTERN, patternMessage: "expected \\d+:\\w{20,} (BotFather shape)" },
      { key: "admin_id", label: "admin chat id", kind: "regex" as const, pattern: ADMIN_ID_PATTERN, patternMessage: "expected a numeric chat id (-?\\d{4,})" },
    ],
    [],
  );
  const errors = useMemo(() => validateFields(specs, { bot_token: token, admin_id: admin }), [specs, token, admin]);
  const dirty = token !== "" || admin !== "" || (enabled !== null && enabled !== (st?.enabled ?? false));

  const errToken = useMemo(() => firstError(errors, "bot_token"), [errors]);
  const errAdmin = useMemo(() => firstError(errors, "admin_id"), [errors]);

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
          <div className="l3-runtime-ver cfg-tg-facts" style={{ marginBottom: 6 }}>
            <span>{t("config.tg.token", "token:")} <StatusBadge status={st?.token_status ?? "UNKNOWN"} /></span>
                        <span className="l3-mask">{st?.masked_token || t("config.tg.no_mask", "no mask served")}</span>
                        <span>{t("config.tg.configured", "configured:")} {st?.configured ? t("config.tg.yes", "yes") : t("config.tg.no", "no")}</span>
                        <span>{t("config.tg.admin_shape", "admin shape:")} {st?.admin_id_shape_valid ? t("config.tg.valid", "valid") : t("config.tg.invalid", "invalid/missing")}</span>
                        <span>{t("config.tg.source", "source:")} {st?.source ?? "—"}</span>
          </div>
          <FieldRow label={t("config.field.enabled", "enabled")} hint={t("config.tg.hint_hot", "telegram.enabled (HOT_RESTRICTED)")}>
                      <CheckField checked={effectiveEnabled} onChange={setEnabled} label={t("config.tg.enabled_label", "telegram enabled")} />
                    </FieldRow>
          <FieldRow label={t("config.field.bot_token", "bot token")} hint={t("config.tg.token_hint_text", "leave empty to keep the stored secret — it never round-trips in plaintext (BUG-072)")} error={errToken}>
                      <TextField value={token} onChange={setToken} error={errToken} placeholder={st?.masked_token || "123456:ABC-DEF…"} spec={t("config.field.bot_token", "bot token")} />
          </FieldRow>
          <FieldRow label={t("config.field.admin_id", "admin chat id")} hint={t("config.tg.admin_hint_text", "numeric chat id of the operator")} error={errAdmin}>
                      <TextField value={admin} onChange={setAdmin} error={errAdmin} placeholder="-1001234567890" spec={t("config.field.admin_id", "admin chat id")} />
          </FieldRow>
          <div className="l3-toolbar cfg-actions">
            <button className="btn" disabled={!dirty || hasErrors(errors) || save.isPending} onClick={() => void runSave()}>
              {save.isPending ? t("config.tg.saving", "saving…") : t("config.tg.save_action", "Save telegram settings…")}
            </button>
            <button className="btn primary" disabled={test.isPending || !st?.configured} title={st?.configured ? t("config.tg.test_hint", "sends a real test message through the notifier") : t("config.tg.test_first", "configure token + admin first")} onClick={() => void runTest()}>
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
  return (
    <div className="cfg-page l3-wrap">
      <ConfigHero />
      <div className="l3-split">
        <div className="cfg-col">
          <RuntimeConfigForm />
          <SettingsProvenance />
        </div>
        <div className="cfg-col">
          <EngineModeCard />
          <ModelSwapCard />
          <TelegramPanel />
        </div>
      </div>
    </div>
  );
}
