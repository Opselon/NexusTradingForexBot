/**
 * features/provisioning/ui/ProvisioningPage.tsx
 *
 * First-Run Model Preparation — the new-web replacement for
 * Web/first_setup.html ("Nexus First Setup — Model Preparation").
 *
 * What this panel IS: the operator surface for preparing a servable model
 * before the engine runs — environment discovery, optional stack install,
 * official-model download, and a local training run with a live event tail.
 *
 * Design rules carried over from the legacy page and the backend contract:
 *  - Environment GET is DISCOVERY ONLY (BUG-301). Installing is the explicit
 *    POST /environment/install action with operator consent.
 *  - Server responses are typed: failures arrive as {success:false, code,
 *    message?, detail?, remedy?}. The UI renders the code + detail + remedy
 *    VERBATIM and never fabricates a message or shows a stack trace.
 *  - One local training run at a time; cancel is observed at the next epoch
 *    boundary, not instantly.
 *
 * Provisioning wave fixes encoded in this rewrite (contract §6):
 *  - BUG-1: `recommended` is an OBJECT — consumed via recommendedActionOf()
 *    plus the static RECOMMENDED_COPY sentence map; never String(rec).
 *  - BUG-2: slot facts are toned per KEY via slotTone(); descriptive values
 *    (path/sha256/detail) render as plain text — healthy facts never red.
 *  - BUG-3: on mount we probe train/progress(0) and ADOPT an active run
 *    (event tail resumes, elapsed timer starts from the first event ts).
 *  - BUG-4: the result panel renders only a non-empty result — idle progress
 *    answers carry result:{} and null/{}/empty are never rendered.
 *  - GAP-5: the environment card renders the FULL server checks[] checklist
 *    (.pv-checks: stage, OK/FAIL badge, detail, remedy on FAIL) — no
 *    hardcoded 4-row summary.
 *  - GAP-6: a dataset browser (.pv-ds) reads /api/provisioning/datasets and
 *    fills the path input on click (input stays manually editable; endpoint
 *    failure shows an honest .pv-ds-empty state, never a fabricated list).
 *
 * Presentation: shared Panel/Segmented primitives + ./provisioning.css.
 * Markup uses ONLY the frozen class inventory of the wave contract: the
 * existing .pv-* set, the new .pv-hero/.pv-checks/.pv-fact/.pv-stepper/
 * .pv-meta/.pv-elapsed/.pv-hint/.pv-dismiss/.pv-ds family/.pv-section, the
 * primitives, and the shared .kv-row/.badge/.banner/.btn/.input/.inline-mono/
 * .muted/.small/.faint/.uppercase/.font-bold vocabulary (theme tokens only,
 * logical properties live in the css lane — this lane never edits the css).
 */

import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";

import type { ShellPageProps } from "@/app/featureModule";
import { EmptyState, ErrorState, LoadingState, Panel, Segmented } from "@/components/primitives";
import { useI18n } from "@/stores/i18nStore";
import { provisioningApi } from "../api";
import type {
  DatasetRoot,
  EnvCheck,
  EnvironmentReport,
  ProgressEvent,
  ProvisioningEnvironmentResponse,
  ProvisioningStatusResponse,
  TrainBackend,
  TrainSource,
} from "../model";
import { TRAIN_BACKENDS, TRAIN_SOURCES } from "../model";
import {
  CANDLE_OPTIONS,
  classNames,
  codeOf,
  errText,
  fmtBytes,
  fmtDuration,
  fmtValue,
  recommendedActionOf,
  slotTone,
  type SlotTone,
} from "./kit";
import "./provisioning.css";

/** Cadence of the active-run tail poll and the elapsed-time tick. */
const POLL_MS = 2000;
const TICK_MS = 1000;

/** Server-side validation bounds — mirrored client-side (contract §6.4). */
const FOLD_MIN = 1;
const FOLD_MAX = 1000;
const EPOCH_MIN = 1;
const EPOCH_MAX = 1000;
const CANDLE_MIN = 3000;
const CANDLE_MAX = 100000;

/** Endpoints this page talks to — one hero provenance chip each (contract §5). */
const ENDPOINTS: Array<{ mode: "GET" | "POST"; path: string }> = [
  { mode: "GET", path: "/api/provisioning/status" },
  { mode: "GET", path: "/api/provisioning/environment" },
  { mode: "POST", path: "/api/provisioning/environment/install" },
  { mode: "POST", path: "/api/provisioning/official" },
  { mode: "POST", path: "/api/provisioning/train/start" },
  { mode: "GET", path: "/api/provisioning/train/progress" },
  { mode: "POST", path: "/api/provisioning/train/cancel" },
  { mode: "GET", path: "/api/provisioning/datasets" },
];

/**
 * Static UI copy per recommended action (contract §6.2). The payload's own
 * reason/detail text always renders VERBATIM alongside this sentence; only
 * the human sentence is authored here. "none" is never shown (guarded).
 */
/** Minimal t() signature — also passed into module-level row components. */
type TranslationFn = (key: string, fallback: string, vars?: Record<string, string | number>) => string;

/** The three stepper stages of a run (contract §6.5), in pipeline order. */
const RUN_STAGES = ["environment", "dataset", "train"] as const;
type RunStageId = (typeof RUN_STAGES)[number];

/** Server event stage -> stepper bucket; unknown stages stay log-only. */
function stageBucket(stage: string): RunStageId | null {
  const s = String(stage).toLowerCase();
  if (s === "environment" || s === "env" || s === "install") return "environment";
  if (s === "dataset" || s === "import" || s === "prepare" || s === "source" || s === "load") return "dataset";
  if (
    s === "train" ||
    s === "features" ||
    s === "sequences" ||
    s === "validate" ||
    s === "validate_model" ||
    s === "verify" ||
    s === "export" ||
    s === "model"
  )
    return "train";
  return null;
}

/** Map a server event status onto a `.pv-step` modifier class (the display
 * word is translated at the call site; server status values stay verbatim). */
function stepClass(status: unknown): string {
  const s = String(status ?? "").toLowerCase();
  if (["done", "ok", "success", "complete", "completed", "finished"].includes(s)) return "is-done";
  if (["active", "start", "started", "progress", "running"].includes(s)) return "is-active";
  if (["failed", "fail", "error", "blocked", "rejected", "cancelled", "canceled"].includes(s)) return "is-failed";
  return "is-pending";
}

