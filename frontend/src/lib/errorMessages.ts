/**
 * Backend error-code → localized message resolver (i18n rules §31, §32, §70).
 *
 * WHY THE FRONTEND OWNS THIS: the backend has TWO code registries
 * (web/errors.py ERROR_CODES — 9 codes; web/api_v1/common.py _ERROR_MESSAGES —
 * 11 codes) plus ~110 route-local codes with NO registry and NO sentence.
 * Worse, some leak sites merge raw str(exc) into the safe envelope's `message`
 * (web/command_center_integration.py, via safe_error_payload extra=). The
 * backend `message` field is therefore NOT a safe display source. Per the
 * backend-error audit recommendation, the frontend maps machine-readable
 * CODES to localized messages and never displays backend prose it does not
 * recognize.
 *
 * CONTRACT
 *  - known code   → localized human message (lib/errors/i18n.ts)
 *  - unknown code → safe generic localized message + the code as a MONOSPACE
 *    token + the request id as "reference" — never raw exception text, never
 *    an untranslated backend sentence, never SHOUTING_SNAKE alone
 *  - status-only failures (no code in the body) fall back to the HTTP map
 *
 * KEY NAMING: errors.<snake_case_code> — the backend code verbatim as the
 * final segment, so a code and its message stay obvious twins. Shared codes
 * between the two registries intentionally share one key.
 *
 * MESSAGES live in lib/errors/i18n.ts (registered in lib/i18nMessages.ts).
 * The English source sits IN each entry because this resolver is the only
 * call site: t() looks up the active locale first and falls back to en, so
 * one table is the single source of truth for all five locales.
 */
import { useI18n } from "@/stores/i18nStore";

/** Resolve a backend error code (or HTTP status) to a LOCALIZED message.
 *  Use this wherever an ApiError is surfaced to the user (toast, banner,
 *  inline error state) INSTEAD of ApiError.message, which may carry raw
 *  backend prose or exception text. Unknown codes never reach the user raw. */
export function localErrorMessage(
  code: string | null | undefined,
  status: number | null | undefined,
  requestId: string | null | undefined,
  t: (key: string, fallback: string, vars?: Record<string, string | number>) => string,
): string {
  // 1. known code → localized message (both registries share keys by design)
  if (code) {
    const key = `errors.${code.toLowerCase()}`;
    const known = t(key, ""); // "" sentinel: scope hit ⇒ non-empty
    if (known) return known;
  }
  // 2. status-only failure (no code in the body)
  if (status !== null && status !== undefined) {
    const skey = `errors.http_${status}`;
    const byStatus = t(skey, "");
    if (byStatus) return byStatus;
  }
  // 3. mandatory unknown-code fallback: generic message + code + reference
  if (code) {
    return t(
      "errors.unknown_reference",
      "Unknown error (code: {code}) — reference: {requestId}",
      { code, requestId: requestId || "—" },
    );
  }
  return t("errors.unknown", "Unknown error");
}

/** React hook form: resolves with the current language's t(). */
export function useLocalErrorMessage(): (
  code: string | null | undefined,
  status: number | null | undefined,
  requestId?: string | null | undefined,
) => string {
  const t = useI18n((s) => s.t);
  return (code, status, requestId) => localErrorMessage(code, status, requestId, t);
}
