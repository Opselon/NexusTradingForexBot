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
        <h1>Settings</h1>
        <span className="crumb">PLATFORM</span>
        <span className="desc">Engine mode · runtime configuration · model swap · provenance · Telegram (legacy tab-config)</span>
      </div>
      <div className="cfg-stats" role="list" aria-label="live status">
        <Stat
          k="execution mode"
          tone={modeTone(mode)}
          v={
            modeQuery.isPending ? (
              <Skeleton count={1} height={16} />
            ) : modeQuery.isError || !mode ? (
              <span className="faint">—</span>
            ) : (
              <span className={`cfg-mode-live tone-${modeTone(mode)}`}>{mode}</span>
            )
          }
          m={modeQuery.data?.effective_mode ? <>effective {modeQuery.data.effective_mode}</> : "effective —"}
        />
        <Stat
          k="engine"
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
          m={modeQuery.data?.engine_attached ? (modeQuery.data?.replaying ? "replaying" : "live session") : "no engine reference"}
        />
        <Stat
          k="config version"
          tone={diag?.mismatch ? "warn" : diag ? "good" : undefined}
          v={cfgQuery.isPending ? <Skeleton count={1} height={16} /> : <>v{String(cfgQuery.data?.configuration_version ?? "—")}</>}
          m={
            diagQuery.isPending
              ? "checking store…"
              : diagQuery.isError
                ? "diagnostics unreadable"
                : diag
                  ? diag.mismatch
                    ? "⚠ persistent/runtime mismatch"
                    : "persistent = runtime"
                  : "—"
          }
        />
        <Stat
          k="telegram"
          tone={tg?.configured ? "good" : undefined}
          v={tgQuery.isPending ? <Skeleton count={1} height={16} /> : <StatusBadge status={tg?.token_status ?? "UNKNOWN"} />}
          m={tg ? (tg.configured ? (tg.admin_id_shape_valid ? "configured · admin ok" : "configured · admin invalid") : "not configured") : "—"}
        />
      </div>
      <p className="l3-note cfg-promise">
        Validate-before-apply is enforced on every write: client rules (type/range/enum/shape) block an invalid payload
        locally, then <span className="inline-mono">/api/settings/validate</span> dry-runs each proposed value
        server-side, and only the surviving hot batch reaches <span className="inline-mono">/api/runtime-config/apply</span>.
        Restart-bound keys are named, never sent. The engine’s report — not the HTTP status — decides the verdict shown here.
      </p>
    </header>
  );
}

/* ------------------------------------------------------------------ */
/* Engine mode card — mode rail + server preview before typed confirm  */
/* ------------------------------------------------------------------ */