/** Latest event of a bucket (events are appended in arrival order). */
function latestForBucket(events: ProgressEvent[], bucket: RunStageId): ProgressEvent | null {
  let found: ProgressEvent | null = null;
  for (const e of events) if (stageBucket(String(e.stage)) === bucket) found = e;
  return found;
}

/** One log line: `[HH:MM:SS] stage — message`, built from event fields only. */
function logLine(e: ProgressEvent): string {
  const t = Date.parse(typeof e.ts === "string" ? e.ts : "");
  const clock = Number.isNaN(t) ? "--:--:--" : new Date(t).toTimeString().slice(0, 8);
  const msg = e.message ? ` — ${e.message}` : "";
  return `[${clock}] ${e.stage}${msg}`;
}

/**
 * BUG-4 guard: a terminal result renders only when the payload is a non-empty
 * object — the idle progress answer carries `result:{}` and null/{}/empty
 * must never produce a phantom "Run result" panel.
 */
function nonEmptyResult(r: unknown): Record<string, unknown> | null {
  if (r === null || typeof r !== "object" || Array.isArray(r)) return null;
  const o = r as Record<string, unknown>;
  return Object.keys(o).length > 0 ? o : null;
}

/** First non-empty scalar among candidates (payload facts only, no guesses). */
function firstScalar(...vals: unknown[]): string | null {
  for (const v of vals) {
    if (typeof v === "string" && v.trim() !== "") return v;
    if (typeof v === "number" || typeof v === "boolean") return String(v);
  }
  return null;
}

/** Local date text for a dataset row (empty string when unparseable). */
function dateText(iso: unknown): string {
  const t = typeof iso === "string" ? Date.parse(iso) : NaN;
  return Number.isNaN(t) ? "" : new Date(t).toLocaleDateString();
}

/** Where a failure came from — decides banner vs. in-card surface. */
type ErrorSource = "status" | "env" | "install" | "official" | "train" | "progress" | "cancel";

interface PageError {
  source: ErrorSource;
  message: string;
  code: string | null;
  detail: string | null;
  remedy: string | null;
}

/** Typed failure already resolved to a body (HTTP 200, {success:false,...}). */
function errFromResponse(source: ErrorSource, body: unknown): PageError {
  const b = body as { detail?: unknown; message?: unknown; remedy?: unknown };
  const code = codeOf(body);
  const detail = typeof b?.detail === "string" ? b.detail : typeof b?.message === "string" ? b.message : null;
  const remedy = typeof b?.remedy === "string" ? b.remedy : null;
  return { source, message: detail ?? code, code, detail, remedy };
}

/** Thrown transport failure — normalized through kit.errText, code kept if any. */
function errFromThrown(source: ErrorSource, err: unknown): PageError {
  const e = err as { code?: unknown; detail?: unknown; remedy?: unknown };
  const message = errText(err);
  return {
    source,
    message,
    code: typeof e?.code === "string" ? e.code : null,
    detail: typeof e?.detail === "string" && e.detail !== message ? e.detail : null,
    remedy: typeof e?.remedy === "string" ? e.remedy : null,
  };
}

/** True when the error carries server-typed fields that must render verbatim. */
function isTypedError(err: PageError | null): boolean {
  return Boolean(err && (err.code || err.detail || err.remedy));
}

/** code + detail(+message) + remedy lines, each VERBATIM from the payload. */
function ErrorFacts({ error }: { error: PageError }) {
  const body = error.detail ?? (error.message && error.message !== error.code ? error.message : null);
  return (
    <div>
      {error.code ? (
        <div>
          <span className="inline-mono font-bold">{error.code}</span>
        </div>
      ) : null}
      {body ? <div className="small">{body}</div> : null}
      {error.remedy ? <div className="small muted">{error.remedy}</div> : null}
    </div>
  );
}

/** Long value truncation (path/sha256) — full text stays on the title. */
const TRUNCATE: CSSProperties = {
  display: "block",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  maxWidth: "100%",
};

/**
 * One `.pv-fact` row (label / value / tone) — render inside a `<dl>`.
 * `tone` comes from slotTone(): null renders plain text with NO badge, so
 * descriptive values (path, sha256, detail) can never read as FAIL (BUG-2).
 */
function FactRow({
  label,
  value,
  tone = null,
  long = false,
}: {
  label: string;
  value: string;
  tone?: SlotTone | null;
  long?: boolean;
}) {
  const full = long ? value : undefined;
  return (
    <div className="pv-fact">
      <dt className="inline-mono small" title={full}>
        {label}
      </dt>
      <dd>
        {tone ? (
          <span className={classNames("badge", tone)} title={full}>
            {value}
          </span>
        ) : long ? (
          <span className="inline-mono small muted" title={full} style={TRUNCATE}>
            {value}
          </span>
        ) : (
          <span className="small muted" title={full}>
            {value}
          </span>
        )}
      </dd>
    </div>
  );
}

/** One row of the full environment checklist (`.pv-checks` dl). */
function CheckRow({ check, t }: { check: EnvCheck; t: TranslationFn }) {
  const codeLabel = check.code && check.code !== "OK" ? ` · ${check.code}` : "";
  return (
    <div className="kv-row">
      <dt className="inline-mono small">
        {check.stage}
        {codeLabel}
      </dt>
      <dd>
        <span className={classNames("badge", check.ok ? "good" : "bad")}>
          {t("provisioning.check.ok", "OK")}
        </span>
        {check.detail ? <span className="small muted">{check.detail}</span> : null}
        {!check.ok && check.remedy ? <span className="small">{check.remedy}</span> : null}
      </dd>
    </div>
  );
}

/** The ok===false subset of the report checklist (server `failing()`). */
function failingChecks(report: EnvironmentReport | undefined | null): EnvCheck[] {
  if (!report) return [];
  const raw = report.checks;
  return Array.isArray(raw) ? raw.filter((c) => !c.ok) : [];
}

/** Dataset browser state — `failed` is honest absence, never a fake list. */
type DsState =
  | { status: "loading" }
  | { status: "failed"; code: string | null; message: string | null }
  | { status: "ok"; roots: DatasetRoot[]; total: number | null };

