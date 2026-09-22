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
