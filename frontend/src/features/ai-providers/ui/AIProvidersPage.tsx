/**
 * features/ai-providers/ui/AIProvidersPage.tsx
 *
 * AI Provider Operational Control Plane (ECOSYSTEM-001).
 *
 * What this panel IS: the operator surface for the provider ecosystem — the
 * registry table with per-provider lifecycle and health, an add-provider
 * wizard (template -> connection -> model -> test -> activate), per-provider
 * Test / Test Model / Discover Models / Configure / Activate / Deactivate /
 * Rollback, and the decision pipeline for one snapshot.
 *
 * What this panel NEVER does:
 *   - edit or display an API key VALUE. The secret is typed into a password
 *     field, sent to the secure store on save, and then only `has_secret` is
 *     ever rendered (Section 37). The transient form value is dropped the
 *     moment the request leaves.
 *   - fabricate rationale or state. Every badge, chip and verdict maps to a
 *     field the backend actually returned; a missing field renders "—" or
 *     UNKNOWN, never a guess (Section 57/61).
 *   - claim an AI executed a trade. Labels are deliberately AI Recommendation
 *     -> Policy Decision -> Risk Decision.
 *   - run a network test on the 5s refresh. Tests are explicit button clicks.
 *
 * Styled via ./ai-providers.css on the shared theme tokens — no CSS framework.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { ShellPageProps } from "@/app/featureModule";
import { useDialogA11y } from "@/components/useDialogA11y";
import { ErrorState, LoadingState, MetricCard, Panel, ProbBar, StatusBadge } from "@/components/primitives";

import { aiProvidersApi, failedPreconditions, rollbackHint } from "../api";
import type {
  CompareResponse,
  DecisionRecord,
  ProviderConfigRequest,
  ProviderEntry,
  ProviderListResponse,
  ProviderTemplate,
  ProviderTestResult,
  SwitchResponse,
  TestModelResponse,
} from "../model";
import { isFailureLifecycle, isHealthyLifecycle, LIFECYCLE_STATES } from "../model";
import "./ai-providers.css";

const REFRESH_MS = 5000;

/** Endpoints this page reads — provenance shown as hero chips, verbatim. */
const ENDPOINTS = [
  "/api/ai-providers",
  "/api/ai-providers/templates",
  "/api/ai-providers/providers",
  "/api/ai-providers/providers/{id}/test",
  "/api/ai-providers/providers/{id}/test-model",
  "/api/ai-providers/switch",
  "/api/ai-providers/deactivate",
  "/api/ai-providers/rollback",
] as const;

/** The wizard steps. The operator may stop after any one; creating a provider
 *  never activates it (Section 53) — activation is the explicit last step. */
type WizardStep = 1 | 2 | 3 | 4 | 5;
const WIZARD_STEPS: Array<{ n: WizardStep; label: string }> = [
  { n: 1, label: "Template" },
  { n: 2, label: "Connection" },
  { n: 3, label: "Model" },
  { n: 4, label: "Test" },
  { n: 5, label: "Activate" },
];

/** The pipeline the operator is meant to read, top to bottom (Section 68). */
const PIPELINE = [
  "Position Snapshot",
  "Internal ML",
  "System One",
  "OpenRouter",
  "Response Normalize",
  "Decision Fusion",
  "Deterministic Policy",
  "Risk Engine",
  "Broker Validation",
] as const;