export default function ProvisioningPage(_props: ShellPageProps) {
  const t = useI18n((s) => s.t);
  /** Stable handle for t() inside effects/callbacks (no re-subscription deps). */
  const tRef = useRef(t);
  tRef.current = t;

  const [status, setStatus] = useState<ProvisioningStatusResponse | null>(null);
  const [env, setEnv] = useState<ProvisioningEnvironmentResponse | null>(null);
  const [backend, setBackend] = useState<TrainBackend>("auto");
  const [source, setSource] = useState<TrainSource>("file");
  const [filePath, setFilePath] = useState<string>("");
  const [candles, setCandles] = useState<string>("10000");
  const [folds, setFolds] = useState<number>(6);
  const [epochs, setEpochs] = useState<number>(4);
  const [prepareEnvironment, setPrepareEnvironment] = useState<boolean>(false);
  const [installing, setInstalling] = useState<boolean>(false);
  const [installStart, setInstallStart] = useState<number | null>(null);
  const [officialBusy, setOfficialBusy] = useState<boolean>(false);
  const [officialStart, setOfficialStart] = useState<number | null>(null);
  const [training, setTraining] = useState<boolean>(false);
  const [runStart, setRunStart] = useState<number | null>(null);
  const [events, setEvents] = useState<ProgressEvent[]>([]);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<PageError | null>(null);
  const [notice, setNotice] = useState<string>("");
  const [ds, setDs] = useState<DsState>({ status: "loading" });
  const [dsReload, setDsReload] = useState<number>(0);
  const [now, setNow] = useState<number>(() => Date.now());
  /** `after=` cursor for train/progress (server slices the tail by INDEX). */
  const seenSeq = useRef<number>(0);
  const trainingRef = useRef<boolean>(false);
  const logRef = useRef<HTMLPreElement | null>(null);
  const stickRef = useRef<boolean>(true);

  const refreshStatus = useCallback(async () => {
    try {
      const s = await provisioningApi.status();
      setStatus(s);
    } catch (err) {
      setError(errFromThrown("status", err));
    }
  }, []);

  const refreshEnv = useCallback(async (be: TrainBackend) => {
    try {
      const e = await provisioningApi.environment(be);
      setEnv(e);
      setError(null);
    } catch (err) {
      setError(errFromThrown("env", err));
    }
  }, []);

  // Initial loads.
  useEffect(() => {
    void refreshStatus();
  }, [refreshStatus]);
  useEffect(() => {
    void refreshEnv(backend);
  }, [backend, refreshEnv]);

  // BUG-3 resume: probe the run on mount and adopt it when it is active.
  // An idle answer (active:false) is ignored — its result:{} must never be
  // adopted (BUG-4). Cleanup cancels adoption on unmount.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const p = await provisioningApi.trainProgress(0);
        if (cancelled || trainingRef.current || !p.active) return;
        const incoming = Array.isArray(p.events) ? p.events : [];
        if (incoming.length > 0) {
          setEvents(incoming);
          const maxSeq = incoming.reduce((m, e) => Math.max(m, Number(e.seq ?? 0)), 0);
          seenSeq.current = Math.max(incoming.length, maxSeq);
        }
        const firstTs = incoming[0] ? Date.parse(String(incoming[0].ts ?? "")) : NaN;
        setRunStart(Number.isNaN(firstTs) ? Date.now() : firstTs);
        setTraining(true);
        setNotice(
          tRef.current(
            "provisioning.banner.resumed",
            "Resumed the active training run — tailing its events.",
          ),
        );
      } catch (err) {
        if (!cancelled) setError(errFromThrown("progress", err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Keep the adoption guard in sync with the run state.
  useEffect(() => {
    trainingRef.current = training;
  }, [training]);

  // Live event tail while a run is active; stops cleanly on unmount and
  // skips its tick while the tab is hidden (contract §6 busy semantics).
  useEffect(() => {
    if (!training) return;
    let cancelled = false;
    const tick = async () => {
      if (document.hidden) return;
      try {
        const p = await provisioningApi.trainProgress(seenSeq.current);
        if (cancelled) return;
        const incoming = Array.isArray(p.events) ? p.events : [];
        if (incoming.length > 0) {
          setEvents((prev) => [...prev, ...incoming]);
          // Seq guard kept from the previous implementation (max seq wins);
          // the transport cursor also advances by the adopted event count
          // because the server slices `events[after:]` by index.
          const maxSeq = incoming.reduce((m, e) => Math.max(m, Number(e.seq ?? 0)), 0);
          seenSeq.current = Math.max(seenSeq.current + incoming.length, maxSeq);
        }
        if (!p.active) {
          setTraining(false);
          setResult(nonEmptyResult(p.result));
          if (p.cancelled)
            setNotice(
              tRef.current(
                "provisioning.notice.run_cancelled",
                "Run cancelled — observed at the epoch boundary.",
              ),
            );
          else if (!p.success) setError(errFromResponse("progress", p));
          else setNotice(tRef.current("provisioning.notice.run_finished", "Run finished."));
        }
      } catch (err) {
        if (!cancelled) setError(errFromThrown("progress", err));
      }
    };
    void tick();
    const id = window.setInterval(tick, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [training]);

  // Elapsed timers (install / official / run) — one 1s tick while any busy
  // flag is set; hidden tabs do not tick (their timers simply pause).
  useEffect(() => {
    if (!(installing || officialBusy || training)) return;
    const id = window.setInterval(() => {
      if (document.hidden) return;
      setNow(Date.now());
    }, TICK_MS);
    return () => window.clearInterval(id);
  }, [installing, officialBusy, training]);

  // GAP-6 dataset browser: read the allowed roots whenever the file source
  // is on screen. Failure never fabricates a list — an honest empty state.
  useEffect(() => {
    if (source !== "file") return;
    let cancelled = false;
    const ac = new AbortController();
    setDs({ status: "loading" });
    void (async () => {
      try {
        const r = await provisioningApi.datasets(ac.signal);
        if (cancelled) return;
        if (!r.success) {
          setDs({ status: "failed", code: r.code ?? null, message: r.message ?? null });
          return;
        }
        setDs({
          status: "ok",
          roots: Array.isArray(r.roots) ? r.roots : [],
          total: typeof r.total === "number" ? r.total : null,
        });
      } catch (err) {
        if (cancelled) return;
        const e = err as { code?: unknown };
        setDs({
          status: "failed",
          code: typeof e?.code === "string" ? e.code : null,
          message: errText(err),
        });
      }
    })();
    return () => {
      cancelled = true;
      ac.abort();
    };
  }, [source, dsReload]);

  // Event-log auto-scroll: follow the tail unless the operator scrolled up.
  const onLogScroll = useCallback(() => {
    const el = logRef.current;
    if (!el) return;
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
  }, []);
  useEffect(() => {
    if (!stickRef.current) return;
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [events]);

  const onInstall = useCallback(async () => {
    setInstalling(true);
    setInstallStart(Date.now());
    setError(null);
    setNotice("");
    try {
      const out = await provisioningApi.install({ backend });
      if (!out.success) {
        setError(errFromResponse("install", out));
      } else {
        setNotice(tRef.current("provisioning.notice.install_done", "Environment install finished."));
        await refreshEnv(backend);
      }
    } catch (err) {
      setError(errFromThrown("install", err));
    } finally {
      setInstalling(false);
    }
  }, [backend, refreshEnv]);

  const onOfficial = useCallback(async () => {
    setOfficialBusy(true);
    setOfficialStart(Date.now());
    setError(null);
    setNotice("");
    try {
      const out = await provisioningApi.official({});
      if (!out.success) {
        setError(errFromResponse("official", out));
      } else {
        setNotice(
          out.ok
            ? tRef.current("provisioning.notice.official_ready", "Official model installed and servable.")
            : tRef.current(
                "provisioning.notice.official_downloaded",
                "Official model downloaded; see the status slot.",
              ),
        );
        await refreshStatus();
      }
    } catch (err) {
      setError(errFromThrown("official", err));
    } finally {
      setOfficialBusy(false);
    }
  }, [refreshStatus]);

  const onTrain = useCallback(async () => {
    if (!formOk) return;
    setError(null);
    setNotice("");
    setEvents([]);
    setResult(null);
    setRunStart(Date.now());
    seenSeq.current = 0;
    const candleNum = candles ? Number(candles) : null;
    try {
      const out = await provisioningApi.trainStart({
        source,
        file: source === "file" ? filePath || null : null,
        candles: source === "broker" ? candleNum : null,
        folds,
        epochs,
        backend,
        prepare_environment: prepareEnvironment,
      });
      if (!out.success) {
        setError(errFromResponse("train", out));
        setRunStart(null);
        return;
      }
      setTraining(true);
      setNotice(tRef.current("provisioning.notice.train_started", "Training run started."));
    } catch (err) {
      setRunStart(null);
      setError(errFromThrown("train", err));
    }
    // Client-side validation gate runs BEFORE submit (see formOk below).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source, filePath, candles, folds, epochs, backend, prepareEnvironment]);

  const onCancel = useCallback(async () => {
    setError(null);
    try {
      const out = await provisioningApi.trainCancel();
      setNotice(
        out.cancel === "REQUESTED"
          ? tRef.current(
              "provisioning.notice.cancel_requested",
              "Cancel requested — observed at the next epoch boundary.",
            )
          : tRef.current("provisioning.notice.no_run", "No active run to cancel."),
      );
    } catch (err) {
      setError(errFromThrown("cancel", err));
    }
  }, []);

  const busy = installing || officialBusy || training;
  const busyReason = training
    ? t("provisioning.train.busy_reason", "a training run is active")
    : officialBusy
      ? t("provisioning.official.busy_reason", "the official download is running")
      : installing
        ? t("provisioning.env.busy_reason", "the environment install is running")
        : "";

  const report = env?.report;
  const failures = useMemo(() => failingChecks(report), [report]);
  const checks = Array.isArray(report?.checks) ? (report?.checks ?? []) : [];
  const trainingReady = Boolean(report?.training_ready);
  const firstFail = failures[0];
  const recommended = recommendedActionOf(status?.recommended);
  const allowedRoots = status?.allowed_import_roots ?? [];
  const slot = status?.slot;
  const provisionerEntries = status?.provisioner ? Object.entries(status.provisioner) : [];

  // Hero shield: model-slot truth (green servable / amber starter / red none).
  const shield = !slot
    ? {
        tone: "unknown",
        text: t("provisioning.hero.shield_pending", "slot status pending"),
        title: t(
          "provisioning.hero.shield_pending_title",
          "Model slot not read yet (status request pending).",
        ),
      }
    : slot.servable === true
      ? {
          tone: "good",
          text: t("provisioning.hero.shield_servable", "model servable"),
          title: t("provisioning.hero.shield_servable_title", "servable bundle: {path}", {
            path: String(slot.path ?? "path unknown"),
          }),
        }
      : slot.starter === true
        ? {
            tone: "warn",
            text: t("provisioning.hero.shield_starter", "DEV STARTER"),
            title: String(
              slot.detail ??
                t("provisioning.hero.shield_starter_title", "dev starter bundle in the slot"),
            ),
          }
        : {
            tone: "bad",
            text: t("provisioning.hero.shield_none", "no servable model"),
            title: String(
              slot.detail ?? t("provisioning.hero.shield_none_title", "no servable model in the slot"),
            ),
          };

  // Environment facts (legacy `environment` first, report dict as fallback).
  const notDetected = t("provisioning.env.not_detected", "not detected");
  const pyVersion = firstScalar(env?.environment?.python, report?.python?.version) ?? notDetected;
  const torchDetected = firstScalar(env?.environment?.torch, report?.pytorch?.detected) ?? notDetected;
  const torchRequired = firstScalar(report?.pytorch?.required);
  const gpuName = firstScalar(env?.environment?.gpu_name, report?.gpu?.name);
  const gpuDriver = firstScalar(report?.gpu?.driver_version);
  const gpuText = gpuName
    ? gpuDriver
      ? t("provisioning.env.gpu_driver", "{gpu} · driver {driver}", { gpu: gpuName, driver: gpuDriver })
      : gpuName
    : t("provisioning.env.no_gpu2", "no GPU detected");

  // Train-form client validation (mirrors the server's 1..1000 / 3000..100000).
  const foldsOk = Number.isFinite(folds) && folds >= FOLD_MIN && folds <= FOLD_MAX;
  const epochsOk = Number.isFinite(epochs) && epochs >= EPOCH_MIN && epochs <= EPOCH_MAX;
  const candleNum = candles ? Number(candles) : NaN;
  const candlesOk =
    source !== "broker" || (Number.isFinite(candleNum) && candleNum >= CANDLE_MIN && candleNum <= CANDLE_MAX);
  const fileOk = source !== "file" || filePath.trim() !== "";
  const formOk = foldsOk && epochsOk && candlesOk && fileOk;
  const formProblem = !foldsOk
    ? t(
        "provisioning.train.folds_problem_long",
        "Folds must be an integer {min}..{max} (the server refuses anything outside that range).",
        { min: FOLD_MIN, max: FOLD_MAX },
      )
    : !epochsOk
      ? t(
          "provisioning.train.epochs_problem_long",
          "Epochs must be an integer {min}..{max} (the server refuses anything outside that range).",
          { min: EPOCH_MIN, max: EPOCH_MAX },
        )
      : !candlesOk
        ? t("provisioning.train.candles_problem", "Broker candles must be within {min}..{max}.", {
            min: CANDLE_MIN,
            max: CANDLE_MAX,
          })
        : !fileOk
          ? t(
              "provisioning.train.file_problem",
              "Pick a dataset in the browser above, or type a path inside an allowed root.",
            )
          : "";

  // Stepper derivation: latest event per stage bucket.
  const stepRows = useMemo(
    () =>
      RUN_STAGES.map((st) => {
        const latest = latestForBucket(events, st);
        const status = latest?.status;
        const cls = stepClass(status);
        const toneWord =
          status === undefined || status === null
            ? t("provisioning.run.step_pending", "pending")
            : cls === "is-done"
              ? t("provisioning.run.step_done", "done")
              : cls === "is-active"
                ? t("provisioning.run.step_in_progress", "in progress")
                : String(status);
        const latestMsg = latest?.message ? String(latest.message) : "";
        return {
          id: st,
          label:
            st === "environment"
              ? t("provisioning.run.stage_environment", "Environment")
              : st === "dataset"
                ? t("provisioning.run.stage_dataset", "Dataset")
                : t("provisioning.run.stage_train", "Train"),
          cls,
          meta: latestMsg ? `${toneWord} — ${latestMsg}` : toneWord,
        };
      }),
    [events, t],
  );

  // Terminal result facts (BUG-4: `result` is already non-empty or null).
  const hasResult = result !== null && Object.keys(result).length > 0;
  const outcomeStr = firstScalar(result?.outcome);
  const reasonStr = firstScalar(result?.reason);
  const remedyStr = firstScalar(result?.remedy);
  const outcomeTone: SlotTone | null = outcomeStr
    ? /(fail|error|block|reject)/i.test(outcomeStr)
      ? "bad"
      : /(ok|success)/i.test(outcomeStr)
        ? "good"
        : null
    : null;
  const resultCallout =
    outcomeTone === "bad" ? "pv-callout bad" : outcomeTone === "good" ? "pv-callout ok" : "pv-callout";

  const dsFileCount = ds.status === "ok" ? ds.roots.reduce((n, r) => n + (r.files?.length ?? 0), 0) : 0;
  const dsTotal = ds.status === "ok" ? (ds.total ?? dsFileCount) : 0;

  const typedError = isTypedError(error);
  const officialError = typedError && error?.source === "official" ? error : null;

  return (
    <div className="pv-page">
      {/* ------------------------------------------------------------- hero */}
      <header className="pv-hero">
        <div className="pv-hero-main">
          <div className="pv-kicker">
            <span className="dot" aria-hidden="true" />
            {t("provisioning.hero.kicker", "MODEL PREPARATION")}
          </div>
          <h1 className="pv-title">
            <span className="glyph" aria-hidden="true">✦</span>
            <span className="word">{t("provisioning.hero.title", "Model Setup")}</span>
          </h1>
          <p className="pv-desc">
            {t(
              "provisioning.hero.desc_intro",
              "Prepare a servable model before the engine runs: probe the training stack (discovery only — installing is an explicit, consented action), download + verify the official bundle, or run a local training job with a live event tail. Reads",
            )}{" "}
            <span className="inline-mono">/api/provisioning/status</span>,{" "}
            <span className="inline-mono">/api/provisioning/environment</span>{" "}
            {t("provisioning.hero.desc_and", "and")}{" "}
            <span className="inline-mono">/api/provisioning/train/progress</span>
            {t("provisioning.hero.desc_posts", "; actions post to")}{" "}
            <span className="inline-mono">/api/provisioning/environment/install</span>,{" "}
            <span className="inline-mono">/api/provisioning/official</span>,{" "}
            <span className="inline-mono">/api/provisioning/train/start</span>{" "}
            {t("provisioning.hero.desc_and", "and")}{" "}
            <span className="inline-mono">/api/provisioning/train/cancel</span>.
          </p>
          <div className="pv-chips">
            {ENDPOINTS.map((e) => (
              <span key={e.path} className="pv-chip inline-mono" title={`${e.mode} ${e.path}`}>
                {e.mode} {e.path}
              </span>
            ))}
          </div>
        </div>
        <div className="pv-hero-side">
          <span
            className={classNames("pv-shield", shield.tone)}
            title={shield.title}
          >
            <span className="d" aria-hidden="true" />
            {shield.text}
          </span>
        </div>
      </header>

      {/* ---------------------------------------------------------- banners */}
      {error && !typedError ? <ErrorState message={error.message} onRetry={() => setError(null)} /> : null}
      {typedError && error && error.source !== "official" ? (
        <div className="banner bad pv-banner" role="alert">
          <ErrorFacts error={error} />
          <button
            type="button"
            className="pv-dismiss"
            aria-label={t("provisioning.banner.dismiss_error", "Dismiss error")}
            onClick={() => setError(null)}
          >
            ✕
          </button>
        </div>
      ) : null}
      {notice ? (
        <div className="banner good pv-banner" role="status">
          <span className="small">{notice}</span>
          <button
            type="button"
            className="pv-dismiss"
            aria-label={t("provisioning.banner.dismiss_notice", "Dismiss notice")}
            onClick={() => setNotice("")}
          >
            ✕
          </button>
        </div>
      ) : null}
      {recommended.action && recommended.action !== "none" ? (
        <div className="banner info pv-banner" role="status">
          <span className="small">
            {recommended.action === "download_official"
              ? t(
                  "provisioning.recommended.download_official",
                  "No servable model — download the official bundle below.",
                )
              : recommended.action === "train_local"
                ? t(
                    "provisioning.recommended.train_local",
                    "No servable model — train a local model below.",
                  )
                : recommended.action === "starter_offline"
                  ? t(
                      "provisioning.recommended.starter_offline",
                      "No servable model — run the offline starter, or train locally below.",
                    )
                  : recommended.action === "upgrade_from_starter"
                    ? t(
                        "provisioning.recommended.upgrade_from_starter",
                        "Running on the dev starter model — upgrade from starter below when ready.",
                      )
                    : t("provisioning.banner.recommended_copy", "Backend recommends: {action}.", {
                        action: String(recommended.action),
                      })}
          </span>
          {recommended.detail ? <span className="small muted"> {recommended.detail}</span> : null}
        </div>
      ) : null}

      <div className="pv-grid">
        {/* ------------------------------------------------ environment card */}
        <Panel
          title={t("provisioning.env.title", "Training Environment")}
          subtitle={
            report
              ? t("provisioning.env.subtitle", "resolved backend: {backend}", {
                  backend: String(report.backend ?? "unknown"),
                })
              : undefined
          }
          right={
            <Segmented
              options={TRAIN_BACKENDS.map((b) => ({ id: b, label: b.toUpperCase() }))}
              value={backend}
              onChange={setBackend}
            />
          }
        >
          <p className="small muted">
            {t("provisioning.env.requested", "Requested backend")}{" "}
            <span className="inline-mono">{backend}</span>{" "}
            {t(
              "provisioning.env.backend_discovery",
              "— discovery only, nothing is installed by this check.",
            )}
          </p>

          {!report ? <LoadingState label={t("provisioning.env.resolving", "Resolving environment…")} /> : null}

          {report && !trainingReady ? (
            <div className="pv-callout bad">
              <div className="head">
                {t(
                  "provisioning.env.blocked_title",
                  "Training blocked — environment not ready",
                )}
              </div>
              <div className="small">
                {firstScalar(firstFail?.remedy, firstFail?.detail, firstFail?.code) ??
                  t(
                    "provisioning.env.blocked_reason",
                    "training_ready is false — see the checklist below for the failing stage.",
                  )}
              </div>
              <div className="pv-toolbar">
                <button
                  type="button"
                  className="btn"
                  disabled={busy}
                  onClick={() => void onInstall()}
                  title={t(
                    "provisioning.env.install_title",
                    "Explicit opt-in: installs pinned python/torch for the chosen backend",
                  )}
                >
                  {installing
                    ? t("provisioning.env.installing2", "Installing training stack…")
                    : t("provisioning.env.install", "Install training stack")}
                </button>
                {installing && installStart !== null ? (
                  <span className="pv-elapsed">
                    {t("provisioning.env.elapsed", "elapsed {time}", {
                      time: fmtDuration(Math.max(0, now - installStart)),
                    })}
                  </span>
                ) : null}
              </div>
            </div>
          ) : null}

          {report ? (
            <>
              <div className="pv-meta">
                {t("provisioning.env.meta", "{checks} checks · {failures} failing · requested backend {backend}", {
                  checks: checks.length,
                  failures: failures.length,
                  backend,
                })}
              </div>
              <div className="pv-section">{t("provisioning.env.section_checklist", "Checklist")}</div>
              <dl className="pv-checks">
                {checks.map((c, i) => (
                  <CheckRow key={`${c.stage}-${c.code}-${i}`} check={c} t={t} />
                ))}
              </dl>

              <div className="pv-section">{t("provisioning.env.section_facts", "Facts")}</div>
              <dl className="pv-list">
                <FactRow label="python" value={pyVersion} />
                <FactRow
                  label="torch"
                  value={torchRequired ? `${torchDetected} (required ${torchRequired})` : torchDetected}
                />
                <FactRow label="gpu" value={gpuText} />
              </dl>
            </>
          ) : null}

          <div className="pv-toolbar">
            <button
              type="button"
              className="btn"
              disabled={busy}
              onClick={() => void onInstall()}
              title={t(
                "provisioning.env.install_title",
                "Explicit opt-in: installs pinned python/torch for the chosen backend",
              )}
            >
              {installing
                ? t("provisioning.env.installing2", "Installing training stack…")
                : t("provisioning.env.install", "Install training stack")}
            </button>
            {installing && installStart !== null ? (
              <span className="pv-elapsed">
                {t("provisioning.env.elapsed", "elapsed {time}", {
                  time: fmtDuration(Math.max(0, now - installStart)),
                })}
              </span>
            ) : null}
            {busy && !installing ? (
              <span className="small muted" aria-live="polite">
                {t("provisioning.env.disabled_busy", "Install disabled — {reason}.", { reason: busyReason })}
              </span>
            ) : (
              <span className="small muted">
                {t(
                  "provisioning.env.install_hint",
                  "Explicit opt-in (pinned variants). Training never auto-starts from here.",
                )}
              </span>
            )}
          </div>
        </Panel>

        {/* --------------------------------------------- official model card */}
        <Panel title={t("provisioning.official.title2", "Model Slot & Official Model")}>
          <p className="small muted">
            {t(
              "provisioning.official.intro2",
              "Download and verify the published model bundle. Nothing is installed unless every verification check passes. Slot facts below are toned per key — descriptive values (path, sha256, detail) are plain text, never a failure badge.",
            )}
          </p>

          {officialError ? (
            <div className="pv-callout bad">
              <div className="head">{t("provisioning.official.refused", "Official install refused")}</div>
              <ErrorFacts error={officialError} />
            </div>
          ) : null}

          {slot ? (
            <dl className="pv-list">
              {Object.entries(slot).map(([k, v]) => (
                <FactRow
                  key={k}
                  label={k}
                  value={fmtValue(v)}
                  tone={slotTone(k, v)}
                  long={k === "path" || k === "sha256"}
                />
              ))}
            </dl>
          ) : (
            <EmptyState
              message={t("provisioning.official.empty", "No slot state yet.")}
              hint={t(
                "provisioning.official.empty_hint2",
                "The recommended action appears as a banner once the status endpoint answers.",
              )}
            />
          )}

          {provisionerEntries.length > 0 ? (
            <>
              <div className="pv-section">
                {t("provisioning.official.section_provisioner", "Provisioner")}
              </div>
              <dl className="pv-list">
                {provisionerEntries.map(([k, v]) => (
                  <FactRow
                    key={k}
                    label={k}
                    value={fmtValue(v)}
                    tone={slotTone(k, v)}
                    long={k === "path" || k === "sha256"}
                  />
                ))}
              </dl>
            </>
          ) : null}

          <div className="pv-toolbar">
            <button
              type="button"
              className="btn"
              disabled={busy}
              onClick={() => void onOfficial()}
              title={t(
                "provisioning.official.btn_title",
                "POST /api/provisioning/official — download + verify the published bundle",
              )}
            >
              {officialBusy
                ? t("provisioning.official.downloading", "Downloading & verifying…")
                : t("provisioning.official.download", "Download & verify official model")}
            </button>
            {officialBusy && officialStart !== null ? (
              <span className="pv-elapsed">
                {t("provisioning.env.elapsed", "elapsed {time}", {
                  time: fmtDuration(Math.max(0, now - officialStart)),
                })}
              </span>
            ) : null}
            {busy && !officialBusy ? (
              <span className="small muted" aria-live="polite">
                {t("provisioning.official.disabled_busy", "Download disabled — {reason}.", {
                  reason: busyReason,
                })}
              </span>
            ) : null}
          </div>
        </Panel>
      </div>

      {/* ------------------------------------------------------- train card */}
      <Panel title={t("provisioning.train.title", "Local Training Run")}>
        <p className="small muted">
          {t(
            "provisioning.train.intro2",
            "Train a model from an imported file or from broker-borrowed history. Folds/epochs are validated here ({min}..{max}) and again by the server; broker source requires an explicit candle count ({cmin}..{cmax}).",
            { min: FOLD_MIN, max: FOLD_MAX, cmin: CANDLE_MIN, cmax: CANDLE_MAX },
          )}
        </p>

        <div className="pv-toolbar">
          <Segmented
            options={TRAIN_SOURCES.map((s) => ({
              id: s,
              label:
                s === "file"
                  ? t("provisioning.train.source_file", "Import file")
                  : t("provisioning.train.source_broker", "Broker history"),
            }))}
            value={source}
            onChange={setSource}
          />
          <Segmented
            options={TRAIN_BACKENDS.map((b) => ({ id: b, label: b.toUpperCase() }))}
            value={backend}
            onChange={setBackend}
          />
        </div>

        {source === "file" ? (
          <div className="pv-field">
            <label className="small muted" htmlFor="prov-file">
              {t(
                "provisioning.train.file_label",
                "Dataset file (CSV/Parquet) — must be inside an allowed import root",
              )}
            </label>
            <input
              id="prov-file"
              type="text"
              className="input pv-file"
              placeholder={t("provisioning.train.file_ph2", "e.g. C:/…/data/raw/XAUUSD_M1.csv")}
              value={filePath}
              onChange={(e) => setFilePath(e.target.value)}
              disabled={busy}
              aria-describedby={fileOk ? undefined : "prov-file-hint"}
            />
            {!fileOk ? (
              <span className="pv-hint is-warn" id="prov-file-hint" aria-live="polite">
                {t(
                  "provisioning.train.file_hint",
                  "Pick a dataset below, or type a path inside an allowed root.",
                )}
              </span>
            ) : null}

            {allowedRoots.length > 0 ? (
              <>
                <div className="small muted">
                  {t("provisioning.train.allowed_roots2", "Allowed import roots (server allowlist):")}
                </div>
                <div className="pv-chips">
                  {allowedRoots.map((r) => (
                    <span key={r} className="pv-chip inline-mono" title={r}>
                      {r}
                    </span>
                  ))}
                </div>
              </>
            ) : null}

            {/* GAP-6: dataset browser over /api/provisioning/datasets. */}
            <div className="pv-section">{t("provisioning.ds.section", "Dataset browser")}</div>
            {ds.status === "loading" ? (
              <LoadingState label={t("provisioning.ds.loading", "Reading allowed import roots…")} />
            ) : null}
            {ds.status === "failed" ? (
              <div className="pv-ds">
                <div className="pv-ds-empty" role="status">
                  {t(
                    "provisioning.ds.failed",
                    "Dataset browser unavailable (backend endpoint pending restart) — type a path below",
                  )}
                </div>
                {ds.code || ds.message ? (
                  <div className="pv-ds-meta">
                    {ds.code ? <span className="inline-mono">{ds.code}</span> : null}
                    {ds.code && ds.message ? " — " : null}
                    {ds.message ?? ""}
                  </div>
                ) : null}
                <div className="pv-toolbar">
                  <button type="button" className="btn small" onClick={() => setDsReload((n) => n + 1)}>
                    {t("provisioning.ds.retry", "Retry dataset listing")}
                  </button>
                </div>
              </div>
            ) : null}
            {ds.status === "ok" ? (
              ds.roots.length === 0 ? (
                <div className="pv-ds">
                  <div className="pv-ds-empty" role="status">
                    {t(
                      "provisioning.ds.no_roots",
                      "The backend reported no allowed import roots — type a path below.",
                    )}
                  </div>
                </div>
              ) : (
                <div className="pv-ds">
                  <div className="pv-meta">
                    {dsTotal === 1
                      ? t("provisioning.ds.count_one", "{n} dataset file across {roots} root", {
                          n: dsTotal,
                          roots: ds.roots.length,
                        })
                      : t("provisioning.ds.count_many", "{n} dataset files across {roots} roots", {
                          n: dsTotal,
                          roots: ds.roots.length,
                        })}
                  </div>
                  {ds.roots.map((r) => (
                    <Fragment key={r.root}>
                      <div className="pv-ds-meta" title={r.root}>
                        <span className="inline-mono">{r.root}</span>
                        {" · "}
                        {r.exists
                          ? t("provisioning.ds.root_listed", "{count} listed", {
                              count: typeof r.count === "number" ? r.count : r.files.length,
                            })
                          : t("provisioning.ds.root_missing", "root does not exist on this host")}
                      </div>
                      {!r.exists ? (
                        <div className="pv-ds-empty">
                          {t(
                            "provisioning.ds.root_missing_hint",
                            "Root does not exist on this host — create it or use another root.",
                          )}
                        </div>
                      ) : r.files.length === 0 ? (
                        <div className="pv-ds-empty">
                          {t("provisioning.ds.root_empty", "No .csv/.parquet files under this root.")}
                        </div>
                      ) : (
                        <div className="pv-ds-list">
                          {r.files.map((f) => (
                            <button
                              key={f.path}
                              type="button"
                              className={classNames("pv-ds-item", filePath.trim() === f.path && "is-sel")}
                              title={f.path}
                              disabled={busy}
                              onClick={() => setFilePath(f.path)}
                            >
                              <span>{f.name}</span>
                              <span className="pv-ds-meta">
                                {fmtBytes(f.size_bytes)}
                                {dateText(f.modified_iso) ? ` · ${dateText(f.modified_iso)}` : ""}
                              </span>
                            </button>
                          ))}
                        </div>
                      )}
                    </Fragment>
                  ))}
                </div>
              )
            ) : null}
          </div>
        ) : (
          <div className="pv-field">
            <div className="small muted" id="prov-candles-label">
              {t("provisioning.train.candles_label", "Candles (most recent N bars — chronological tail)")}
            </div>
            <Segmented options={CANDLE_OPTIONS} value={candles} onChange={setCandles} />
            <p className="small muted">
              {t(
                "provisioning.train.broker_note",
                "Broker source borrows the most recent N bars from the connected broker — nothing is uploaded; the {min}..{max} range is checked here and enforced again server-side.",
                { min: CANDLE_MIN, max: CANDLE_MAX },
              )}
            </p>
            {!candlesOk ? (
              <span className="pv-hint is-bad" id="prov-candles-hint" aria-live="polite">
                {t("provisioning.train.candles_problem", "Broker candles must be within {min}..{max}.", {
                  min: CANDLE_MIN,
                  max: CANDLE_MAX,
                })}
              </span>
            ) : null}
          </div>
        )}

        <div className="pv-toolbar end">
          <label className="small muted" htmlFor="prov-folds">
            {t("provisioning.train.folds", "Folds")}
            <input
              id="prov-folds"
              type="number"
              className="input pv-num"
              min={FOLD_MIN}
              max={FOLD_MAX}
              value={folds}
              onChange={(e) => setFolds(Number(e.target.value))}
              disabled={busy}
              aria-describedby={foldsOk ? undefined : "prov-folds-hint"}
            />
          </label>
          {!foldsOk ? (
            <span className="pv-hint is-bad" id="prov-folds-hint" aria-live="polite">
              {t("provisioning.train.folds_problem", "Folds must be {min}..{max}.", { min: FOLD_MIN, max: FOLD_MAX })}
            </span>
          ) : null}
          <label className="small muted" htmlFor="prov-epochs">
            {t("provisioning.train.epochs", "Epochs")}
            <input
              id="prov-epochs"
              type="number"
              className="input pv-num"
              min={EPOCH_MIN}
              max={EPOCH_MAX}
              value={epochs}
              onChange={(e) => setEpochs(Number(e.target.value))}
              disabled={busy}
              aria-describedby={epochsOk ? undefined : "prov-epochs-hint"}
            />
          </label>
          {!epochsOk ? (
            <span className="pv-hint is-bad" id="prov-epochs-hint" aria-live="polite">
              {t("provisioning.train.epochs_problem", "Epochs must be {min}..{max}.", {
                min: EPOCH_MIN,
                max: EPOCH_MAX,
              })}
            </span>
          ) : null}
          <label className="pv-consent" htmlFor="prov-consent">
            <input
              id="prov-consent"
              type="checkbox"
              checked={prepareEnvironment}
              onChange={(e) => setPrepareEnvironment(e.target.checked)}
              disabled={busy}
            />
            {t(
              "provisioning.train.consent",
              "Prepare environment if not ready (explicit consent)",
            )}
          </label>
        </div>

        <div className="pv-toolbar">
          <button
            type="button"
            className="btn primary"
            disabled={busy || !formOk}
            title={formOk ? "POST /api/provisioning/train/start" : formProblem}
            onClick={() => void onTrain()}
          >
            {training
              ? t("provisioning.train.training", "Training…")
              : t("provisioning.train.start", "Start training run")}
          </button>
          <button type="button" className="btn" disabled={!training} onClick={() => void onCancel()}>
            {t("provisioning.train.cancel", "Cancel run")}
          </button>
          {!formOk ? (
            <span className="pv-hint is-bad" aria-live="polite">
              {formProblem}
            </span>
          ) : busy && training ? (
            <span className="small muted" aria-live="polite">
              {t("provisioning.train.disabled_active", "Start disabled — a training run is active.")}
            </span>
          ) : busy ? (
            <span className="small muted" aria-live="polite">
              {t("provisioning.train.disabled_busy", "Start disabled — {reason}.", { reason: busyReason })}
            </span>
          ) : (
            <span className="small muted">
              {t(
                "provisioning.train.cancel_note",
                "Cancel is observed at the next epoch boundary, not instantly.",
              )}
            </span>
          )}
        </div>

        {/* ------------------------------------------- run lifecycle (§6.5) */}
        {training || events.length > 0 || hasResult ? (
          <>
            <div className="pv-section">{t("provisioning.run.section", "Run lifecycle")}</div>
            <ol className="pv-stepper">
              {stepRows.map((s) => (
                <li key={s.id} className={classNames("pv-step", s.cls)}>
                  <span className="pv-step-dot" aria-hidden="true" />
                  <span className="pv-step-label">{s.label}</span>
                  <span className="pv-step-meta">{s.meta}</span>
                </li>
              ))}
            </ol>
            <div className="pv-meta">
              {runStart !== null ? (
                <span className="pv-elapsed">
                  {t("provisioning.env.elapsed", "elapsed {time}", {
                    time: fmtDuration(Math.max(0, now - runStart)),
                  })}
                </span>
              ) : (
                <span className="pv-elapsed">{t("provisioning.run.elapsed_none", "elapsed —")}</span>
              )}
              {training
                ? ` · ${t("provisioning.run.active", "run active")}`
                : events.length > 0
                  ? ` · ${t("provisioning.run.idle", "run idle")}`
                  : ""}
            </div>

            {events.length > 0 ? (
              <pre tabIndex={0} className="pv-log" aria-live="polite" ref={logRef} onScroll={onLogScroll}>
                {events.map(logLine).join("\n")}
              </pre>
            ) : null}

            {hasResult && result ? (
              <div className={resultCallout}>
                <div className="head">{t("provisioning.train.result", "Run result")}</div>
                {outcomeStr || reasonStr || remedyStr ? (
                  <dl className="pv-list">
                    {outcomeStr ? <FactRow label="outcome" value={outcomeStr} tone={outcomeTone} /> : null}
                    {reasonStr ? <FactRow label="reason" value={reasonStr} /> : null}
                    {remedyStr ? <FactRow label="remedy" value={remedyStr} /> : null}
                  </dl>
                ) : null}
                <details>
                  <summary className="small muted">
                    {t("provisioning.run.raw_json", "Raw result JSON")}
                  </summary>
                  <pre tabIndex={0} className="pv-json">{JSON.stringify(result, null, 2)}</pre>
                </details>
              </div>
            ) : null}
          </>
        ) : null}
      </Panel>
    </div>
  );
}