function EngineModeCard() {
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
      title="Engine execution mode"
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
          {modeQuery.error instanceof Error ? modeQuery.error.message : "runtime/mode unreadable"} — the current mode is
          UNKNOWN, never guessed. <button className="btn small" onClick={() => void modeQuery.refetch()}>Retry</button>
        </div>
      ) : (
        <div className="cfg-mode-card">
          <div className="cfg-mode-facts">
            <div>
              <div className="timestamp-note">configured</div>
              <div className={`l3-mode-badge ${badgeClass(current)}`}>{current ?? "ENGINE OFFLINE"}</div>
            </div>
            <div>
              <div className="timestamp-note">effective (runtime)</div>
              <div className="inline-mono">{modeQuery.data?.effective_mode ?? "—"}</div>
            </div>
            <div>
              <div className="timestamp-note">engine attached</div>
              <StatusBadge status={modeQuery.data?.engine_attached ? "CONNECTED" : "DISCONNECTED"} />
            </div>
          </div>

          <div className="l3-field">
            <div className="l3-field-head">
              <span className="lab">switch mode</span>
              <span className="cfg-rail-hint">allowed from {current ?? "current"} · server re-checks at confirm</span>
            </div>
            <div className="cfg-rail" role="group" aria-label="target execution mode">
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
                        ? "current mode"
                        : enabled
                          ? `switch to ${m}`
                          : `${current ?? "?"} → ${m} is not an allowed transition`
                    }
                    onClick={() => setTarget(m)}
                  >
                    {isCurrent && <span className="cfg-chip-now">now</span>}
                    {m}
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
                Switch to {target || "…"}…
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
                LIVE = real capital at risk through the broker adapter. The backend re-checks everything (BUG-148 path)
                — a 200 alone is never treated as success; the response payload is.
              </div>
            )}
          </div>
          <ResultStrip result={setMode.isPending ? { running: true, lastResult: null, lastMessage: null } : outcomeLine(result)} />
        </div>
      )}
      {confirmOpen && target && (
        <TypedConfirmModal
          title={`Switch execution mode → ${target}`}
          word={target}
          confirmLabel="Switch mode"
          busy={previewBusy || setMode.isPending}
          busyLabel={previewBusy ? "validating…" : "sending…"}
          onCancel={() => setConfirmOpen(false)}
          onConfirm={() => void confirmSwitch()}
          body={
            <>
              <div>
                Current mode <b>{current ?? "UNKNOWN"}</b> → <b>{target}</b>. The engine hot-swaps the adapter boundary,
                persists to the settings DB and the versioned runtime store (source=WEB_UI).{" "}
                {target === "LIVE" ? "Orders will be placed with the real broker." : ""}
              </div>
              <div className="cfg-preview">
                {previewBusy && (
                  <div className="timestamp-note">validating against the backend transition matrix…</div>
                )}
                {!previewBusy && preview.isError && (
                  <div className="l3-note warn">
                    Server preview unavailable ({preview.error instanceof Error ? preview.error.message : "request failed"}) —
                    the backend still re-checks the transition on switch; a refusal is shown verbatim.
                  </div>
                )}
                {!previewBusy && !preview.isError && previewBlocked && pv && (
                  <div className="l3-note bad">
                    SERVER REFUSED THE TRANSITION — {pv.validation.errors.join(" · ")}
                  </div>
                )}
                {!previewBusy && !preview.isError && pv && pv.validation.valid && pv.validation.warnings.length > 0 && (
                  <div className="l3-note warn">{pv.validation.warnings.join(" · ")}</div>
                )}
                {!previewBusy && !preview.isError && pv && pv.validation.valid && pv.impact.touches.length > 0 && (
                  <div className="cfg-impact">
                    <span className="k">touches</span>
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
      setResult({ ok: false, message: "Nothing changed against the server baseline — payload would be empty.", requestId: null });
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
    pushToast(ran.outcome?.ok ? "ok" : "fail", ran.outcome?.message ?? "apply refused");
    if (ran.outcome?.ok) {
      // Keep the draft when restart-bound edits remain (they were NOT sent —
      // they must stay visible as unsaved local edits, never silently lost).
      if (!ran.restartRequired || ran.restartRequired.length === 0) setDraft(null);
    } else if (hasErrors(allErrors)) {
      setResult({ ok: false, message: `${ran.outcome?.message ?? "Refused."} → ${flattenErrors(allErrors).join(" · ")}`, requestId: ran.outcome?.requestId ?? null });
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
      title="Runtime configuration (execution · risk · model)"
      accent
      query={cfgQuery}
      skeletonRows={6}
      emptyMessage="Backend returned no configuration."
      right={
        <>
          <FreshnessCaption fetchedAtMs={cfgQuery.dataUpdatedAt || null} intervalMs={60_000} note={cfgQuery.data?.runtime_applied ? "live store" : "live.yaml fallback (engine offline)"} stale={!cfgQuery.data?.runtime_applied} />
          <PollControl paused={poll.paused} onToggle={poll.togglePaused} intervalMs={60_000} busy={cfgQuery.isFetching} />
        </>
      }
    >
      {(cfg) => (
        <div>
          {!cfg.runtime_applied && (
            <div className="l3-note warn" style={{ marginBottom: 10 }}>
              ENGINE OFFLINE — values below come from the live.yaml bootstrap fallback (diagnostic only). Applies will
              be refused or persisted-only; the backend decides.
            </div>
          )}
          {cfg.telegram?.bot_token && isMaskedValue(cfg.telegram.bot_token) && (
            <div className="l3-note" style={{ marginBottom: 10 }}>
              Telegram token arrives masked (<span className="inline-mono">{cfg.telegram.bot_token}</span>) — manage it in the Telegram panel; the form never re-submits a mask (BUG-080).
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
                <span className="cfg-dirtycount" title="edited locally, not yet applied">
                  {dirtyKeys.length} edited
                </span>
                <button className="btn ghost" onClick={() => { setDraft(null); setServerFieldErrors({}); setSteps(null); }}>
                  Revert {dirtyKeys.length} edit{dirtyKeys.length === 1 ? "" : "s"}
                </button>
              </>
            )}
            <span className="timestamp-note cfg-pipeline">
              validate(/api/settings/validate, value) → apply(/api/runtime-config/apply) → report → refetch
            </span>
            <button
              className="btn primary"
              disabled={dirtyKeys.length === 0 || hasErrors(errors) || apply.isPending}
              title={hasErrors(errors) ? "fix the highlighted fields first — an invalid payload is never sent" : "validate each key server-side, then apply the batch"}
              onClick={() => void applyChanges()}
            >
              {apply.isPending
                ? step === "applied"
                  ? "applying…"
                  : "validating…"
                : `Validate & apply (${dirtyKeys.length})`}
            </button>
          </div>
          {hasErrors(errors) && dirtyKeys.length > 0 && (
            <div className="l3-note bad">CLIENT VALIDATION BLOCKED SUBMISSION: {flattenErrors(errors).join(" · ")}</div>
          )}
          <ResultStrip result={apply.isPending ? { running: true, lastResult: null, lastMessage: null } : outcomeLine(result)} />
          {restartLeft && (
            <div className="l3-note warn cfg-restart">
              RESTART REQUIRED — not sent through the hot-apply gate, kept as local edits:{" "}
              {restartLeft.map((k) => (
                <span key={k} className="cfg-restart-chip">{k}</span>
              ))}
            </div>
          )}

          <div className="cfg-sec">
            <div className="cfg-sec-title">Version truth (runtime-config diagnostics)</div>
            {diagQuery.isPending ? (
              <Skeleton count={2} height={12} />
            ) : diagQuery.data ? (
              <div className="l3-runtime-ver">
                <span>persistent v{String(diagQuery.data.persistent_version ?? "—")}</span>
                <span>runtime v{String(diagQuery.data.runtime_version ?? "—")}</span>
                <span className={diagQuery.data.mismatch ? "mismatch" : ""}>{diagQuery.data.mismatch ? "⚠ VERSION MISMATCH" : "versions match"}</span>
                <span>last apply: {diagQuery.data.last_apply_status}</span>
                {diagQuery.data.last_apply_error && <span className="mismatch">error: {diagQuery.data.last_apply_error}</span>}
                <span>live.yaml: {diagQuery.data.live_yaml_exists ? `yes (${diagQuery.data.live_yaml_hash.slice(0, 12)}…)` : "absent"}</span>
              </div>
            ) : (
              <div className="l3-note bad">diagnostics unavailable</div>
            )}
            {effectiveQuery.data && (
              <div className="tiny faint" style={{ marginTop: 4 }}>
                effective snapshot: v{String(effectiveQuery.data.configuration_version ?? "—")} · source{" "}
                {String(effectiveQuery.data.source ?? "—")} · updated {String(effectiveQuery.data.updated_at ?? "—")}
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
  const swap = useModelSwap();
  const pushToast = useUiStore((s) => s.pushToast);
  const [path, setPath] = useState("");
  const spec = { key: "model_artifact_path", label: "artifact path", kind: "path" as const, required: true, pattern: "\\.(pt|onnx|joblib|pkl)$", patternMessage: "artifact must be a .pt / .onnx / .joblib / .pkl file" };
  const errors = validateFields([spec], { model_artifact_path: path });
  const err = firstError(errors, spec.key);

  const run = async () => {
    if (hasErrors(errors) || path.trim() === "") return;
    const outcome = await swap.mutateAsync(path.trim());
    pushToast(outcome.ok ? "ok" : "fail", outcome.message);
  };

  return (
    <Panel title="Model artifact hot-swap" right={<span className="timestamp-note">load → validate → warm → atomic swap</span>}>
      <div className="l3-form">
        <FieldRow label={spec.label} hint={spec.key} error={path === "" ? null : err}>
          <TextField value={path} onChange={setPath} error={err} placeholder="artifacts/model.pt" spec="artifact path" />
        </FieldRow>
      </div>
      <div className="l3-toolbar cfg-actions">
        <button className="btn primary" disabled={path.trim() === "" || hasErrors(errors) || swap.isPending} onClick={() => void run()}>
          {swap.isPending ? "swapping…" : "Swap model…"}
        </button>
      </div>
      {hasErrors(errors) && path !== "" && <div className="l3-note bad">{flattenErrors(errors).join(" · ")}</div>}
      <ResultStrip result={swap.isPending ? { running: true, lastResult: null, lastMessage: null } : outcomeLine(swap.data ?? null)} />
      <div className="tiny faint" style={{ marginTop: 6 }}>
        A healthy serving model is never replaced before the new artifact loads and warms — the engine's verdict is shown above verbatim.
      </div>
    </Panel>
  );
}

/* ------------------------------------------------------------------ */
/* Settings provenance editor (/api/settings)                          */
/* ------------------------------------------------------------------ */

function SettingsProvenance() {
  const poll = usePolling(60_000);
  const query = useSettingsSnapshotQuery(poll.paused);
  const [open, setOpen] = useState(false);

  return (
    <Panel
      title="Settings provenance (application_settings)"
      right={
        <>
          <FreshnessCaption fetchedAtMs={query.dataUpdatedAt || null} intervalMs={60_000} stale={poll.paused} />
          <button className="btn small ghost" onClick={() => setOpen((o) => !o)}>{open ? "hide" : "show"} table</button>
        </>
      }
    >
      {query.isPending ? (
        <Skeleton count={3} />
      ) : query.isError ? (
        <div className="l3-note bad">{query.error instanceof Error ? query.error.message : "settings unreadable"}</div>
      ) : (
        <>
          <div className="l3-runtime-ver">
            <span>state: <b>{query.data?.state ?? "—"}</b></span>
            <span>db: <MonoValue value={query.data?.db_path} /></span>
            <span>{Object.keys(query.data?.settings ?? {}).length} tracked keys</span>
          </div>
          {open && query.data && (
            <div tabIndex={0} className="l3-scroll sm" style={{ marginTop: 8 }}>
              <table className="data-table">
                <thead>
                  <tr>
                    <th scope="col">KEY</th><th scope="col">VALUE</th><th scope="col">SOURCE</th><th scope="col">VER</th><th scope="col">MUTABILITY</th>
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
            Read-only provenance ("which value is active, where did it come from"). Writes go through the runtime-config
            gate above or the Telegram panel — never by editing this table; secret rows are masked by the backend itself.
          </div>
          <div className="cfg-sec-title" style={{ marginTop: 10 }}>Recent settings audit</div>
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
  const errors = validateFields(specs, { bot_token: token, admin_id: admin });
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
      title="Telegram alerts"
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
        <div className="l3-note bad">{status.error instanceof Error ? status.error.message : "telegram status unreadable"}</div>
      ) : (
        <div className="l3-form">
          <div className="l3-runtime-ver cfg-tg-facts" style={{ marginBottom: 6 }}>
            <span>token: <StatusBadge status={st?.token_status ?? "UNKNOWN"} /></span>
            <span className="l3-mask">{st?.masked_token || "no mask served"}</span>
            <span>configured: {st?.configured ? "yes" : "no"}</span>
            <span>admin shape: {st?.admin_id_shape_valid ? "valid" : "invalid/missing"}</span>
            <span>source: {st?.source ?? "—"}</span>
          </div>
          <FieldRow label="enabled" hint="telegram.enabled (HOT_RESTRICTED)">
            <CheckField checked={effectiveEnabled} onChange={setEnabled} label="telegram enabled" />
          </FieldRow>
          <FieldRow label={specs[0]!.label!} hint="leave empty to keep the stored secret — it never round-trips in plaintext (BUG-072)" error={firstError(errors, "bot_token")}>
            <TextField value={token} onChange={setToken} error={firstError(errors, "bot_token")} placeholder={st?.masked_token || "123456:ABC-DEF…"} spec="bot token" />
          </FieldRow>
          <FieldRow label={specs[1]!.label!} hint="numeric chat id of the operator" error={firstError(errors, "admin_id")}>
            <TextField value={admin} onChange={setAdmin} error={firstError(errors, "admin_id")} placeholder="-1001234567890" spec="admin chat id" />
          </FieldRow>
          <div className="l3-toolbar cfg-actions">
            <button className="btn" disabled={!dirty || hasErrors(errors) || save.isPending} onClick={() => void runSave()}>
              {save.isPending ? "saving…" : "Save telegram settings…"}
            </button>
            <button className="btn primary" disabled={test.isPending || !st?.configured} title={st?.configured ? "sends a real test message through the notifier" : "configure token + admin first"} onClick={() => void runTest()}>
              {test.isPending ? "sending…" : "Send test message"}
            </button>
          </div>
          {hasErrors(errors) && <div className="l3-note bad">{flattenErrors(errors).join(" · ")}</div>}
          <ResultStrip result={save.isPending ? { running: true, lastResult: null, lastMessage: null } : outcomeLine(saveResult)} />
          <ResultStrip result={test.isPending ? { running: true, lastResult: null, lastMessage: null } : outcomeLine(testResult)} />
          {st?.worker && (
            <div className="tiny faint" style={{ marginTop: 6 }}>
              notifier worker: {Object.entries(st.worker).slice(0, 6).map(([k, v]) => `${k}=${String(v)}`).join(" · ")}
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
