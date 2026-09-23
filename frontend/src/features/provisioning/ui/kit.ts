/**
 * features/provisioning/ui/kit.ts — shared display helpers for the
 * First-Run Model Preparation screen (house pattern: feature kit module).
 * Pure formatters / class-name joiners — no React, no fetch.
 */

/** Join truthy class fragments. */
export function classNames(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

/** Candle-count segmented options (label = short form, value = bars). */
export const CANDLE_OPTIONS = [
  { id: "3000", label: "3k" },
  { id: "10000", label: "10k" },
  { id: "30000", label: "30k" },
  { id: "100000", label: "100k" },
];

/** Render an error: the backend `detail` string when present, else the typed code. */
export function errText(err: unknown): string {
  const e = err as { detail?: unknown; message?: string; code?: string };
  if (e?.detail) return String(e.detail);
  if (e?.message) return e.message;
  if (e?.code) return e.code;
  return String(err);
}

/** Pull the typed failure code out of a resolved backend body. */
export function codeOf(body: unknown): string {
  const b = body as { code?: string; message?: string; error?: { code?: string; message?: string } };
  const nested = b?.error;
  return String((nested?.code ?? b?.code ?? b?.message ?? "UNKNOWN_ERROR"));
}

/** Server slot value reads as OK (booleans / OK strings). */
export function isOkValue(v: unknown): boolean {
  if (typeof v === "boolean") return v;
  if (v === null || v === undefined) return false;
  return String(v).toUpperCase() === "OK";
}

/** Render any slot value as display text. */
export function fmtValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

/**
 * Normalize the status `recommended` payload (producer:
 * FirstRunCoordinator.recommended_action() — an OBJECT of
 * {action, reason?, official_configured?, slot?, environment?}; legacy or
 * mocked payloads may carry a bare string). NEVER render the raw value with
 * String() — that produced the literal "[object Object]" on the live page
 * (wave BUG-1). `action` is "" when absent; `detail` is the producer's own
 * reason/remedy text (verbatim) or null.
 */
export function recommendedActionOf(rec: unknown): { action: string; detail: string | null } {
  if (rec !== null && typeof rec === "object") {
    const r = rec as Record<string, unknown>;
    const action = typeof r.action === "string" ? r.action : "";
    const raw = r.reason ?? r.remedy ?? r.message ?? null;
    return { action, detail: raw === null ? null : String(raw) };
  }
  if (typeof rec === "string" && rec.trim() !== "") return { action: rec.trim(), detail: null };
  return { action: "", detail: null };
}

/** Tone vocabulary shared by slot facts / badges (null = no badge, plain text). */
export type SlotTone = "good" | "bad" | "warn" | "unknown";

/**
 * Key-aware truth mapping for one model-slot fact. Returns null when the
 * value is descriptive text (path, sha256, free-text detail) — render those
 * plainly with NO badge; otherwise a badge tone. Truth is PER-KEY: the old
 * value-only mapper read `state:"ready"` and `origin:"UNKNOWN"` as FAIL and
 * painted healthy facts red (wave BUG-2). Keys outside this table fall back
 * to the generic OK/FAIL vocabulary; anything else stays plain text.
 */
export function slotTone(key: string, value: unknown): SlotTone | null {
  if (value === null || value === undefined) return null;
  const k = key.toLowerCase();
  if (typeof value === "boolean") {
    if (k === "starter") return value ? "warn" : "good";
    return value ? "good" : "bad";
  }
  const s = String(value).toLowerCase();
  if (k === "state") {
    if (s === "ready") return "good";
    if (s === "empty" || s === "missing" || s === "unknown") return "warn";
    if (s === "corrupt" || s === "rejected" || s === "blocked") return "bad";
    return "unknown";
  }
  if (k === "origin") {
    if (s === "official") return "good";
    if (s === "starter" || s === "unknown") return "warn";
    return "unknown";
  }
  if (s === "ok") return "good";
  if (s === "fail" || s === "failed" || s === "error") return "bad";
  return null;
}

/** Human byte size (B/KB/MB/GB — one decimal above B). */
export function fmtBytes(n: number): string {
  if (!Number.isFinite(n) || n < 0) return "—";
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v >= 10 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}

/** Elapsed-time text for busy timers: "0s" / "43s" / "2m 05s" / "1h 04m". */
export function fmtDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "—";
  const total = Math.floor(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  if (m > 0) return `${m}m ${String(s).padStart(2, "0")}s`;
  return `${s}s`;
}