function classNames(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

/** Pull the `detail` string out of a 4xx the way the shared middleware surfaces
 *  it, so a validation failure reads verbatim (Section 57). */
function errText(e: unknown): string {
  if (e instanceof Error) return e.message;
  return String(e);
}

/** A failed switch now returns a STRUCTURED 422 body: {reason, preconditions}.
 *  Surfacing only the bare reason would hide WHICH check failed (Section 42:
 *  every refusal must name its cause), so pull the per-check details out and
 *  render them verbatim. Never invents a cause the backend did not send. */
function switchRefusalText(e: unknown): string {
  const detail = httpDetail(e) as SwitchRefusalDetail | null;
  if (detail) {
    const reason = typeof detail.reason === "string" ? detail.reason : "";
    const rows = Array.isArray(detail.preconditions)
      ? detail.preconditions.filter((p) => p && typeof p.detail === "string")
      : [];
    const parts = rows.map((p) => p.detail);
    if (reason && parts.length) return `${reason} — ${parts.join("; ")}`;
    if (reason) return reason;
    if (parts.length) return parts.join("; ");
  }
  return errText(e);
}

/** The structured refusal the backend sends on a failed switch. */
type SwitchRefusalDetail = {
  reason?: string;
  preconditions?: Array<{ check: string; detail: string }>;
};

/** FastAPI error payloads arrive as {detail: ...}; pull the payload out. */
function httpDetail(e: unknown): unknown {
  const any = e as { response?: { data?: { detail?: unknown } }; detail?: unknown };
  return any?.response?.data?.detail ?? any?.detail ?? null;
}

/** Health dot class for the provider list (Section 19). Derived only from
 *  backend fields. */
function healthClass(p: ProviderEntry): string {
  if (!p.enabled) return "off";
  if (p.health && !p.health.available) return "bad";
  if (p.circuit_breaker_state === "OPEN") return "bad";
  if (p.failure_count > 0) return "warn";
  if (p.health && p.health.available) return "good";
  return "unknown";
}

/** Reachable word for the health column: the backend's own availability, never
 *  a client-side probe. */
function reachableWord(p: ProviderEntry): { word: string; tone: "good" | "bad" | "warn" | "dim" } {
  if (!p.enabled) return { word: "DISABLED", tone: "dim" };
  if (p.health) return p.health.available ? { word: "REACHABLE", tone: "good" } : { word: "UNREACHABLE", tone: "bad" };
  if (p.health_status) return { word: p.health_status, tone: "dim" };
  return { word: "UNKNOWN", tone: "dim" };
}

/** Lifecycle badge category — the token is the backend's; only the COLOUR is
 *  chosen here, from the token's own category. */
function lifecycleTone(state: string | null | undefined): "good" | "warn" | "bad" | "dim" {
  if (!state) return "dim";
  if (isFailureLifecycle(state)) return "bad";
  if (isHealthyLifecycle(state)) return "good";
  if (state.toUpperCase() === "ACTIVE") return "good";
  if (state.toUpperCase() === "TESTING") return "warn";
  return "dim";
}

function timeOrDash(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleTimeString();
}

function activeModelOf(p: ProviderEntry, data: ProviderListResponse | null): string | null {
  if (data && p.provider_id === data.active_provider) return data.active_model;
  return p.default_model;
}

/* ============================ shared modal shell ============================ */

function ModalShell({
  label,
  wide,
  onClose,
  children,
}: {
  label: string;
  wide?: boolean;
  onClose: () => void;
  children: React.ReactNode;
}) {
  const boxRef = useRef<HTMLDivElement | null>(null);
  useDialogA11y(boxRef, onClose);
  return (
    <div className="modal-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        ref={boxRef}
        className={classNames("modal", wide && "aipage-wizard")}
        role="dialog"
        aria-modal="true"
        aria-label={label}
      >
        {children}
      </div>
    </div>
  );
}

/* ======================= TEST MODEL dialog (per-provider) =================== */

function TestModelDialog({
  provider,
  onClose,
  onDone,
}: {
  provider: ProviderEntry;
  onClose: () => void;
  onDone: (msg: string) => void;
}) {
  const [model, setModel] = useState(provider.default_model ?? "");
  const [result, setResult] = useState<TestModelResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = useCallback(async () => {
    const trimmed = model.trim();
    if (!trimmed) {
      setError("enter a model id to probe");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await aiProvidersApi.testModel(provider.provider_id, trimmed);
      setResult(res);
      onDone(`test-model ${trimmed} on ${provider.provider_id}: ${res.passed ? "PASSED" : "FAILED"} @ ${res.stage}`);
    } catch (e) {
      setResult(null);
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }, [model, provider.provider_id, onDone]);

  return (
    <ModalShell label={`Test model on ${provider.provider_id}`} onClose={onClose}>
      <div className="modal-header">Test Model — {provider.provider_id}</div>
      <div className="modal-body">
        <p className="aipage-help">
          A NON-MUTATING probe: it exercises one model against SIMULATED TEST DATA and never changes the persisted
          default model. A provider can be healthy while a specific model is unavailable — that is why this is a
          separate control from Test Provider.
        </p>
        <div className="aipage-field">
          <label htmlFor="aip-tm-model">Model</label>
          <input
            id="aip-tm-model"
            type="text"
            value={model}
            placeholder={provider.default_model ?? "model id"}
            onChange={(e) => setModel(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && !busy && void run()}
          />
          <span className="hint">current default: {provider.default_model ?? "—"}</span>
        </div>
        {error && <div className="aipage-notice bad">{error}</div>}
        {result && <TestResultBlock result={result} />}
      </div>
      <div className="modal-actions">
        <button className="btn" disabled={busy} onClick={onClose}>
          Close <kbd>esc</kbd>
        </button>
        <button className="btn primary" disabled={busy || !model.trim()} onClick={() => void run()}>
          {busy ? "Testing model…" : "Run Test Model"}
        </button>
      </div>
    </ModalShell>
  );
}

/* ====================== CONFIGURE modal (per-provider) ====================== */

function ConfigureDialog({
  provider,
  onClose,
  onSaved,
}: {
  provider: ProviderEntry;
  onClose: () => void;
  onSaved: (msg: string) => void;
}) {
  const [name, setName] = useState(provider.provider_name);
  const [endpoint, setEndpoint] = useState(provider.endpoint);
  const [model, setModel] = useState(provider.default_model ?? "");
  const [timeoutMs, setTimeoutMs] = useState(String(provider.timeout ?? ""));
  const [retries, setRetries] = useState(String(provider.max_retries ?? ""));
  const [enabled, setEnabled] = useState(provider.enabled);
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const req: ProviderConfigRequest = { provider_name: name.trim() || undefined, endpoint: endpoint.trim() || undefined, model: model.trim() || undefined, enabled };
      const t = Number(timeoutMs);
      if (timeoutMs.trim() !== "" && Number.isFinite(t)) req.timeout = t;
      const r = Number(retries);
      if (retries.trim() !== "" && Number.isFinite(r)) req.max_retries = r;
      // The secret leaves the client only inside this request; nothing keeps it.
      if (secret !== "") req.api_key = secret;
      await aiProvidersApi.configure(provider.provider_id, req);
      onSaved(`configured ${provider.provider_id}${secret !== "" ? " — TOKEN CONFIGURED" : ""}`);
      setSecret("");
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }, [name, endpoint, model, timeoutMs, retries, enabled, secret, provider.provider_id, onSaved]);

  return (
    <ModalShell label={`Configure ${provider.provider_id}`} onClose={onClose}>
      <div className="modal-header">Configure — {provider.provider_id}</div>
      <div className="modal-body">
        <div className="aipage-grid2">
          <div className="aipage-field">
            <label htmlFor="aip-cf-name">Provider name</label>
            <input id="aip-cf-name" type="text" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="aipage-field">
            <label htmlFor="aip-cf-endpoint">Endpoint</label>
            <input
              id="aip-cf-endpoint"
              type="text"
              value={endpoint}
              placeholder="https://…"
              onChange={(e) => setEndpoint(e.target.value)}
            />
          </div>
          <div className="aipage-field">
            <label htmlFor="aip-cf-model">Default model</label>
            <input id="aip-cf-model" type="text" value={model} onChange={(e) => setModel(e.target.value)} />
          </div>
          <div className="aipage-field">
            <label htmlFor="aip-cf-timeout">Timeout (ms)</label>
            <input
              id="aip-cf-timeout"
              type="number"
              min={0}
              value={timeoutMs}
              onChange={(e) => setTimeoutMs(e.target.value)}
            />
          </div>
          <div className="aipage-field">
            <label htmlFor="aip-cf-retries">Max retries</label>
            <input
              id="aip-cf-retries"
              type="number"
              min={0}
              value={retries}
              onChange={(e) => setRetries(e.target.value)}
            />
          </div>
          <div className="aipage-field">
            <label htmlFor="aip-cf-enabled">Enabled</label>
            <select id="aip-cf-enabled" value={enabled ? "1" : "0"} onChange={(e) => setEnabled(e.target.value === "1")}>
              <option value="1">enabled</option>
              <option value="0">disabled</option>
            </select>
          </div>
        </div>
        <div className="aipage-field">
          <label htmlFor="aip-cf-secret">Secret (optional)</label>
          <input
            id="aip-cf-secret"
            type="password"
            autoComplete="off"
            value={secret}
            placeholder={provider.has_secret ? "stored — leave blank to keep" : "paste key, never displayed"}
            onChange={(e) => setSecret(e.target.value)}
          />
          <span className="hint">
            {provider.has_secret
              ? "TOKEN CONFIGURED — the stored value is never returned by the API."
              : "no secret configured"}
          </span>
        </div>
        {error && <div className="aipage-notice bad">{error}</div>}
      </div>
      <div className="modal-actions">
        <button className="btn" disabled={busy} onClick={onClose}>
          Cancel <kbd>esc</kbd>
        </button>
        <button className="btn primary" disabled={busy} onClick={() => void save()}>
          {busy ? "Saving…" : "Save configuration"}
        </button>
      </div>
    </ModalShell>
  );
}

/* ============================ test result block ============================ */

function TestResultBlock({ result }: { result: ProviderTestResult }) {
  return (
    <div className={classNames("aipage-test", result.passed ? "ok" : "bad")}>
      <div className="aipage-test-head">
        <strong>{result.provider_id}</strong>
        {result.model && <span className="aipage-mono">{result.model}</span>}
        <StatusBadge status={result.passed ? "ok" : "failed"} label={result.passed ? "PASSED" : "FAILED"} />
        <span className="aipage-mono">{result.stage}</span>
        <span>{Math.round(result.latency_ms)} ms</span>
        {result.is_test_data && <span className="aipage-chip warn">SIMULATED TEST DATA</span>}
      </div>
      <div className="aipage-mono aipage-detail">{result.detail}</div>
      {result.error && <div className="aipage-fail aipage-detail">{result.error}</div>}
      {result.normalized_response && (
        <div className="aipage-mono aipage-detail">
          {JSON.stringify(result.normalized_response.decision ?? result.normalized_response, null, 2)}
        </div>
      )}
      <div className="aipage-meta">
        <span>input tokens: {result.input_tokens ?? "—"}</span>
        <span>output tokens: {result.output_tokens ?? "—"}</span>
        <span>cost: {result.cost_estimate != null ? result.cost_estimate.toExponential(2) : "unavailable"}</span>
      </div>
    </div>
  );
}

/* ======================= ADD PROVIDER wizard (5 steps) ===================== */

interface WizardState {
  step: WizardStep;
  templateId: string | null;
  providerId: string;
  providerName: string;
  endpoint: string;
  secret: string;
  model: string;
  createdId: string | null;
  created: boolean;
  models: string[];
  discovering: boolean;
  testResult: ProviderTestResult | null;
  testing: boolean;
  switchRes: SwitchResponse | null;
  activating: boolean;
  error: string | null;
}

function AddProviderWizard({
  templates,
  templatesError,
  onClose,
  onCreated,
}: {
  templates: ProviderTemplate[];
  templatesError: string | null;
  onClose: () => void;
  onCreated: (msg: string) => void;
}) {
  const [state, setState] = useState<WizardState>({
    step: 1,
    templateId: null,
    providerId: "",
    providerName: "",
    endpoint: "",
    secret: "",
    model: "",
    createdId: null,
    created: false,
    models: [],
    discovering: false,
    testResult: null,
    testing: false,
    switchRes: null,
    activating: false,
    error: null,
  });

  const tpl = templates.find((t) => t.template_id === state.templateId) ?? null;

  const set = useCallback(<K extends keyof WizardState>(key: K, value: WizardState[K]) => {
    setState((s) => ({ ...s, [key]: value }));
  }, []);

  const pickTemplate = useCallback(
    (t: ProviderTemplate) => {
      setState((s) => ({
        ...s,
        templateId: t.template_id,
        endpoint: t.defaults.endpoint ?? "",
        model: t.defaults.default_model ?? "",
        error: null,
      }));
    },
    [],
  );

  /** STEP 2->3 boundary: register the provider. The secret goes to the secure
   *  store; the response never echoes it and this component drops its copy. */
  const create = useCallback(async () => {
    if (!state.templateId) return;
    set("error", null);
    set("created", false);
    try {
      const res = await aiProvidersApi.add({
        provider_id: state.providerId.trim(),
        template_id: state.templateId,
        provider_name: state.providerName.trim() || undefined,
        endpoint: state.endpoint.trim() || undefined,
        model: state.model.trim() || undefined,
        api_key: state.secret || undefined,
        enabled: true,
      });
      set("secret", ""); // never retained past the request
      set("createdId", res.provider_id);
      set("created", true);
      set("step", 3);
      onCreated(`provider ${res.provider_id} added — ${res.lifecycle_state} (${res.restart_requirement})`);
    } catch (e) {
      set("error", errText(e));
    }
  }, [state.templateId, state.providerId, state.providerName, state.endpoint, state.model, state.secret, set, onCreated]);

  /** STEP 3: DISCOVER MODELS — only when the template supports listing. */
  const discover = useCallback(async () => {
    if (!state.createdId) return;
    set("discovering", true);
    set("error", null);
    try {
      const names = await aiProvidersApi.modelNames(state.createdId);
      set("models", names);
      if (names.length === 0) set("error", "the adapter reported no models for this provider");
    } catch (e) {
      set("models", []);
      set("error", errText(e));
    } finally {
      set("discovering", false);
    }
  }, [state.createdId, set]);

  /** STEP 4: run the provider connectivity/auth/contract test. */
  const runTest = useCallback(async () => {
    if (!state.createdId) return;
    set("testing", true);
    set("error", null);
    try {
      const res = await aiProvidersApi.test(state.createdId);
      set("testResult", res);
      if (!res.passed) set("error", `test failed at ${res.stage}: ${res.detail}`);
    } catch (e) {
      set("testResult", null);
      set("error", errText(e));
    } finally {
      set("testing", false);
    }
  }, [state.createdId, set]);

  /** STEP 5: explicit activation. Never implied by creation. */
  const activate = useCallback(async () => {
    if (!state.createdId) return;
    set("activating", true);
    set("error", null);
    try {
      const res = await aiProvidersApi.switch({ primary: state.createdId });
      set("switchRes", res);
      if (res.switched) {
        onCreated(`activated ${res.active_provider} (${res.decision_mode})`);
      } else {
        set("error", "switch refused: " + failedPreconditions(res));
      }
    } catch (e) {
      set("switchRes", null);
      set("error", "switch refused: " + switchRefusalText(e));
    } finally {
      set("activating", false);
    }
  }, [state.createdId, set, onCreated]);

  const canCreate =
    !!state.templateId && state.providerId.trim().length > 0 && !state.created;

  return (
    <ModalShell label="Add provider" wide onClose={onClose}>
      <div className="modal-header">Add Provider</div>
      <div className="aipage-steps" aria-hidden="true">
        {WIZARD_STEPS.map((s) => (
          <span
            key={s.n}
            className={classNames(
              "aipage-step",
              state.step > s.n && "done",
              state.step === s.n && "current",
            )}
          >
            <span className="n">{s.n}</span>
            {s.label}
          </span>
        ))}
      </div>
      <div className="modal-body">
        {state.error && <div className="aipage-notice bad">{state.error}</div>}

        {/* STEP 1 — TEMPLATE ------------------------------------------------ */}
        {state.step === 1 && (
          <>
            <p className="aipage-help">
              Choose an adapter. The template decides which connection fields the provider needs and whether model
              listing is supported.
            </p>
            {templatesError && <div className="aipage-notice warn">templates unavailable: {templatesError}</div>}
            <div className="aipage-tpls" role="radiogroup" aria-label="provider template">
              {templates.map((t) => (
                <button
                  key={t.template_id}
                  type="button"
                  role="radio"
                  aria-checked={state.templateId === t.template_id}
                  className={classNames("aipage-tpl", state.templateId === t.template_id && "sel")}
                  onClick={() => pickTemplate(t)}
                >
                  <input type="radio" readOnly checked={state.templateId === t.template_id} tabIndex={-1} />
                  <span className="aipage-tpl-body">
                    <span className="aipage-tpl-name">{t.label}</span>
                    <br />
                    <span className="aipage-tpl-sub">
                      {t.template_id} · {t.adapter_type}
                      {t.supports_model_listing ? " · model listing" : " · no model listing"}
                    </span>
                    {t.capabilities.length > 0 && (
                      <span className="aipage-caps" style={{ marginTop: 5 }}>
                        {t.capabilities.map((c) => (
                          <span key={c} className="aipage-cap">
                            {c}
                          </span>
                        ))}
                      </span>
                    )}
                  </span>
                </button>
              ))}
            </div>
            <div className="aipage-field" style={{ marginTop: 12 }}>
              <label htmlFor="aip-wz-id">Provider id</label>
              <input
                id="aip-wz-id"
                type="text"
                value={state.providerId}
                placeholder="my-openrouter"
                onChange={(e) => set("providerId", e.target.value)}
              />
              <span className="hint">a lowercase id the registry will know this provider by</span>
            </div>
          </>
        )}

        {/* STEP 2 — CONNECTION --------------------------------------------- */}
        {state.step === 2 && (
          <>
            <p className="aipage-help">
              {tpl ? `Adapter ${tpl.adapter_type} · auth ${tpl.defaults.auth_type}` : "Connection"}
            </p>
            <div className="aipage-grid2">
              <div className="aipage-field">
                <label htmlFor="aip-wz-name">Display name</label>
                <input
                  id="aip-wz-name"
                  type="text"
                  value={state.providerName}
                  placeholder={tpl?.label ?? ""}
                  onChange={(e) => set("providerName", e.target.value)}
                />
              </div>
              <div className="aipage-field">
                <label htmlFor="aip-wz-endpoint">Endpoint</label>
                <input
                  id="aip-wz-endpoint"
                  type="text"
                  value={state.endpoint}
                  placeholder="https://…"
                  onChange={(e) => set("endpoint", e.target.value)}
                />
              </div>
            </div>
            <div className="aipage-field">
              <label htmlFor="aip-wz-secret">Secret</label>
              <input
                id="aip-wz-secret"
                type="password"
                autoComplete="off"
                value={state.secret}
                placeholder="paste key — never displayed or echoed back"
                onChange={(e) => set("secret", e.target.value)}
              />
              <span className="hint">
                stored in the secure store; the response carries no key material and this field is cleared on save
              </span>
            </div>
          </>
        )}

        {/* STEP 3 — MODEL -------------------------------------------------- */}
        {state.step === 3 && (
          <>
            <p className="aipage-help">
              {state.created
                ? `Provider ${state.createdId} is registered. Set its default model, or discover what the adapter exposes.`
                : "Register the provider to configure its model."}
            </p>
            <div className="aipage-field">
              <label htmlFor="aip-wz-model">Model</label>
              <input
                id="aip-wz-model"
                type="text"
                value={state.model}
                placeholder="model id"
                onChange={(e) => set("model", e.target.value)}
              />
            </div>
            {tpl?.supports_model_listing ? (
              <>
                <div className="aipage-actions">
                  <button
                    type="button"
                    className="btn small"
                    disabled={!state.createdId || state.discovering}
                    onClick={() => void discover()}
                  >
                    {state.discovering ? "Discovering…" : "Discover Models"}
                  </button>
                </div>
                {state.models.length > 0 && (
                  <div className="aipage-modelist" style={{ marginTop: 8 }}>
                    {state.models.map((m) => (
                      <button
                        key={m}
                        type="button"
                        className={classNames(state.model === m && "sel")}
                        onClick={() => set("model", m)}
                      >
                        <span aria-hidden="true">{state.model === m ? "◉" : "○"}</span>
                        {m}
                      </button>
                    ))}
                  </div>
                )}
              </>
            ) : (
              <p className="aipage-help">This adapter does not support model listing — type the model id.</p>
            )}
          </>
        )}

        {/* STEP 4 — TEST --------------------------------------------------- */}
        {state.step === 4 && (
          <>
            <p className="aipage-help">
              Run the provider connectivity, authentication and contract test against SIMULATED TEST DATA. This places
              no order.
            </p>
            <div className="aipage-actions">
              <button
                type="button"
                className="btn small primary"
                disabled={!state.createdId || state.testing}
                onClick={() => void runTest()}
              >
                {state.testing ? "Testing…" : "Run Test"}
              </button>
            </div>
            {state.testResult && (
              <div className="aipage-wiztest">
                <TestResultBlock result={state.testResult} />
              </div>
            )}
          </>
        )}

        {/* STEP 5 — ACTIVATE ----------------------------------------------- */}
        {state.step === 5 && (
          <>
            <p className="aipage-help">
              Activation is an explicit operator action — a created provider never becomes active on its own. Failed
              preconditions are listed verbatim; nothing is forced past them.
            </p>
            <div className="aipage-actions">
              <button
                type="button"
                className="btn small primary"
                disabled={!state.createdId || state.activating}
                onClick={() => void activate()}
              >
                {state.activating ? "Activating…" : `Activate ${state.createdId ?? ""}`}
              </button>
            </div>
            {state.switchRes && (
              <div className="aipage-wiztest">
                <div className={classNames("aipage-notice", state.switchRes.switched ? "ok" : "bad")}>
                  {state.switchRes.switched
                    ? `switched to ${state.switchRes.active_provider} · mode ${state.switchRes.decision_mode}` +
                      (state.switchRes.restart_required ? " — restart required to activate." : "")
                    : "switch refused"}
                </div>
                {state.switchRes.preconditions.length > 0 && (
                  <div className="aipage-preconditions">
                    {state.switchRes.preconditions.map((p, i) => (
                      <div key={i} className={classNames("aipage-pre", p.passed ? "pass" : "fail")}>
                        <span className="mark">{p.passed ? "✓" : "✕"}</span>
                        <span>{p.check}: {p.detail}</span>
                      </div>
                    ))}
                  </div>
                )}
                {state.switchRes.warnings.length > 0 && (
                  <div className="aipage-notice warn" style={{ marginTop: 8 }}>
                    {state.switchRes.warnings.join("; ")}
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </div>
      <div className="modal-actions">
        <button className="btn" onClick={onClose}>
          {state.step === 5 ? "Done" : "Close"} <kbd>esc</kbd>
        </button>
        {state.step === 1 && (
          <button className="btn primary" disabled={!canCreate} onClick={() => set("step", 2)}>
            Next: Connection
          </button>
        )}
        {state.step === 2 && (
          <button className="btn primary" disabled={!canCreate} onClick={() => (state.created ? set("step", 3) : void create())}>
            {state.created ? "Next: Model" : "Create provider"}
          </button>
        )}
        {state.step === 3 && (
          <button className="btn primary" disabled={!state.createdId} onClick={() => set("step", 4)}>
            Next: Test
          </button>
        )}
        {state.step === 4 && (
          <button className="btn primary" disabled={!state.createdId} onClick={() => set("step", 5)}>
            Next: Activate
          </button>
        )}
      </div>
    </ModalShell>
  );
}

/* ================================ THE PAGE ================================= */

export default function AIProvidersPage(_props: ShellPageProps) {
  const [data, setData] = useState<ProviderListResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [test, setTest] = useState<ProviderTestResult | null>(null);
  const [compare, setCompare] = useState<CompareResponse | null>(null);
  const [trace, setTrace] = useState<DecisionRecord | null>(null);
  const [history, setHistory] = useState<DecisionRecord[]>([]);
  const [historySource, setHistorySource] = useState<"durable" | "in_memory">("in_memory");
  const [snapshot, setSnapshot] = useState<Record<string, unknown> | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [noticeTone, setNoticeTone] = useState<"ok" | "warn" | "bad" | null>(null);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [templates, setTemplates] = useState<ProviderTemplate[]>([]);
  const [templatesError, setTemplatesError] = useState<string | null>(null);
  const [testModelFor, setTestModelFor] = useState<ProviderEntry | null>(null);
  const [configFor, setConfigFor] = useState<ProviderEntry | null>(null);
  /** Models discovered for a provider via GET /providers/{id}/models — shown so
   *  DISCOVER MODELS has a visible result, not just a transient notice. */
  const [discoveredFor, setDiscoveredFor] = useState<string | null>(null);
  const [discovered, setDiscovered] = useState<string[]>([]);

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const res = await aiProvidersApi.list(signal);
      setData(res);
      setError(null);
    } catch (e) {
      if (!signal) setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  /** Decision history — the durable ledger when the box persists decisions
   *  (Section 63), the in-memory ring otherwise. Loaded alongside the provider
   *  list so the panel is never stale after a switch or evaluate. */
  const loadHistory = useCallback(
    async (signal?: AbortSignal) => {
      try {
        const res = await aiProvidersApi.decisions(15, signal);
        setHistory(res.decisions ?? []);
        setHistorySource(res.source ?? "in_memory");
      } catch {
        // History is a convenience: a failed read must not block the page.
        setHistory([]);
      }
    },
    [],
  );

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    void loadHistory(controller.signal);
    // Poll GET / every 5s (existing behaviour). A poll ONLY re-reads state —
    // it never triggers a network test; tests are explicit button clicks.
    const timer = window.setInterval(() => void load(), REFRESH_MS);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [load, loadHistory]);

  const act = data;
  const providers = data?.providers ?? [];
  const rollback = useMemo(() => rollbackHint(act?.active_provider), [act?.active_provider]);

  /** Templates are loaded once when the wizard opens (the registry is small
   *  and stable; no reason to fetch it on every render). */
  const openWizard = useCallback(async () => {
    setWizardOpen(true);
    if (templates.length === 0 && !templatesError) {
      try {
        const res = await aiProvidersApi.templates();
        setTemplates(res.templates ?? []);
        setTemplatesError(null);
      } catch (e) {
        setTemplatesError(e instanceof Error ? e.message : String(e));
      }
    }
  }, [templates.length, templatesError]);

  const say = useCallback((tone: "ok" | "warn" | "bad", msg: string) => {
    setNotice(msg);
    setNoticeTone(tone);
  }, []);

  /** Run the Test Center for one provider (Section 22). A TEST, never a trade. */
  const runTest = useCallback(
    async (providerId: string) => {
      setBusy(providerId);
      setNotice(null);
      try {
        const res = await aiProvidersApi.test(providerId);
        setTest(res);
        say(res.passed ? "ok" : "bad", `test ${providerId}: ${res.passed ? "PASSED" : "FAILED"} @ ${res.stage}`);
        // A test records last_test/latency backend-side, so re-read the list.
        await load();
      } catch (e) {
        setTest(null);
        say("bad", errText(e));
      } finally {
        setBusy(null);
      }
    },
    [load, say],
  );

  /** Discover models for an existing provider (contract #8). */
  const discoverModels = useCallback(
    async (providerId: string) => {
      setBusy(`models:${providerId}`);
      setNotice(null);
      try {
        const names = await aiProvidersApi.modelNames(providerId);
        setDiscoveredFor(providerId);
        setDiscovered(names);
        setNotice(names.length > 0 ? `${providerId}: ${names.length} models — ${names.slice(0, 4).join(", ")}${names.length > 4 ? "…" : ""}` : `${providerId}: the adapter reported no models`);
        setNoticeTone(names.length > 0 ? "ok" : "warn");
        await load();
      } catch (e) {
        setDiscoveredFor(providerId);
        setDiscovered([]);
        say("bad", errText(e));
      } finally {
        setBusy(null);
      }
    },
    [load, say],
  );

  /** Evaluate the SIMULATED sample snapshot (Sections 22, 56). */
  const evaluate = useCallback(async () => {
    setBusy("evaluate");
    setNotice(null);
    try {
      const res = await aiProvidersApi.evaluate({ simulated: true });
      setSnapshot(res.request ?? null);
      setTrace(res.decision);
      say("ok", `evaluated sample snapshot — ${res.decision.final_action}`);
    } catch (e) {
      say("bad", errText(e));
    } finally {
      setBusy(null);
    }
  }, [say]);

  /** Side-by-side comparison of every enabled provider (Section 24). */
  const runCompare = useCallback(async () => {
    setBusy("compare");
    setNotice(null);
    try {
      setCompare(await aiProvidersApi.compare({ simulated: true }));
    } catch (e) {
      say("bad", errText(e));
    } finally {
      setBusy(null);
    }
  }, [say]);

  /** The explicit switch flow with preconditions (Section 26). */
  const switchTo = useCallback(
    async (primary: string, mode = "HYBRID") => {
      setBusy(`switch:${primary}`);
      setNotice(null);
      try {
        const res = await aiProvidersApi.switch({ primary, mode: mode as never });
        if (res.switched) {
          say(
            "ok",
            `Switched to ${res.active_provider} (${res.decision_mode})` +
              (res.restart_required ? " — restart required to activate." : ""),
          );
        } else {
          say(
            "warn",
            "Switch refused: " + failedPreconditions(res),
          );
        }
        await load();
      } catch (e) {
        say("bad", errText(e));
      } finally {
        setBusy(null);
      }
    },
    [load, say],
  );

  /** Deactivate the current external provider — the backend routes back to the
   *  internal model and stands the old provider down (contract #10). */
  const deactivate = useCallback(async () => {
    setBusy("deactivate");
    setNotice(null);
    try {
      const res = await aiProvidersApi.deactivate();
      say("ok", `deactivated — ${res.note}`);
      await load();
    } catch (e) {
      say("bad", errText(e));
    } finally {
      setBusy(null);
    }
  }, [load, say]);

  /** Restore the previous activation (contract #11). A 412 means there is
   *  nothing to roll back to and is surfaced verbatim. */
  const rollbackTo = useCallback(async () => {
    setBusy("rollback");
    setNotice(null);
    try {
      const res = await aiProvidersApi.rollback();
      say("ok", `rollback — ${res.note} (active: ${res.active_provider})`);
      await load();
    } catch (e) {
      say("warn", errText(e));
      await load();
    } finally {
      setBusy(null);
    }
  }, [load, say]);

  /** Remove a custom provider. Built-ins are protected by the backend (400
   *  detail) and that refusal is shown verbatim. */
  const remove = useCallback(
    async (providerId: string) => {
      setBusy(`remove:${providerId}`);
      setNotice(null);
      try {
        const res = await aiProvidersApi.remove(providerId);
        say(res.removed ? "ok" : "warn", `${providerId}: ${res.status} (removed: ${res.removed})`);
        await load();
      } catch (e) {
        say("bad", errText(e));
      } finally {
        setBusy(null);
      }
    },
    [load, say],
  );

  /** Enable/disable a provider (contract #12). */
  const toggleEnabled = useCallback(
    async (p: ProviderEntry) => {
      setBusy(`toggle:${p.provider_id}`);
      setNotice(null);
      try {
        if (p.enabled) await aiProvidersApi.disable(p.provider_id);
        else await aiProvidersApi.enable(p.provider_id);
        say("ok", `${p.provider_id}: ${p.enabled ? "disabled" : "enabled"}`);
        await load();
      } catch (e) {
        say("bad", errText(e));
      } finally {
        setBusy(null);
      }
    },
    [load, say],
  );

  /** Probabilities from the trace, for the evidence bars. */
  const evidenceBars = useMemo(() => {
    if (!trace) return [];
    return Object.entries(trace.evidence).flatMap(([providerId, ev]) => {
      const p = ev as { p_hold?: number; p_close?: number; action?: string };
      return [
        { label: `${providerId} hold`, value: p.p_hold ?? null, tone: "flat" as const },
        { label: `${providerId} close`, value: p.p_close ?? null, tone: "sell" as const },
      ];
    });
  }, [trace]);

  /** The most recent decision that used a fallback — drives the hero badge. */
  const lastFallback = useMemo(() => {
    if (trace?.fallback_used) return trace;
    for (const d of history) if (d.fallback_used) return d;
    return null;
  }, [trace, history]);

  if (error) return <ErrorState message={error} onRetry={() => void load()} />;
  if (!data) return <LoadingState label="Loading AI providers…" />;

  return (
    <div className="aipage">
      {/* ---- hero: kicker -> glyph + gradient title -> desc -> chips -------- */}
      <section className="aipage-hero" aria-labelledby="aipage-h1">
        <div className="aipage-hero-main">
          <div className="aipage-kicker">
            <span className="aipage-kicker-dot" aria-hidden="true" />
            SAFETY &amp; GOVERNANCE · SWITCHABLE PROVIDER RUNTIME
            <span className="aipage-kicker-rule" aria-hidden="true" />
          </div>
          <h1 className="aipage-title" id="aipage-h1">
            <span className="glyph" aria-hidden="true">
              ◈
            </span>
            <span className="word">AI Providers</span>
          </h1>
          <p className="aipage-desc">
            Operational control plane for the switchable provider ecosystem — registry, lifecycle and health, add /
            test / discover / activate / deactivate / rollback. Every state below is served by the backend; tests run
            on SIMULATED TEST DATA and never place an order.
          </p>
          <div className="aipage-chips">
            {ENDPOINTS.map((ep) => (
              <span className="aipage-chip2" key={ep} title={`provenance — backend endpoint ${ep}`}>
                <span className="dot" aria-hidden="true" />
                {ep}
              </span>
            ))}
          </div>
        </div>
        <div className="aipage-hero-side">
          <div className="aipage-source" role="status">
            <span className="k">runtime source</span>
            <span className="v">{act?.active_provider ?? "—"}</span>
            <span className="sub">
              {act?.active_model ? `model ${act.active_model}` : "no model"} · {act?.decision_mode ?? "—"}
            </span>
          </div>
          {lastFallback && (
            <span className="aipage-fallback" title={lastFallback.fallback_reason || "a fallback was used"}>
              FALLBACK ACTIVE{lastFallback.fallback_reason ? ` — ${lastFallback.fallback_reason}` : ""}
            </span>
          )}
        </div>
      </section>

      {/* ---- header: what is ACTIVE right now (Section 53) ------------------ */}
      <Panel
        title="Activation"
        right={
          <div className="aipage-actions">
            <button
              type="button"
              className="btn small primary"
              disabled={busy === "deactivate" || !act?.active_provider}
              onClick={() => void deactivate()}
              title="POST /api/ai-providers/deactivate — route back to the internal model"
            >
              {busy === "deactivate" ? "Deactivating…" : "Deactivate"}
            </button>
            <button
              type="button"
              className="btn small"
              disabled={busy === "rollback" || !rollback.available}
              onClick={() => void rollbackTo()}
              title={
                rollback.available
                  ? "POST /api/ai-providers/rollback — restore the previous activation"
                  : `rollback unavailable — ${rollback.reason}`
              }
            >
              {busy === "rollback" ? "Rolling back…" : "Rollback"}
            </button>
          </div>
        }
      >
        <div className="aipage-active">
          <MetricCard label="Active Provider" value={act?.active_provider ?? "—"} />
          <MetricCard label="Decision Mode" value={act?.decision_mode ?? "—"} />
          <MetricCard label="Fallback" value={act?.fallback_provider ?? "none"} />
          <MetricCard label="Shadow" value={act?.shadow_provider ?? "none"} />
          <MetricCard label="Contract" value={data.contract_version} />
          <MetricCard label="Policy" value={data.policy_version} />
        </div>
        {notice && (
          <div className={classNames("aipage-notice", noticeTone ?? undefined)} role="status">
            {notice}
          </div>
        )}
      </Panel>

      {/* ---- provider list (Section 19) ------------------------------------- */}
      <Panel
        title="Providers"
        subtitle={
          <span>
            lifecycle:&nbsp;{LIFECYCLE_STATES.join(" · ")}
          </span>
        }
        right={
          <button
            type="button"
            className="btn small primary"
            disabled={busy === "add"}
            onClick={() => {
              setBusy("add");
              void openWizard().finally(() => setBusy(null));
            }}
            title="POST /api/ai-providers/providers — register a provider from a template"
          >
            {busy === "add" ? "Opening…" : "Add Provider"}
          </button>
        }
      >
        <table className="aipage-table">
          <thead>
            <tr>
              <th />
              <th>Provider</th>
              <th>Type</th>
              <th>Model</th>
              <th>Lifecycle</th>
              <th>State</th>
              <th>Reachable</th>
              <th>Last test</th>
              <th>Latency</th>
              <th>Failures</th>
              <th>Breaker</th>
              <th>Capabilities</th>
              <th>Token</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {providers.map((p) => {
              const isActive = p.provider_id === act?.active_provider;
              const reach = reachableWord(p);
              return (
                <tr key={p.provider_id}>
                  <td>
                    <span className={classNames("aipage-dot", healthClass(p))} />
                  </td>
                  <td>
                    <strong>{p.provider_name}</strong>
                    {isActive && <span className="aipage-chip">PRIMARY</span>}
                    {!p.enabled && <span className="aipage-chip">INACTIVE</span>}
                    <br />
                    <span className="aipage-mono">{p.provider_id}</span>
                  </td>
                  <td className="aipage-mono">{p.template_id ?? p.type}</td>
                  <td className="aipage-mono">{activeModelOf(p, data) ?? "—"}</td>
                  <td>
                    <span className={classNames("aipage-lc", lifecycleTone(p.lifecycle_state))}>
                      <span className="dot" aria-hidden="true" />
                      {p.lifecycle_state ?? "UNKNOWN"}
                    </span>
                  </td>
                  <td>
                    <StatusBadge
                      status={isActive ? "ACTIVE" : "INACTIVE"}
                      label={isActive ? "ACTIVE — the runtime source" : p.enabled ? "enabled, not active" : "disabled"}
                    />
                  </td>
                  <td className={classNames("aipage-mono", reach.tone === "bad" && "aipage-fail")}>{reach.word}</td>
                  <td className="aipage-mono">{timeOrDash(p.last_test)}</td>
                  <td>{p.latency_ms != null ? `${Math.round(p.latency_ms)} ms` : "—"}</td>
                  <td className={p.failure_count > 0 ? "aipage-fail" : undefined}>{p.failure_count}</td>
                  <td>
                    <StatusBadge status={p.circuit_breaker_state ?? null} label={p.circuit_breaker_state ?? "—"} />
                  </td>
                  <td>
                    {p.capabilities.length > 0 ? (
                      <span className="aipage-caps">
                        {p.capabilities.map((c) => (
                          <span key={c} className="aipage-cap">
                            {c}
                          </span>
                        ))}
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td>
                    <span className={classNames("aipage-token", p.has_secret ? "set" : "missing")}>
                      <span className="dot" aria-hidden="true" />
                      {p.has_secret ? "set" : "missing"}
                    </span>
                  </td>
                  <td>
                    <div className="aipage-actions">
                      <button
                        type="button"
                        disabled={busy === p.provider_id}
                        onClick={() => void runTest(p.provider_id)}
                      >
                        {busy === p.provider_id ? "Testing…" : "Test"}
                      </button>
                      <button
                        type="button"
                        disabled={busy === `tm:${p.provider_id}`}
                        onClick={() => setTestModelFor(p)}
                      >
                        Test Model
                      </button>
                      <button
                        type="button"
                        disabled={busy === `models:${p.provider_id}`}
                        onClick={() => void discoverModels(p.provider_id)}
                        title="GET /api/ai-providers/providers/{id}/models"
                      >
                        {busy === `models:${p.provider_id}` ? "Listing…" : "Discover"}
                      </button>
                      <button type="button" onClick={() => setConfigFor(p)}>
                        Configure
                      </button>
                      <button
                        type="button"
                        disabled={busy === `switch:${p.provider_id}` || isActive}
                        onClick={() => void switchTo(p.provider_id)}
                      >
                        Activate
                      </button>
                      <button
                        type="button"
                        disabled={busy === `toggle:${p.provider_id}` || isActive}
                        onClick={() => void toggleEnabled(p)}
                      >
                        {p.enabled ? "Disable" : "Enable"}
                      </button>
                      <button
                        type="button"
                        className="danger"
                        disabled={busy === `remove:${p.provider_id}` || isActive}
                        onClick={() => void remove(p.provider_id)}
                        title="DELETE /api/ai-providers/providers/{id} — custom providers only"
                      >
                        Remove
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {discoveredFor && (
          <div className="aipage-test" style={{ marginTop: 10 }}>
            <div className="aipage-test-head">
              <strong>Discovered models — {discoveredFor}</strong>
              <StatusBadge
                status={discovered.length > 0 ? "ok" : "unavailable"}
                label={discovered.length > 0 ? `${discovered.length} models` : "no models reported"}
              />
            </div>
            {discovered.length > 0 ? (
              <span className="aipage-caps">
                {discovered.map((m) => (
                  <span className="aipage-cap" key={m}>
                    {m}
                  </span>
                ))}
              </span>
            ) : (
              <p className="aipage-help">
                The adapter reported an empty list — model listing either is unsupported for this template or the
                provider returned nothing.
              </p>
            )}
          </div>
        )}
      </Panel>

      {/* ---- Test Centre (Section 22) --------------------------------------- */}
      <Panel title="Test Centre">
        <p className="aipage-help">
          Tests run against SIMULATED TEST DATA and never place an order. A Test Result is not a live trading
          decision.
        </p>
        <div className="aipage-actions">
          <button type="button" disabled={busy === "evaluate"} onClick={() => void evaluate()}>
            {busy === "evaluate" ? "Evaluating…" : "Evaluate Sample Snapshot"}
          </button>
          <button type="button" disabled={busy === "compare"} onClick={() => void runCompare()}>
            {busy === "compare" ? "Comparing…" : "Compare Providers"}
          </button>
        </div>

        {test && <TestResultBlock result={test} />}

        {compare && (
          <table className="aipage-table" style={{ marginTop: 10 }}>
            <thead>
              <tr>
                <th>Provider</th>
                <th>Model</th>
                <th>Action</th>
                <th>p(hold)</th>
                <th>p(close)</th>
                <th>Latency</th>
                <th>Error</th>
              </tr>
            </thead>
            <tbody>
              {compare.providers.map((r) => (
                <tr key={r.provider_id}>
                  <td>{r.provider_id}</td>
                  <td className="aipage-mono">{r.model ?? "—"}</td>
                  <td>
                    <span className={classNames("aipage-chip", r.action?.toLowerCase())}>
                      {r.action ?? (r.ok ? "—" : "FAILED")}
                    </span>
                  </td>
                  <td>{r.p_hold != null ? r.p_hold.toFixed(3) : "—"}</td>
                  <td>{r.p_close != null ? r.p_close.toFixed(3) : "—"}</td>
                  <td>{r.latency_ms != null ? `${Math.round(r.latency_ms)} ms` : "—"}</td>
                  <td className="aipage-mono">{r.error_category ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>

      {/* ---- pipeline + decision trace (Sections 23, 41) -------------------- */}
      <Panel title="Decision Pipeline">
        <ol className="aipage-pipeline">
          {PIPELINE.map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ol>
        {trace && (
          <div className="aipage-trace">
            <div className="aipage-test-head">
              <strong>Policy Decision</strong>
              <span className={classNames("aipage-chip", trace.final_action.toLowerCase())}>{trace.final_action}</span>
              <StatusBadge
                status={trace.risk.allowed ? "allowed" : "restricted"}
                label={trace.risk.allowed ? "Risk: allowed" : "Risk: restricted"}
              />
              {trace.fallback_used && (
                <StatusBadge status="warn" label={`FALLBACK USED — ${trace.fallback_reason}`} />
              )}
            </div>
            <ProbBar rows={evidenceBars} />
            <div className="aipage-meta">
              <span>decision {trace.decision_id}</span>
              <span>template {trace.versions.template}</span>
              <span>policy {trace.versions.policy}</span>
              <span>gate {trace.versions.gate}</span>
              <span>{Math.round(trace.latency_ms)} ms</span>
              {trace.is_test_data && <span className="aipage-chip warn">SIMULATED TEST DATA</span>}
            </div>
            <div className="aipage-meta">
              {trace.providers_used.map((p) => (
                <span key={p}>used: {p}</span>
              ))}
              {trace.providers_failed.map((p) => (
                <span key={p} className="aipage-fail">
                  failed: {p}
                </span>
              ))}
            </div>
            <div className="aipage-scores">
              {Object.entries(trace.policy.scores).map(([action, score]) => (
                <span key={action} className="aipage-mono">
                  {action}: {score.toFixed(4)}
                </span>
              ))}
            </div>
            {trace.risk.rejections.length > 0 && (
              <div className="aipage-fail">gate: {trace.risk.rejections.join(", ")}</div>
            )}
          </div>
        )}
        {snapshot && (
          <details className="aipage-snapshot">
            <summary>Input snapshot</summary>
            <pre className="aipage-mono">{JSON.stringify(snapshot, null, 2)}</pre>
          </details>
        )}
      </Panel>

      {/* ---- decision history (Sections 23, 63) ---------------------------- */}
      <Panel
        title="Decision History"
        subtitle={
          historySource === "durable"
            ? "Persisted (SQLite / PostgreSQL) — survives restarts"
            : "In memory only — not persisted across restarts"
        }
      >
        {history.length === 0 ? (
          <p className="aipage-help">
            No decisions recorded yet. Run the Test Centre above or let the engine decide a position; every final
            decision is written here.
          </p>
        ) : (
          <table className="aipage-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Action</th>
                <th>Providers</th>
                <th>Failed</th>
                <th>Fallback</th>
                <th>Risk</th>
                <th>Latency</th>
                <th>Data</th>
              </tr>
            </thead>
            <tbody>
              {history.map((d) => (
                <tr
                  key={d.decision_id}
                  className="aipage-row"
                  onClick={() => setTrace(d)}
                  title="Click to load this trace into the Decision Pipeline panel"
                >
                  <td className="aipage-mono">{timeOrDash(d.created_at)}</td>
                  <td>
                    <span className={classNames("aipage-chip", d.final_action.toLowerCase())}>{d.final_action}</span>
                  </td>
                  <td className="aipage-mono">{d.providers_used.join(", ") || "—"}</td>
                  <td className="aipage-fail">{d.providers_failed.length > 0 ? d.providers_failed.join(", ") : "—"}</td>
                  <td>{d.fallback_used ? d.fallback_reason || "yes" : "—"}</td>
                  <td>
                    <StatusBadge
                      status={d.risk.allowed ? "allowed" : "restricted"}
                      label={d.risk.allowed ? "allowed" : "restricted"}
                    />
                  </td>
                  <td>{Math.round(d.latency_ms)} ms</td>
                  <td>
                    {d.is_test_data ? (
                      <span className="aipage-chip warn">SIMULATED</span>
                    ) : (
                      <span className="aipage-chip">live</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>

      {/* ---- overlays ------------------------------------------------------- */}
      {wizardOpen && (
        <AddProviderWizard
          templates={templates}
          templatesError={templatesError}
          onClose={() => {
            setWizardOpen(false);
            void load();
          }}
          onCreated={(msg) => {
            say("ok", msg);
            void load();
          }}
        />
      )}
      {testModelFor && (
        <TestModelDialog
          provider={testModelFor}
          onClose={() => setTestModelFor(null)}
          onDone={(msg) => say("ok", msg)}
        />
      )}
      {configFor && (
        <ConfigureDialog
          provider={configFor}
          onClose={() => setConfigFor(null)}
          onSaved={(msg) => {
            say("ok", msg);
            void load();
          }}
        />
      )}
    </div>
  );
}
